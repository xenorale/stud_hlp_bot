from __future__ import annotations

import csv
import html
import io
import re
import time
import warnings
import xml.etree.ElementTree as ET
import zipfile



import requests
import urllib3
from dataclasses import dataclass
from typing import Optional

SHEET_ID = "1Jod7MWr5SsinEyP778UV2JxFaeFuk-5otC8z8XV-juY"
SHEET_GID = "2090530136"
EXPORT_URL = (
    f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
    f"/export?format=csv&gid={SHEET_GID}"
)
XLSX_URL = (
    f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
    f"/export?format=xlsx&gid={SHEET_GID}"
)

_MERGE_CACHE: dict = {"merges": None, "ts": 0}
_MERGE_TTL = 86400  # 24 часа

DAYS_ORDER = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"]
_TIME_RE = re.compile(r"^\d{1,2}:\d{2}")
_COURSE_RE = re.compile(r"^(\d+)\s+курс$")
_GROUP_RE = re.compile(r"^(\d+)\s+группа$")
_TEACHER_RE = re.compile(
    r"(доц\.|ст\.преп\.|преп\.|асс\.|проф\.)\s+(.+?)(?:\s+(\d+\S*))?\s*$"
)


@dataclass
class Lesson:
    day: str
    time: str
    sub1_num: str   # подгруппа 1, числитель
    sub1_den: str   # подгруппа 1, знаменатель
    sub2_num: str   # подгруппа 2, числитель
    sub2_den: str   # подгруппа 2, знаменатель

    def get(self, subgroup: int, week: str) -> str:
        """Возвращает предмет для подгруппы (1/2/0) и недели (num/den)."""
        if subgroup == 1:
            return self.sub1_num if week == "num" else self.sub1_den
        if subgroup == 2:
            return self.sub2_num if week == "num" else self.sub2_den
        # 0 = для внешних запросов возвращаем sub1 (общий случай)
        return self.sub1_num if week == "num" else self.sub1_den

    def has_subgroup_diff(self, week: str) -> bool:
        """True если подгруппы отличаются на данной неделе."""
        if week == "num":
            return self.sub1_num.strip() != self.sub2_num.strip()
        return self.sub1_den.strip() != self.sub2_den.strip()

    def has_week_diff(self, subgroup: int) -> bool:
        """True если числитель и знаменатель отличаются для подгруппы."""
        if subgroup == 1:
            return self.sub1_num.strip() != self.sub1_den.strip()
        if subgroup == 2:
            return self.sub2_num.strip() != self.sub2_den.strip()
        return False


def _col_to_idx(col_str: str) -> int:
    """'A'->0, 'BU'->72"""
    result = 0
    for c in col_str:
        result = result * 26 + (ord(c) - 64)
    return result - 1


def _parse_cell_ref(ref: str) -> tuple[int, int]:
    """'BU60' -> (row=59, col=72) 0-indexed"""
    m = re.match(r"([A-Z]+)(\d+)", ref)
    return int(m.group(2)) - 1, _col_to_idx(m.group(1))


def fetch_merge_ranges() -> list[tuple[int, int, int, int]]:
    """Скачивает xlsx и возвращает список (min_row, max_row, min_col, max_col) 0-indexed."""
    now = time.time()
    if _MERGE_CACHE["merges"] is not None and now - _MERGE_CACHE["ts"] < _MERGE_TTL:
        return _MERGE_CACHE["merges"]

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)
        resp = requests.get(XLSX_URL, verify=False, timeout=30)
    resp.raise_for_status()
    xlsx_data = resp.content

    with zipfile.ZipFile(io.BytesIO(xlsx_data)) as zf:
        sheet_xml = zf.read("xl/worksheets/sheet1.xml")

    root = ET.fromstring(sheet_xml)
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    merges: list[tuple[int, int, int, int]] = []
    mc_elem = root.find(f"{{{ns}}}mergeCells")
    if mc_elem is not None:
        for mc in mc_elem:
            ref = mc.get("ref", "")
            if ":" not in ref:
                continue
            a, b = ref.split(":")
            r1, c1 = _parse_cell_ref(a)
            r2, c2 = _parse_cell_ref(b)
            merges.append((min(r1, r2), max(r1, r2), min(c1, c2), max(c1, c2)))

    _MERGE_CACHE["merges"] = merges
    _MERGE_CACHE["ts"] = now
    return merges


