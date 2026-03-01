import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import List, Optional
from .brs_parser import BrsRow, fetch_and_parse_brs
from .calculators import get_attendance_penalty


def format_brs_subject(row: BrsRow) -> str:
    """Форматирует один предмет из БРС в красивый текст"""

    # Аттестации
    att_list = []
    for att in [row.att1, row.att2, row.att3]:
        if att is not None:
            att_list.append(f"{att}")
    att_str = " / ".join(att_list) if att_list else "—"

    # Посещаемость с взвешенным баллом
    att_pct_str = f"{row.attendance_pct}%" if row.attendance_pct else "—"

    # Интерпретируем weighted score
    penalty_info = get_attendance_penalty(row.weighted_score)
    weighted_display = (
        f"{row.weighted_score} {penalty_info['grade']}"
        if row.weighted_score is not None
        else "—"
    )

    # Итоговый результат
    final_score_str = (
        f"{row.final_score} ({row.final_text})"
        if row.final_score is not None
        else row.final_text or "—"
    )

    return (
        f"📚 **{row.subject}**\n"
        f"   Семестр {row.semester} | {row.control}\n"
        f"   Преподаватель: {row.teacher_short}\n"
        f"   Аттестации: {att_str}\n"
        f"   Посещаемость: {att_pct_str}\n"
        f"   Взвешенный балл: {weighted_display}\n"
        f"   Экзамен: {row.exam_score if row.exam_score else '—'}\n"
        f"   Итог: {final_score_str}"
    )


def get_brs_data(
        username: str,
        password: str,
        student_id: str,
) -> Optional[List[BrsRow]]:
    """
    Получает данные из БРС с обработкой ошибок.

    Возвращает список предметов или None при ошибке.
    """
    try:
        rows = fetch_and_parse_brs(student_id, username, password)
        return rows if rows else None
    except Exception as e:
        print(f"❌ Ошибка при получении данных БРС: {e}")
        return None


def group_by_semester(rows: List[BrsRow]) -> dict:
    """Группирует предметы по семестрам"""
    grouped = {}
    for row in rows:
        sem = row.semester
        if sem not in grouped:
            grouped[sem] = []
        grouped[sem].append(row)
    return grouped


def get_semester_summary(rows: List[BrsRow]) -> str:
    """Создает сводку по всем предметам в семестре"""
    if not rows:
        return "Нет данных"

    lines = []
    weighted_scores = [r.weighted_score for r in rows if r.weighted_score is not None]

    if weighted_scores:
        avg_weighted = sum(weighted_scores) / len(weighted_scores)
        lines.append(f"📊 Средний взвешенный балл: {avg_weighted:.2f}")

    for row in rows:
        penalty_info = get_attendance_penalty(row.weighted_score)
        lines.append(f"   • {row.subject}: {penalty_info['grade']}")

    return "\n".join(lines)