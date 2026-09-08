"""
Coordinate-based MSBTE Result Gazette PDF parser.

Ported (near-verbatim) from the study-phase prototype (`proto_parse.py`),
which was built and verified against a real 613-page Diploma gazette
(Institute 00013, Govt. Polytechnic Ratnagiri, Summer 2026) -- see
MSBTE_Result_Analysis_Study.md in the project docs for the full format
study this was reverse-engineered from.

Each page of the gazette prints, in order: an institute/course/exam-for/
session header, a block of subject-header lines (index / subject
abbreviation / head type / max / min), a "SEAT NO." table header, then one
student per record with 1-3 mark rows per subject head (External /
Internal / Total) and a result line.

This module returns one dict per page (see `parse_page`) -- callers combine
pages across a course/exam-for block themselves (see course_index.py).
"""
import io
import re
import collections
import logging

import pdfplumber

from . import ocr_fallback

logger = logging.getLogger("msbte_gazette_parser")

MARK_RE = re.compile(r'^(\d{3}|AB|OPT|DIS|CPS)([#*@]?)$')

# Student-line regex needs SEAT NO (6 digits) and ENROLL NO (10-11 digits)
# to already be pure digit strings before it will match at all -- see
# _fix_student_line_digits, which repairs OCR digit-confusions in just
# those two positions (never in the name) before the match is attempted.
STUDENT_LINE_RE = re.compile(
    r'^(\d{6}) (\d{10,11}) (.+?) ([A-Z]) ([A-Z])(?: ([SW]\d\d) (\d+))?$'
)


def _words_look_garbled(words) -> bool:
    """True if the extracted text is mostly Private-Use-Area codepoints
    (U+E000-U+F8FF) -- the signature of a PDF whose embedded font has no
    ToUnicode mapping, so normal text extraction returns meaningless glyph
    codes instead of real characters (see ocr_fallback.py for the fix)."""
    chars = ''.join(w['text'] for w in words)
    if not chars:
        return False
    pua = sum(1 for ch in chars if 0xE000 <= ord(ch) <= 0xF8FF)
    return pua / len(chars) > 0.3


def _group_lines(words, ytol=3):
    words = sorted(words, key=lambda w: (w['top'], w['x0']))
    lines = []
    for w in words:
        if lines and abs(lines[-1]['top'] - w['top']) <= ytol:
            lines[-1]['words'].append(w)
        else:
            lines.append({'top': w['top'], 'words': [w]})
    for l in lines:
        l['words'].sort(key=lambda w: w['x0'])
        l['text'] = ' '.join(w['text'] for w in l['words'])
    return lines


def _fix_student_line_digits(t: str) -> str:
    """Repairs OCR digit-confusions (O/0, l/1, S/5...) in just the seat-no
    and enroll-no tokens at the start of a student line, leaving the name
    and everything after it untouched -- those two tokens are positionally
    guaranteed to be pure digits, so a correction there can't be wrong the
    way blindly fixing 'digit-like' letters in a name could be (a real
    student can be named SOHAM or OMKAR)."""
    parts = t.split(' ', 2)
    if len(parts) < 3:
        return t
    seat, enroll, rest = parts
    return f"{ocr_fallback.fix_digit_confusions(seat)} {ocr_fallback.fix_digit_confusions(enroll)} {rest}"


def parse_page(page) -> dict | None:
    """Returns the parsed structure for one page, or None if this page
    doesn't look like a gazette result-sheet page at all (e.g. a cover page)
    -- callers should skip Nones rather than treat them as errors."""
    words = page.extract_words(x_tolerance=1.5, y_tolerance=2)
    if not words:
        return None
    return _parse_words(words, page.page_number)


