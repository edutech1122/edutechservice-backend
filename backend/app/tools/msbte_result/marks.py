"""
Mark parsing and per-head analytics -- ported from the excel-generation
prototype (`build_pharmacy_sample.py`), verified against the user's own
worked sample numbers for the Diploma in Pharmacy course (see
MSBTE_Result_Analysis_Architecture.md addenda for the verification notes).
"""
import re

INTERNAL_MAX = 20   # not printed in the gazette; inferred from data (see study note)
EXTERNAL_MAX = 80
HEAD_MAX = 100

MARK_RE = re.compile(r'^(\d{3}|AB|OPT|DIS|CPS)([#*@]?)$')


def parse_mark(v):
    """Return (value:int|None, failed:bool, applicable:bool)."""
    if v is None:
        return None, False, False
    m = MARK_RE.match(v)
    if not m:
        return None, False, False
    tok, sym = m.groups()
    if tok == 'OPT':
        return None, False, False
    if tok == 'AB':
        return 0, True, True
    if tok in ('DIS', 'CPS'):
        return 0, True, True
    return int(tok), sym == '*', True


def build_block(pages: list[dict], course_code: str, exam_for: str, consider_repeaters: bool) -> dict | None:
    """Combines every page for one (course, exam_for) block into a single
    {'cols', 'students', 'scheme', 'exam_for'} structure, matching the shape
    the excel builder expects. `consider_repeaters=False` (the recommended
    default -- see architecture doc) drops students whose status is 'X'.

    IMPORTANT: exam_for strings (e.g. "SECOND SEMESTER (K)") are NOT unique
    across courses -- a real multi-course gazette has many courses sharing
    the same stage/scheme label. Filtering by exam_for alone would silently
    merge unrelated courses' students (and, since page-local column index
    `n` isn't stable across courses either, crash with a KeyError on the
    mismatched column layout). Always filter by course_code too."""
    blk_pages = [pg for pg in pages if pg['course_code'] == course_code and pg['exam_for'] == exam_for]
    if not blk_pages:
        return None
    cols = blk_pages[0]['cols']
    scheme_match = re.search(r'\((\w)\)', exam_for)
    scheme = scheme_match.group(1) if scheme_match else '?'
    students = []
    for pg in blk_pages:
        for s in pg['students']:
            if 'total' not in s:
                continue
            if not consider_repeaters and s.get('status', '').upper() == 'X':
                continue
            row = {
                'seat': s['seat'], 'enroll': s['enroll'], 'name': s['name'],
                'status': s['status'], 'result': s.get('result'), 'total_printed': s['total'],
            }
            heads = {}
            for c in cols:
                m = s['marks'][c['n']]
                ext, ext_fail, ext_ok = parse_mark(m['r1'])
                inn, inn_fail, inn_ok = parse_mark(m['r2'])
                tot, tot_fail, tot_ok = parse_mark(m['r3'])
                heads[c['n']] = {
                    'subject': c['subject'], 'head': c['head'],
                    'ext': ext, 'inn': inn, 'tot': tot,
                    'failed': tot_fail or ext_fail or inn_fail, 'applicable': ext_ok,
                }
            row['heads'] = heads
            students.append(row)
    return {'cols': cols, 'students': students, 'scheme': scheme, 'exam_for': exam_for}


def analytics(students: list[dict], cols: list[dict], component: str) -> dict:
    """component: 'ext' | 'inn' | 'tot'. Pass/fail per component is 40% of
    that component's own maximum -- verified against real sample data (see
    architecture doc). Returns per-head stats keyed by column number."""
    maxval = {'ext': EXTERNAL_MAX, 'inn': INTERNAL_MAX, 'tot': HEAD_MAX}[component]
    threshold = 0.4 * maxval
    out = {}
    for c in cols:
        n = c['n']
        vals = [s['heads'][n][component] for s in students if s['heads'][n]['applicable'] and s['heads'][n][component] is not None]
        passed = [s for s in students if s['heads'][n]['applicable'] and s['heads'][n][component] is not None and s['heads'][n][component] >= threshold]
        appeared = [s for s in students if s['heads'][n]['applicable']]
        above60 = [v for v in vals if v >= 0.6 * maxval]
        total_secured = sum(vals) if vals else 0
        score_index = round(total_secured / (len(appeared) * maxval) * 100) if appeared else 0
        out[n] = {
            'low': min(vals) if vals else 0,
            'high': max(vals) if vals else 0,
            'appeared': len(appeared),
            'passed': len(passed),
            'pct_passed': round(len(passed) / len(appeared) * 100) if appeared else 0,
            'pct_above60': round(len(above60) / len(appeared) * 100) if appeared else 0,
            'total_secured': total_secured,
            'score_index': score_index,
        }
    return out
