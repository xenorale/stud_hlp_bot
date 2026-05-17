def get_attendance_penalty(weighted_score: float) -> dict:
    if weighted_score is None:
        return {'penalty_points': 0, 'description': 'Нет данных о посещаемости', 'grade': '❓'}
    if weighted_score >= -1:
        grade = '✅'
        desc = 'Отличная (90–100%)'
    elif weighted_score > -3:
        grade = '✅'
        desc = 'Хорошая (71–89%)'
    elif weighted_score > -4:
        grade = '⚠️'
        desc = 'Средняя (61–70%)'
    elif weighted_score >= -5:
        grade = '❌'
        desc = 'Низкая (50–60%)'
    else:
        grade = '❌'
        desc = 'Критическая (<50%)'
    return {'penalty_points': weighted_score, 'description': desc, 'grade': grade}

def simulate_attendance_change(current_pct: float, classes_held: int, future_total: int, skips: int) -> dict:
    current_pct = max(0.0, min(100.0, current_pct))
    current_weighted = round(current_pct * 0.1 - 10, 2)
    attended_so_far = int(current_pct / 100 * classes_held)
    future_attended = max(0, future_total - skips)
    new_attended = attended_so_far + future_attended
    total = classes_held + future_total
    new_pct = round(new_attended / total * 100 if total > 0 else 0.0, 2)
    new_weighted = round(new_pct * 0.1 - 10, 2)
    penalty = get_attendance_penalty(new_weighted)
    return {'current_pct': round(current_pct, 2), 'new_pct': new_pct, 'current_weighted': current_weighted, 'new_weighted': new_weighted, 'change': round(new_weighted - current_weighted, 2), 'grade': penalty['grade'], 'description': penalty['description']}

def grade_by_percentage(pct: float) -> str:
    if pct >= 85:
        return '5 ✅'
    elif pct >= 70:
        return '4 ✅'
    elif pct >= 50:
        return '3 ⚠️'
    else:
        return '2 ❌'