def _parse_words(words, page_number) -> dict | None:
    """Core parser, operating on a word list shaped like pdfplumber's
    page.extract_words() (dicts with text/x0/x1/top/bottom) -- used both
    for pdfplumber's own extraction and for the OCR fallback's word list
    (see ocr_fallback.py), which is why this doesn't take a `page` object
    directly."""
    if not words:
        return None
    lines = _group_lines(words)
    txt = [l['text'] for l in lines]
    full = '\n'.join(txt)

    m = re.search(r'RESULT SHEET FOR THE (.+?) EXAMINATION HELD IN (.+?) \(', full)
    if not m:
        return None
    exam_for, session = m.group(1), m.group(2)

    m = re.search(r'COURSE\s*:\s*(\w\w)\s*-\s*(.+)', full)
    if not m:
        return None
    course_code, course_name = m.group(1), m.group(2).strip()

    m = re.search(r'INSTITUTE\s*:\s*(\d+)\s*-\s*(.+?)\s*COURSE', full)
    inst_code, inst_name = (m.group(1), m.group(2)) if m else (None, None)

    # header blocks: sequences of 5 lines: index, subj, type, max, min
    cols = []
    i = 0
    seat_line_idx = None
    while i < len(lines):
        l = lines[i]
        if l['text'].startswith('SEAT'):
            # Loosened from 'SEAT NO.' -- OCR-sourced lines (see
            # ocr_fallback.py) can split this header row at a slightly
            # different point than pdfplumber's exact per-line coordinates
            # would (confirmed on a real page: "NO. ... " and "SEAT NAME
            # ..." came back as two separate lines instead of one "SEAT
            # NO. ... NAME ..." line). Any real gazette page has exactly
            # one line starting with "SEAT" -- this table header -- so the
            # looser check is safe for pdfplumber-sourced lines too.
            seat_line_idx = i
            break
        ws = l['words']
        if all(re.fullmatch(r'\d{1,2}', w['text']) for w in ws) and i + 4 < len(lines):
            idx = ws
            subj = lines[i + 1]['words']
            typ = lines[i + 2]['words']
            mx = lines[i + 3]['words']
            mn = lines[i + 4]['words']
            if len(idx) == len(subj) == len(typ) == len(mx) == len(mn):
                block = max([c['block'] for c in cols], default=-1) + 1
                for a, b, c, d, e in zip(idx, subj, typ, mx, mn):
                    try:
                        cols.append({
                            'n': int(a['text']), 'subject': b['text'], 'head': c['text'],
                            'max': int(d['text']), 'min': int(e['text']),
                            'x1': d['x1'], 'block': block,
                        })
                    except ValueError:
                        pass
                i += 5
                continue
        i += 1

    if seat_line_idx is None or not cols:
        return None

    students = []
    j = seat_line_idx + 1
    cur = None
    blocks_by_id = collections.defaultdict(list)
    for c in cols:
        blocks_by_id[c['block']].append(c)

    while j < len(lines):
        l = lines[j]
        t = l['text']
        sm = STUDENT_LINE_RE.match(t)
        if not sm:
            # Retry with seat-no/enroll-no digit-confusion correction (a
            # no-op for pdfplumber-sourced words, since those never
            # misread digits as letters in the first place -- only
            # matters for the OCR fallback path).
            sm = STUDENT_LINE_RE.match(_fix_student_line_digits(t))
        if sm:
            cur = {
                'seat': sm.group(1), 'enroll': sm.group(2), 'name': sm.group(3),
                'status': sm.group(4), 'app_code': sm.group(5), 'rows': [],
            }
            students.append(cur)
            j += 1
            continue
        if t.startswith('Result Date') or (' of ' in t and 'Page ' in t):
            break
        if cur is None:
            j += 1
            continue
        tm = re.search(r'Total\s*:\s*(\d+)\s*Result\s*:\s*(.+?)(?:\s*TCALSE:(\d+)\s*/\s*Credits:(\d+))?$', t)
        if tm:
            cur['total'] = int(tm.group(1))
            cur['result'] = tm.group(2).strip()
            j += 1
            continue
        toks = [(w, MARK_RE.match(w['text'])) for w in l['words']]
        if toks and not all(mm for _, mm in toks):
            # Retry with digit-confusion correction, and if that resolves
            # it, use the corrected text going forward (both for the match
            # itself and for the value stored in marks{} below) -- a no-op
            # for pdfplumber-sourced words.
            fixed = [(w, ocr_fallback.fix_digit_confusions(w['text'])) for w in l['words']]
            fixed_toks = [(w, MARK_RE.match(t2)) for w, t2 in fixed]
            if all(mm for _, mm in fixed_toks):
                for w, t2 in fixed:
                    w['text'] = t2
                toks = fixed_toks
            else:
                # A real mark row still failing after digit-confusion repair
                # is usually one or two OCR-garbled cells among otherwise
                # clean ones (confirmed on the D. Pharma gazette: e.g. one
                # "049%" among nine valid "###" tokens) -- not proof the
                # whole line isn't a mark row. Requiring every token to
                # validate before keeping ANY of them threw away good data
                # alongside bad. Instead, keep only the tokens that do
                # validate (after the same digit-fix retry) and drop the
                # rest -- those specific cells come back as None in the
                # marks dict below rather than corrupting a neighbor's data
                # or discarding the whole row. Still requires a majority to
                # validate, so a line that isn't a mark row at all (mostly
                # non-numeric) doesn't get misfiled as one.
                good = []
                for w, mm in toks:
                    if mm:
                        good.append(w)
                        continue
                    t2 = ocr_fallback.fix_digit_confusions(w['text'])
                    if MARK_RE.match(t2):
                        w['text'] = t2
                        good.append(w)
                if len(good) >= (len(toks) + 1) // 2:
                    l = {**l, 'words': good}
                    toks = [(w, True) for w in good]
                else:
                    toks = []
        if toks and all(mm for _, mm in toks):
            cur['rows'].append(l)
        j += 1

    for s in students:
        marks = {c['n']: {'r1': None, 'r2': None, 'r3': None} for c in cols}
        rows = sorted(s['rows'], key=lambda r: r['top'])
        groups = []
        for r in rows:
            if groups and r['top'] - groups[-1][-1]['top'] < 20:
                groups[-1].append(r)
            else:
                groups.append([r])
        for bi, g in enumerate(groups):
            bcols = blocks_by_id.get(bi, [])
            if not bcols:
                continue
            for ri, r in enumerate(g[:3]):
                for w in r['words']:
                    xc = w['x1'] - (4 if w['text'][-1] in '#*@' else 0)
                    c = min(bcols, key=lambda c: abs(c['x1'] - xc))
                    if abs(c['x1'] - xc) <= 15:
                        marks[c['n']]['r%d' % (ri + 1)] = w['text']
        s['marks'] = marks
        del s['rows']

    return {
        'page': page_number, 'exam_for': exam_for, 'session': session,
        'course_code': course_code, 'course_name': course_name,
        'institute_code': inst_code, 'institute_name': inst_name,
        'cols': cols, 'students': students,
    }


