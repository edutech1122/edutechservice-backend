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
import re
import collections
import logging

import pdfplumber

logger = logging.getLogger("msbte_gazette_parser")

MARK_RE = re.compile(r'^(\d{3}|AB|OPT|DIS|CPS)([#*@]?)$')


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


def parse_page(page) -> dict | None:
    """Returns the parsed structure for one page, or None if this page
    doesn't look like a gazette result-sheet page at all (e.g. a cover page)
    -- callers should skip Nones rather than treat them as errors."""
    words = page.extract_words(x_tolerance=1.5, y_tolerance=2)
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
        if l['text'].startswith('SEAT NO.'):
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
        sm = re.match(r'^(\d{6}) (\d{10,11}) (.+?) ([A-Z]) ([A-Z])(?: ([SW]\d\d) (\d+))?$', t)
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
        'page': page.page_number, 'exam_for': exam_for, 'session': session,
        'course_code': course_code, 'course_name': course_name,
        'institute_code': inst_code, 'institute_name': inst_name,
        'cols': cols, 'students': students,
    }


def parse_gazette(pdf_bytes: bytes, max_pages: int | None = None) -> list[dict]:
    """Parses every page of the gazette. Pages that don't look like a
    result-sheet page (blank separators, cover pages) are silently skipped.
    Raises on a genuinely unreadable/corrupted PDF -- callers should catch
    and surface a clear error, same as the photo_signature_extractor tool
    does for its own PDFs."""
    pages: list[dict] = []
    with pdfplumber.open(pdf_bytes if hasattr(pdf_bytes, "read") else __import__("io").BytesIO(pdf_bytes)) as pdf:
        total = len(pdf.pages)
        if max_pages is not None and total > max_pages:
            raise ValueError(f"This gazette has {total} pages, more than the {max_pages}-page limit.")
        for page in pdf.pages:
            try:
                parsed = parse_page(page)
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
    return pages