def _same_merge(row: int, col_a: int, col_b: int,
                merges: list[tuple[int, int, int, int]]) -> bool:
    """True если (row, col_a) и (row, col_b) входят в одну объединённую область."""
    for min_r, max_r, min_c, max_c in merges:
        if min_r <= row <= max_r and min_c <= col_a <= max_c and min_c <= col_b <= max_c:
            return True
    return False


def fetch_sheet_rows() -> list[list[str]]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)
        resp = requests.get(EXPORT_URL, verify=False, timeout=30)
    resp.raise_for_status()
    text = resp.content.decode("utf-8")
    return list(csv.reader(io.StringIO(text)))


def _get_course_ranges(rows: list[list[str]]) -> dict[int, tuple[int, int]]:
    """
    Возвращает {номер_курса: (start_col, end_col)}.
    Использует ПЕРВОЕ вхождение "N курс" (заголовок повторяется для каждого направления).
    """
    row0 = rows[0]
    first_pos: dict[int, int] = {}
    for i, cell in enumerate(row0):
        m = _COURSE_RE.match(cell.strip())
        if m:
            n = int(m.group(1))
            if n not in first_pos:
                first_pos[n] = i

    sorted_courses = sorted(first_pos.items())
    ranges: dict[int, tuple[int, int]] = {}
    for idx, (course, start) in enumerate(sorted_courses):
        end = sorted_courses[idx + 1][1] - 1 if idx + 1 < len(sorted_courses) else len(row0) - 1
        ranges[course] = (start, end)
    return ranges


def get_available_groups(rows: list[list[str]]) -> dict[int, list[int]]:
    """Возвращает {номер_курса: [номера_групп]} из заголовков таблицы."""
    course_ranges = _get_course_ranges(rows)
    row1 = rows[1]
    result: dict[int, list[int]] = {}
    for course, (start, end) in sorted(course_ranges.items()):
        groups = []
        for i in range(start, min(end + 1, len(row1))):
            m = _GROUP_RE.match(row1[i].strip())
            if m:
                groups.append(int(m.group(1)))
        if groups:
            result[course] = sorted(groups)
    return result


def _find_subsection_start(rows: list[list[str]], col: int, course_num: int) -> int:
    """
    Возвращает начальную колонку подсекции (потока), в которой находится col.
    В таблице один курс может делиться на несколько потоков — каждый начинается
    с повторного заголовка 'N курс' в row[0].
    """
    row0 = rows[0]
    starts = [
        i for i, cell in enumerate(row0)
        if _COURSE_RE.match(cell.strip()) and int(_COURSE_RE.match(cell.strip()).group(1)) == course_num
        and i <= col
    ]
    return max(starts) if starts else col


def _find_group_col(rows: list[list[str]], group_num: int, course_num: int) -> Optional[int]:
    """Ищет колонку 'N группа' внутри диапазона нужного курса."""
    course_ranges = _get_course_ranges(rows)
    if course_num not in course_ranges:
        return None
    start, end = course_ranges[course_num]
    row1 = rows[1]
    label = f"{group_num} группа"
    for i in range(start, min(end + 1, len(row1))):
        if row1[i].strip() == label:
            return i
    return None


