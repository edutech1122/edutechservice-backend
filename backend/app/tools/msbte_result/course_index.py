"""
Builds the course catalogue from parsed gazette pages, and implements the
customer-facing selection rules confirmed in MSBTE_Result_Analysis_Architecture.md:

- Annual vs semester pattern detection (an "exam_for" containing "YEAR" is
  annual; containing "SEMESTER" is a semester course).
- Scheme selection: default to the alphabetically-latest scheme letter
  present for the course (K > J > I), overridable by the customer.
- Summer -> even semesters only, Winter -> odd semesters only (annual
  courses are unaffected -- every year is included). ASSUMPTION: "FINAL
  SEMESTER" is treated as the 6th (even) semester of a standard 3-year/
  6-semester diploma -- flagged in the Notes sheet since MSBTE's own
  gazette text doesn't spell out the numeral for it.
"""
import re
from collections import defaultdict

SEMESTER_ORDINAL = {
    'FIRST': 1, 'SECOND': 2, 'THIRD': 3, 'FOURTH': 4, 'FIFTH': 5, 'SIXTH': 6,
    'SEVENTH': 7, 'EIGHTH': 8,
}
FINAL_SEMESTER_ORDINAL = 6  # see module docstring


def _scheme_of(exam_for: str) -> str | None:
    m = re.search(r'\((\w)\)', exam_for)
    return m.group(1) if m else None


def _stage_label(exam_for: str) -> str:
    """The part of exam_for identifying WHICH year/semester this is,
    without the scheme suffix -- e.g. 'FIRST YEAR (J)' -> 'FIRST YEAR',
    'FINAL SEMESTER (K)' -> 'FINAL SEMESTER'. Two blocks with the same
    stage label but different schemes are the "multiple schemes for this
    stage" case the scheme tickmark resolves."""
    return re.sub(r'\s*\(\w\)\s*$', '', exam_for).strip()


def _semester_number(stage_label: str) -> int | None:
    if 'SEMESTER' not in stage_label.upper():
        return None
    word = stage_label.upper().replace('SEMESTER', '').strip()
    if word == 'FINAL':
        return FINAL_SEMESTER_ORDINAL
    return SEMESTER_ORDINAL.get(word)


def detect_pattern(exam_fors: list[str]) -> str:
    """'annual' if any block is a *-YEAR block, else 'semester'."""
    for ef in exam_fors:
        if 'YEAR' in ef.upper():
            return 'annual'
    return 'semester'


def build_course_index(pages: list[dict]) -> dict:
    """Returns {course_code: {course_name, pattern, schemes: [...],
    default_scheme, session, stages: [{stage_label, semester_number,
    schemes_available: [...]}]}}."""
    by_course: dict[str, list[dict]] = defaultdict(list)
    for pg in pages:
        by_course[pg['course_code']].append(pg)

    index = {}
    for code, pgs in by_course.items():
        course_name = pgs[0]['course_name']
        session = pgs[0]['session']
        exam_fors = sorted({pg['exam_for'] for pg in pgs})
        pattern = detect_pattern(exam_fors)
        schemes = sorted({_scheme_of(ef) for ef in exam_fors if _scheme_of(ef)})
        default_scheme = max(schemes) if schemes else None

        stages_map: dict[str, set[str]] = defaultdict(set)
        for ef in exam_fors:
            stages_map[_stage_label(ef)].add(_scheme_of(ef) or '?')
        stages = []
        for stage_label, scheme_set in stages_map.items():
            stages.append({
                'stage_label': stage_label,
                'semester_number': _semester_number(stage_label),
                'schemes_available': sorted(scheme_set),
            })
        stages.sort(key=lambda s: (s['semester_number'] is None, s['semester_number'] or 0, s['stage_label']))

        index[code] = {
            'course_code': code,
            'course_name': course_name,
            'session': session,
            'pattern': pattern,
            'schemes': schemes,
            'default_scheme': default_scheme,
            'stages': stages,
            'institute_code': pgs[0].get('institute_code'),
            'institute_name': pgs[0].get('institute_name'),
        }
    return index


def session_parity(session: str) -> str | None:
    """'even' for a Summer exam, 'odd' for Winter -- None if the session
    text names neither (in which case no semester filtering is applied)."""
    s = session.upper()
    if 'SUMMER' in s:
        return 'even'
    if 'WINTER' in s:
        return 'odd'
    return None


def select_stages(course: dict, scheme: str | None, consider_all_semesters: bool = False) -> list[dict]:
    """Applies the scheme choice and the Summer/Winter semester-parity rule,
    returning the ordered list of stage dicts to actually include in the
    analysis. `consider_all_semesters=True` bypasses the parity filter
    (useful for an annual course, where it's a no-op anyway, or if the
    customer explicitly wants every semester shown)."""
    scheme = scheme or course['default_scheme']
    parity = None if (course['pattern'] == 'annual' or consider_all_semesters) else session_parity(course['session'])

    selected = []
    for stage in course['stages']:
        if scheme not in stage['schemes_available']:
            continue  # this stage has no block under the chosen scheme
        if parity and stage['semester_number'] is not None:
            is_even = stage['semester_number'] % 2 == 0
            if parity == 'even' and not is_even:
                continue
            if parity == 'odd' and is_even:
                continue
        selected.append(stage)
    return selected


def exam_for_for(stage: dict, scheme: str) -> str:
    return f"{stage['stage_label']} ({scheme})"
