from utils.calculators import get_attendance_penalty, simulate_attendance_change, grade_by_percentage

def test_get_attendance_penalty():
    # Test 90-100% (-1.0 to 0)
    res = get_attendance_penalty(0)
    assert res['grade'] == '✅'
    
    # Test 50-60% (-5.0 to -4.0)
    res2 = get_attendance_penalty(-4.5)
    assert res2['grade'] == '❌'
    assert res2['penalty_points'] == -4.5
    
    # Test None
    res_none = get_attendance_penalty(None)
    assert res_none['penalty_points'] == 0

def test_simulate_attendance_change():
    # 85% from 20 classes, 30 future, 3 skips
    res = simulate_attendance_change(85.0, 20, 30, 3)
    assert res['new_pct'] == 88.0
    assert res['new_weighted'] == -1.2
    
def test_grade_by_percentage():
    assert grade_by_percentage(90) == '5 ✅'
    assert grade_by_percentage(75) == '4 ✅'
    assert grade_by_percentage(60) == '3 ⚠️'
    assert grade_by_percentage(40) == '2 ❌'
