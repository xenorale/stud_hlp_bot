from aiogram.fsm.state import State, StatesGroup

class ProfileSetup(StatesGroup):
    choosing_course = State()
    choosing_group = State()
    choosing_subgroup = State()

class CalcAttendance(StatesGroup):
    selecting_subject = State()
    waiting_for_future_skips = State()
    waiting_for_future_total = State()

class SetReminder(StatesGroup):
    waiting_for_subject = State()
    waiting_for_time = State()

class BRSAuth(StatesGroup):
    waiting_for_username = State()
    waiting_for_password = State()
    waiting_for_student_id = State()