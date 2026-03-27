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

    # Формула: weighted = pct * 0.1 - 10
    # 90% → -1.0 | 70% → -3.0 | 60% → -4.0 | 50% → -5.0
    if weighted_score >= -1:      # pct >= 90%
        grade = '✅'
        desc = 'Отличная (90–100%)'
    elif weighted_score > -3:     # pct 71–89%
        grade = '✅'
        desc = 'Хорошая (71–89%)'
    elif weighted_score > -4:     # pct 61–70%
        grade = '⚠️'
        desc = 'Средняя (61–70%)'
    elif weighted_score >= -5:    # pct 50–60%
        grade = '❌'
        desc = 'Низкая (50–60%)'
    else:                         # pct < 50%
        grade = '❌'
        desc = 'Критическая (<50%)'

    return {
        'penalty_points': weighted_score,
        'description': desc,
        'grade': grade
    }


def simulate_attendance_change(
    current_pct: float,  # текущая посещаемость из БРС (%)
    classes_held: int,   # сколько пар уже прошло
    future_total: int,   # сколько пар ещё будет
    skips: int           # из будущих — планируешь пропустить
) -> dict:
    """
    Считает новую посещаемость после дополнительных пропусков.

    Формула БРС: weighted = pct * 0.1 - 10
      (82.35% → -1.76, 91.3% → -0.87, 76.92% → -2.31)

    Пример: 85% из 20 прошедших пар, впереди 30, пропустишь 3:
      attended = int(0.85*20) + (30-3) = 17 + 27 = 44
      total    = 20 + 30 = 50
      new_pct  = 44/50*100 = 88%
      new_weighted = 88 * 0.1 - 10 = -1.2
    """
    current_pct = max(0.0, min(100.0, current_pct))
    current_weighted = round(current_pct * 0.1 - 10, 2)

    attended_so_far = int(current_pct / 100 * classes_held)
    future_attended = max(0, future_total - skips)
    new_attended = attended_so_far + future_attended
    total = classes_held + future_total

    new_pct = round((new_attended / total * 100) if total > 0 else 0.0, 2)
    new_weighted = round(new_pct * 0.1 - 10, 2)
    penalty = get_attendance_penalty(new_weighted)

    return {
        'current_pct': round(current_pct, 2),
        'new_pct': new_pct,
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