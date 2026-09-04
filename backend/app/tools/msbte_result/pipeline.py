"""
Orchestration: gazette bytes -> course catalogue (for the picker UI), and
gazette bytes + course selection + customer options -> a finished workbook.
"""
import io

from .gazette_parser import parse_gazette
from .course_index import build_course_index, select_stages, exam_for_for
from .marks import build_block
from .excel_builder import (
    Workbook, write_sheet, build_result_analysis_sheet, build_j12_sheet,
    build_notes_sheet, compute_academic_year,
)

MAX_GAZETTE_PAGES = 900  # a full-diploma, multi-course gazette can run 600+ pages


def list_courses(pdf_bytes: bytes) -> dict:
    """Returns {courses: {code: {...}}, session, institute_name, institute_code}
    for the course-selection UI. Raises ValueError on an unreadable PDF."""
    pages = parse_gazette(pdf_bytes, max_pages=MAX_GAZETTE_PAGES)
    index = build_course_index(pages)
    first = pages[0]
    return {
        'courses': index,
        'session': first['session'],
        'institute_name': first.get('institute_name'),
        'institute_code': first.get('institute_code'),
    }


class CourseNotFoundError(ValueError):
    pass


def generate_workbook(pdf_bytes: bytes, course_code: str, options: dict) -> tuple[bytes, int, dict]:
    """options keys (all optional unless noted):
      scheme: str | None                  -- override the default (latest) scheme
      consider_repeaters: bool = False    -- include status='X' students
      consider_all_semesters: bool = False -- bypass the Summer/Winter parity filter
      institute_name, coordinator_name, coordinator_role, principal_name: str
      student_limit: int | None           -- cap total students across all stages (free tier)

    Returns (xlsx_bytes, total_student_count, meta) where meta carries the
    resolved scheme/pattern/session/academic_year for the caller to persist
    on the Job row.
    """
    pages = parse_gazette(pdf_bytes, max_pages=MAX_GAZETTE_PAGES)
    index = build_course_index(pages)
    course = index.get(course_code.upper())
    if course is None:
        raise CourseNotFoundError(f"Course '{course_code}' was not found in this gazette.")

    scheme = (options.get('scheme') or course['default_scheme'])
    consider_repeaters = bool(options.get('consider_repeaters', False))
    consider_all_semesters = bool(options.get('consider_all_semesters', False))
    student_limit = options.get('student_limit')

    stages = select_stages(course, scheme, consider_all_semesters)
    if not stages:
        raise ValueError(
            f"No pages match course {course_code} under scheme '{scheme}' with the current filters."
        )

    sections = []
    for stage in stages:
        exam_for = exam_for_for(stage, scheme)
        block = build_block(pages, course_code.upper(), exam_for, consider_repeaters)
        if block and block['students']:
            sections.append((stage['stage_label'].title(), block))

    if not sections:
        raise ValueError(f"No student records found for course {course_code} under scheme '{scheme}'.")

    total_students = sum(len(blk['students']) for _, blk in sections)

    if student_limit is not None and total_students > student_limit:
        remaining = student_limit
        trimmed = []
        for label, blk in sections:
            if remaining <= 0:
                break
            if len(blk['students']) > remaining:
                blk = dict(blk)
                blk['students'] = blk['students'][:remaining]
            trimmed.append((label, blk))
            remaining -= len(blk['students'])
        sections = trimmed
        total_students = sum(len(blk['students']) for _, blk in sections)

    session_text = course['session'].title()
    academic_year = compute_academic_year(course['session'])
    pattern_label = course['pattern'].upper()
    institute_name = options.get('institute_name') or course.get('institute_name') or ''
    coordinator_name = options.get('coordinator_name') or ''
    coordinator_role = options.get('coordinator_role') or 'Exam/Academic co-ordinator'
    principal_name = options.get('principal_name') or ''

    wb = Workbook()
    wb.remove(wb.active)

    notes = [
        f'MSBTE Result Analysis -- {course["course_name"]} ({course_code}) -- generated workbook',
        '',
        f'Source session: {session_text}. Institute: {institute_name or "(not provided)"}.',
        f'Scheme analysed: {scheme}. Pattern: {pattern_label}.',
        f'Repeater (status X) students: {"included" if consider_repeaters else "excluded (default)"}.',
        f'Semester filter: {"all semesters shown" if consider_all_semesters else "Summer/Winter parity rule applied"}.',
        '',
        'Assumptions (see MSBTE_Result_Analysis_Architecture.md for full detail):',
        '  - Internal component maximum = 20 marks, External component maximum = 80 marks, per head.',
        "  - Pass/Fail per component is 40% of that component's own maximum.",
        '  - J11 "Annual" row = head Total (external+internal) stats; "Sessional" row = Internal stats.',
        '  - J12 "TH" = head External-component score index (not Total); "TM" = Internal-component score index.',
        '  - Course Code / subject-code columns are blank/editable unless a code master was supplied.',
        '  - "FINAL SEMESTER" is treated as semester 6 of a standard 6-semester diploma for the parity rule.',
        '  - Values are computed in Python and written as plain numbers, not Excel formulas.',
    ]
    build_notes_sheet(wb, notes)

    for label, block in sections:
        write_sheet(wb, f'{label} Internal', block, 'inn')
        write_sheet(wb, f'{label} External', block, 'ext')
        write_sheet(wb, f'{label} Total', block, 'tot', with_rank_cols=True)

    build_result_analysis_sheet(
        wb, course_code, sections, pattern_label=pattern_label,
        institute_name=institute_name, session_text=session_text,
        academic_year=academic_year, coordinator_name=coordinator_name,
        coordinator_role=coordinator_role, principal_name=principal_name,
    )
    build_j12_sheet(
        wb, course_code, sections,
        institute_name=institute_name, session_text=session_text,
        academic_year=academic_year, coordinator_name=coordinator_name,
        coordinator_role=coordinator_role, principal_name=principal_name,
    )

    buf = io.BytesIO()
    wb.save(buf)
    meta = {
        'scheme': scheme, 'pattern': pattern_label, 'session': session_text,
        'academic_year': academic_year, 'course_name': course['course_name'],
        'stages_included': [label for label, _ in sections],
    }
    return buf.getvalue(), total_students, meta