def parse_group_schedule(group_num: int, course_num: int) -> list[Lesson]:
    """
    Скачивает расписание и парсит пары для (курс, группа).

    Структура таблицы:
      - 2 колонки на группу: [подгруппа 1, подгруппа 2]
      - 2 строки на временной слот: строка с временем = числитель,
        следующая строка без времени = знаменатель
    """
    rows = fetch_sheet_rows()

    col = _find_group_col(rows, group_num, course_num)
    if col is None:
        raise ValueError(
            f"Группа {group_num} не найдена на {course_num} курсе. "
            "Проверь номер курса и группы в профиле."
        )

    row1 = rows[1]
    has_second_col = col + 1 < len(row1) and not row1[col + 1].strip()
    col2 = col + 1 if has_second_col else col

    # Начало подсекции (потока) для данной группы
    subsec_start = _find_subsection_start(rows, col, course_num)
    merges = fetch_merge_ranges()

    lessons: list[Lesson] = []
    current_day = ""
    i = 2

    while i < len(rows):
        row = rows[i]
        if not row:
            i += 1
            continue

        day_cell = row[0].strip()
        if day_cell in DAYS_ORDER:
            current_day = day_cell

        if not current_day:
            i += 1
            continue

        time_cell = row[1].strip() if len(row) > 1 else ""
        if not _TIME_RE.match(time_cell):
            i += 1
            continue

        # Числитель
        sub1_num = row[col].strip() if col < len(row) else ""
        sub2_num = row[col2].strip() if col2 < len(row) else ""

        # Потоковая пара: ячейка слита с началом подсекции — проверяем через merge info
        if not sub1_num and not sub2_num and subsec_start < col:
            potok = row[subsec_start].strip() if subsec_start < len(row) else ""
            if potok and _same_merge(i, subsec_start, col, merges):
                sub1_num = potok
                sub2_num = potok

        # Знаменатель — следующая строка без времени и без нового дня
        sub1_den = ""
        sub2_den = ""
        if i + 1 < len(rows):
            nrow = rows[i + 1]
            n_day = nrow[0].strip() if nrow else ""
            n_time = nrow[1].strip() if len(nrow) > 1 else ""
            if not _TIME_RE.match(n_time) and n_day not in DAYS_ORDER:
                sub1_den = nrow[col].strip() if col < len(nrow) else ""
                sub2_den = nrow[col2].strip() if col2 < len(nrow) else ""
                # Потоковый знаменатель
                if not sub1_den and not sub2_den and subsec_start < col:
                    potok_den = nrow[subsec_start].strip() if subsec_start < len(nrow) else ""
                    if potok_den and _same_merge(i + 1, subsec_start, col, merges):
                        sub1_den = potok_den
                        sub2_den = potok_den
                i += 1  # пропускаем строку знаменателя

        # Если знаменатель пустой — копируем числитель (пара каждую неделю)
        if not sub1_den:
            sub1_den = sub1_num
        if not sub2_den:
            sub2_den = sub2_num

        if sub1_num or sub1_den or sub2_num or sub2_den:
            lessons.append(Lesson(
                day=current_day,
                time=time_cell,
                sub1_num=sub1_num,
                sub1_den=sub1_den,
                sub2_num=sub2_num,
                sub2_den=sub2_den,
            ))

        i += 1

    return lessons


def has_subgroups(lessons: list[Lesson]) -> bool:
    """Проверяет, есть ли разбивка по подгруппам (хотя бы одна пара отличается)."""
    return any(
        l.sub1_num != l.sub2_num or l.sub1_den != l.sub2_den
        for l in lessons
    )


_ID_RE = re.compile(r"\s*\(id=\d+\)\s*")
_DO_RE = re.compile(r"\(ДО\s*\)", re.IGNORECASE)


def _format_subject(subject: str) -> str:
    """Форматирует строку предмета в красивый HTML."""
    if not subject:
        return ""

    is_online = bool(_DO_RE.search(subject))
    mode_icon = "💻" if is_online else "🏫"

    # Убираем (id=...) и (ДО), схлопываем пробелы
    clean = _ID_RE.sub(" ", subject)
    clean = _DO_RE.sub("", clean)
    clean = re.sub(r"\s+", " ", clean).strip()

    m = _TEACHER_RE.search(clean)
    if m:
        subj_name = re.sub(r"\s+", " ", clean[:m.start()]).strip()
        teacher = f"{m.group(1)} {m.group(2).strip()}"
        room = m.group(3).strip() if m.group(3) else ""
        teacher_line = f"👤 {html.escape(teacher)}"
        if room and not is_online:
            teacher_line += f" · ауд. {html.escape(room)}"
        return f"{mode_icon} <b>{html.escape(subj_name)}</b>\n{teacher_line}"

    return f"{mode_icon} <b>{html.escape(clean)}</b>"


def format_lesson_for_subgroup(lesson: Lesson, subgroup_num: int, week: str) -> str:
    """
    Форматирует пару с учётом подгруппы и недели.
    subgroup_num: 0 = вся группа, 1 = 1-я подгруппа, 2 = 2-я подгруппа.
    week: 'num' = числитель, 'den' = знаменатель.
    """
    sub1 = lesson.sub1_num if week == "num" else lesson.sub1_den
    sub2 = lesson.sub2_num if week == "num" else lesson.sub2_den

    if subgroup_num == 1:
        return _format_subject(sub1)
    if subgroup_num == 2:
        return _format_subject(sub2)

    # subgroup_num == 0: показываем обе, если они отличаются
    if sub1 == sub2:
        return _format_subject(sub1)
    parts = []
    if sub1:
        parts.append(f"<i>1 подгр.:</i>\n{_format_subject(sub1)}")
    if sub2:
        parts.append(f"<i>2 подгр.:</i>\n{_format_subject(sub2)}")
    return "\n".join(parts)


def group_by_day(lessons: list[Lesson]) -> dict[str, list[Lesson]]:
    result: dict[str, list[Lesson]] = {}
    for lesson in lessons:
        result.setdefault(lesson.day, []).append(lesson)
    return result
