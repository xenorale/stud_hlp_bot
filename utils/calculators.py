def get_attendance_penalty(weighted_score: float) -> dict:
    """
    Преобразует взвешенный балл посещаемости в штраф/бонус.

    Пример из БРС:
    - 82.35% → weighted_score = -1.76
    - 100% → weighted_score = 0 (или +5)
    - Ниже 50% → максимальный штраф

    Возвращает:
    {
        'penalty_points': float,      # штраф в баллах (отрицательное число)
        'description': str,            # описание
        'grade': str                   # оценка за посещаемость
    }
    """

    if weighted_score is None:
        return {
            'penalty_points': 0,
            'description': 'Нет данных о посещаемости',
            'grade': '❓'
        }

    # Штраф уже рассчитан БРС, просто интерпретируем
    if weighted_score >= 0:
        # Хорошая посещаемость — бонус или 0
        if weighted_score >= 4:
            grade = '✅'
            desc = 'Отличная (100%)'
        elif weighted_score >= 2:
            grade = '✅'
            desc = 'Хорошая (95-100%)'
        else:
            grade = '✅'
            desc = 'Нормальная (85-95%)'
    else:
        # Штраф за плохую посещаемость
        if weighted_score <= -3:
            grade = '❌'
            desc = 'Низкая (<60%)'
        elif weighted_score <= -1.5:
            grade = '⚠️'
            desc = 'Средняя (60-75%)'
        else:
            grade = '⚠️'
            desc = 'Слабая (75-85%)'

    return {
        'penalty_points': weighted_score,
        'description': desc,
        'grade': grade
    }


def simulate_attendance_change(
    current_weighted,   # может быть None!
    classes_attended: int,
    total_classes: int,
    skips: int
) -> dict:
    # Если weighted_score = None, считаем от attendance_pct напрямую
    if current_weighted is not None:
        current_pct = max(0, min(100, (current_weighted + 0.5) / 0.1 + 85))
    else:
        # Нет данных о weighted — берём посещаемость как 100%
        current_pct = 100.0
        current_weighted = 0.0

    new_attended = max(0, classes_attended - skips)
    new_total = total_classes + skips
    new_pct = (new_attended / new_total * 100) if new_total > 0 else 0

    new_weighted = round((new_pct - 85) * 0.1 - 0.5, 2)
    penalty = get_attendance_penalty(new_weighted)

    return {
        'current_pct': round(current_pct, 2),
        'new_pct': round(new_pct, 2),
        'current_weighted': current_weighted,
        'new_weighted': new_weighted,
        'change': round(new_weighted - current_weighted, 2),
        'grade': penalty['grade'],
        'description': penalty['description']
    }


def grade_by_percentage(pct: float) -> str:
    """Преобразует процент в оценку (5-балльная шкала)"""
    if pct >= 85:
        return '5 ✅'
    elif pct >= 70:
        return '4 ✅'
    elif pct >= 50:
        return '3 ⚠️'
    else:
        return '2 ❌'