def parse_gazette(pdf_bytes: bytes, max_pages: int | None = None) -> list[dict]:
    """Parses every page of the gazette. Pages that don't look like a
    result-sheet page (blank separators, cover pages) are silently skipped.
    A page whose embedded font has no ToUnicode mapping (pdfplumber reads
    it as meaningless Private-Use-Area codepoints -- see
    _words_look_garbled) is retried via OCR (ocr_fallback.py) before being
    given up on. Raises on a genuinely unreadable/corrupted PDF -- callers
    should catch and surface a clear error, same as the
    photo_signature_extractor tool does for its own PDFs."""
    raw = pdf_bytes.read() if hasattr(pdf_bytes, "read") else pdf_bytes
    pages: list[dict] = []
    ocr_pages_used = 0
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        total = len(pdf.pages)
        if max_pages is not None and total > max_pages:
            raise ValueError(f"This gazette has {total} pages, more than the {max_pages}-page limit.")
        for page_index, page in enumerate(pdf.pages):
            try:
                words = page.extract_words(x_tolerance=1.5, y_tolerance=2)
                if words and _words_look_garbled(words):
                    logger.info(
                        "Gazette page %s: embedded font has no usable Unicode mapping -- "
                        "falling back to OCR.", page.page_number,
                    )
                    words = ocr_fallback.extract_words_via_ocr(raw, page_index)
                    ocr_pages_used += 1
                parsed = _parse_words(words, page.page_number)
            except Exception:
                logger.exception("Failed to parse gazette page %s -- skipping.", page.page_number)
                parsed = None
            if parsed:
                pages.append(parsed)
    if not pages:
        raise ValueError(
            "No result-sheet pages could be read from this PDF. It may not be an MSBTE "
            "result gazette, or its layout differs from the one this tool was built against."
        )
    if ocr_pages_used:
        logger.info(
            "Gazette parsed with %d/%d page(s) read via OCR fallback (scrambled font).",
            ocr_pages_used, len(pages),
        )
    return pages
