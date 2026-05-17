import asyncio
import html
import json
import logging
import os
from datetime import datetime
from typing import Optional
from aiogram import Router, types, F
from aiogram.filters import StateFilter, Command
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from aiogram.fsm.context import FSMContext
from config import WEBAPP_URL
from bot.states import ProfileSetup, CalcAttendance, BRSAuth
from bot.keyboards import get_main_keyboard
from utils.calculators import get_attendance_penalty, simulate_attendance_change, grade_by_percentage
from utils.brs_helpers import group_by_semester
from utils.brs_parser import fetch_and_parse_brs
from utils.schedule_parser import parse_group_schedule, fetch_sheet_rows, get_available_groups, format_lesson_for_subgroup, DAYS_ORDER, Lesson
from database.db import get_user_profile, save_user_profile, save_brs_credentials, get_brs_cookies, save_brs_cookies
logger = logging.getLogger(__name__)
router = Router()
_FAQ_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'faq.json')

def _load_faq() -> list[dict]:
    try:
        with open(_FAQ_PATH, encoding='utf-8') as f:
            return json.load(f).get('faq', [])
    except FileNotFoundError:
        logger.warning('data/faq.json не найден, FAQ будет пустым')
        return []
FAQ_ITEMS: list[dict] = _load_faq()
BRS_CACHE: dict = {}
CACHE_TTL = 3600
SCHEDULE_CACHE: dict = {}
SCHEDULE_TTL = 86400
GROUPS_CACHE: dict = {'data': None, 'timestamp': None}

@router.message(Command('start'))
async def cmd_start(message: Message, state: FSMContext):
    if WEBAPP_URL:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🎓 Открыть приложение', web_app=types.WebAppInfo(url=WEBAPP_URL))]])
        await message.answer('👋 Привет! Я помощник студента ВГУ ФКН.\n\nНажми кнопку чтобы открыть приложение 👇', reply_markup=keyboard)
    else:
        profile = await get_user_profile(message.from_user.id)
        if profile:
            subgroup_label = {0: '', 1: ', подгр. 1', 2: ', подгр. 2'}.get(profile.get('subgroup', 0), '')
            await message.answer(f"👋 Привет! {profile['course']} курс, {profile['group']} группа{subgroup_label}\n\nВыбери что нужно:", reply_markup=get_main_keyboard(), parse_mode='HTML')
        else:
            await _start_profile_setup(message, state)

async def _get_available_groups_cached() -> dict[int, list[int]]:
    now = datetime.now().timestamp()
    if GROUPS_CACHE['data'] and now - (GROUPS_CACHE['timestamp'] or 0) < SCHEDULE_TTL:
        return GROUPS_CACHE['data']
    rows = await asyncio.to_thread(fetch_sheet_rows)
    data = get_available_groups(rows)
    GROUPS_CACHE['data'] = data
    GROUPS_CACHE['timestamp'] = now
    return data

async def _start_profile_setup(target: Message, state: FSMContext):
    await state.set_state(ProfileSetup.choosing_course)
    buttons = [[InlineKeyboardButton(text=f'{c} курс', callback_data=f'setup_course_{c}')] for c in range(1, 6)]
    await target.answer('👋 Привет! Я помощник студента ВГУ ФКН.\n\nДавай настроим профиль. На каком курсе ты учишься?', reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@router.callback_query(ProfileSetup.choosing_course, F.data.startswith('setup_course_'))
async def setup_choose_course(callback: CallbackQuery, state: FSMContext):
    course = int(callback.data.split('_')[2])
    await callback.answer()
    await state.update_data(course=course)
    await state.set_state(ProfileSetup.choosing_group)
    await callback.message.edit_text('⏳ Загружаю список групп...')
    try:
        available = await _get_available_groups_cached()
    except Exception as e:
        await callback.message.edit_text(f'❌ Не удалось загрузить таблицу:\n{e}')
        await state.clear()
        return
    groups = available.get(course, [])
    if not groups:
        await callback.message.edit_text(f'❌ Группы для {course} курса не найдены в таблице.')
        await state.clear()
        return
    rows = [groups[i:i + 5] for i in range(0, len(groups), 5)]
    buttons = [[InlineKeyboardButton(text=str(g), callback_data=f'setup_group_{g}') for g in row] for row in rows]
    await callback.message.edit_text(f'<b>{course} курс</b> — выбери группу:', reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode='HTML')

@router.callback_query(ProfileSetup.choosing_group, F.data.startswith('setup_group_'))
async def setup_choose_group(callback: CallbackQuery, state: FSMContext):
    group = int(callback.data.split('_')[2])
    data = await state.get_data()
    course = data['course']
    await callback.answer()
    await state.update_data(group=group)
    await state.set_state(ProfileSetup.choosing_subgroup)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='1️⃣ Подгруппа 1', callback_data='setup_subgroup_1'), InlineKeyboardButton(text='2️⃣ Подгруппа 2', callback_data='setup_subgroup_2')], [InlineKeyboardButton(text='👥 Вся группа', callback_data='setup_subgroup_0')]])
    await callback.message.edit_text(f'<b>{course} курс, {group} группа</b>\n\nТы в какой подгруппе?', reply_markup=keyboard, parse_mode='HTML')

@router.callback_query(ProfileSetup.choosing_subgroup, F.data.startswith('setup_subgroup_'))
async def setup_choose_subgroup(callback: CallbackQuery, state: FSMContext):
    subgroup = int(callback.data.split('_')[2])
    data = await state.get_data()
    course = data['course']
    group = data['group']
    await callback.answer()
    await state.clear()
    await save_user_profile(callback.from_user.id, course, group, subgroup)
    subgroup_label = {0: 'вся группа', 1: '1 подгруппа', 2: '2 подгруппа'}.get(subgroup, '')
    await callback.message.edit_text(f'✅ Профиль сохранён!\n\n<b>{course} курс, {group} группа</b> — {subgroup_label}\n\nВыбери что нужно:', reply_markup=get_main_keyboard(), parse_mode='HTML')

@router.callback_query(F.data == 'profile')
async def show_profile(callback: CallbackQuery, state: FSMContext):
    profile = await get_user_profile(callback.from_user.id)
    if profile:
        subgroup_label = {0: 'вся группа', 1: '1 подгруппа', 2: '2 подгруппа'}.get(profile.get('subgroup', 0), 'не указана')
        text = f"⚙️ <b>Профиль</b>\n\n📚 Курс: <b>{profile['course']}</b>\n👥 Группа: <b>{profile['group']}</b>\n🔢 Подгруппа: <b>{subgroup_label}</b>"
    else:
        text = '⚙️ Профиль не настроен'
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='✏️ Изменить профиль', callback_data='profile_edit')], [InlineKeyboardButton(text='⬅️ Назад', callback_data='back_menu')]])
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode='HTML')
    await callback.answer()

@router.callback_query(F.data == 'profile_edit')
async def profile_edit(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(ProfileSetup.choosing_course)
    buttons = [[InlineKeyboardButton(text=f'{c} курс', callback_data=f'setup_course_{c}')] for c in range(1, 6)]
    await callback.message.edit_text('На каком курсе ты учишься?', reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

async def get_cached_brs_data(telegram_id: int):
    profile = await get_user_profile(telegram_id)
    if not profile or not profile.get('brs_username') or (not profile.get('brs_password')) or (not profile.get('student_id')):
        return None
    now = datetime.now().timestamp()
    if telegram_id in BRS_CACHE and BRS_CACHE[telegram_id]['data'] and (now - BRS_CACHE[telegram_id]['timestamp'] < CACHE_TTL):
        return BRS_CACHE[telegram_id]['data']
    logger.info('⏳ Загружаю БРС (не в кэше)...')
    cookies_json = await get_brs_cookies(telegram_id)
    cookies_dict = json.loads(cookies_json) if cookies_json else None
    try:
        rows, new_cookies = await fetch_and_parse_brs(profile['student_id'], profile['brs_username'], profile['brs_password'], cookies_dict)
        if new_cookies:
            await save_brs_cookies(telegram_id, json.dumps(new_cookies))
    except Exception as e:
        logger.error(f'Ошибка загрузки БРС: {e}')
        rows = None
    if rows:
        BRS_CACHE[telegram_id] = {'data': rows, 'timestamp': now}
        logger.info(f'✅ БРС обновлены: {len(rows)} предметов')
    return rows

@router.callback_query(F.data == 'brs_grades')
async def show_brs_grades(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    profile = await get_user_profile(callback.from_user.id)
    if not profile or not profile.get('brs_username'):
        await callback.message.edit_text('Для доступа к БРС нужно войти. Введи свой логин от БРС (например, s0000000):')
        await state.set_state(BRSAuth.waiting_for_username)
        return
    await callback.message.edit_text('⏳ Загружаю данные из БРС...')
    try:
        rows = await get_cached_brs_data(callback.from_user.id)
        if not rows:
            await callback.message.edit_text('❌ Не удалось загрузить данные из БРС.\nПроверь логин/пароль.', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ Назад', callback_data='back_menu')]]))
            return
        grouped = group_by_semester(rows)
        if not grouped:
            await callback.message.edit_text('❌ Нет данных БРС', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ Назад', callback_data='back_menu')]]))
            return
        latest_sem = max(grouped.keys())
        await state.update_data(semesters=grouped, current_semester=latest_sem)
        await show_semester_grades(callback, state, latest_sem)
    except Exception as e:
        logger.error(f'Ошибка БРС: {e}')
        await callback.message.edit_text(f'❌ Ошибка при загрузке БРС:\n`{str(e)[:100]}`', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ Назад', callback_data='back_menu')]]), parse_mode='Markdown')

@router.message(BRSAuth.waiting_for_username)
async def brs_auth_username(message: Message, state: FSMContext):
    await state.update_data(brs_username=message.text.strip())
    await state.set_state(BRSAuth.waiting_for_password)
    await message.answer('Теперь введи пароль от БРС:')

@router.message(BRSAuth.waiting_for_password)
async def brs_auth_password(message: Message, state: FSMContext):
    await state.update_data(brs_password=message.text.strip())
    await state.set_state(BRSAuth.waiting_for_student_id)
    await message.answer('Введи свой ID в БРС (можно найти в URL страницы БРС):')

@router.message(BRSAuth.waiting_for_student_id)
async def brs_auth_student_id(message: Message, state: FSMContext):
    student_id = message.text.strip()
    data = await state.get_data()
    username = data.get('brs_username')
    password = data.get('brs_password')
    await save_brs_credentials(message.from_user.id, username, password, student_id)
    await state.clear()
    await message.answer('✅ Учетные данные БРС сохранены!', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='📚 Открыть оценки', callback_data='brs_grades')]]))

async def show_semester_grades(callback: CallbackQuery, state: FSMContext, semester: int):
    data = await state.get_data()
    grouped = data.get('semesters', {})
    if semester not in grouped:
        await callback.answer('Нет данных для этого семестра')
        return
    sem_rows = grouped[semester]
    lines = [f'📊 **Семестр {semester}**\n']
    for row in sem_rows:
        penalty_info = get_attendance_penalty(row.weighted_score)
        att_str = f'{row.attendance_pct}%' if row.attendance_pct else '—'
        if row.weighted_score is not None and row.attendance_pct:
            att_str += f' ({row.weighted_score})'
        final_score_display = ''
        if row.final_score:
            final_score_display = f'{row.final_score}'
        if row.final_text:
            final_score_display += f' {row.final_text}'
        lines.append(f"{penalty_info['grade']} **{row.subject}**\n   Посещаемость: {att_str}\n   Оценка: {final_score_display or '—'}")
    buttons = []
    semesters = sorted(grouped.keys(), reverse=True)
    if len(semesters) > 1:
        sem_idx = semesters.index(semester)
        if sem_idx > 0:
            next_sem = semesters[sem_idx - 1]
            buttons.append([InlineKeyboardButton(text=f'Семестр {next_sem} ➡️', callback_data=f'sem_{next_sem}')])
        if sem_idx < len(semesters) - 1:
            prev_sem = semesters[sem_idx + 1]
            buttons.append([InlineKeyboardButton(text=f'⬅️ Семестр {prev_sem}', callback_data=f'sem_{prev_sem}')])
    buttons.append([InlineKeyboardButton(text='⬅️ Назад', callback_data='back_menu')])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text('\n'.join(lines), reply_markup=keyboard, parse_mode='Markdown')

@router.callback_query(F.data.startswith('sem_'))
async def navigate_semester(callback: CallbackQuery, state: FSMContext):
    sem = int(callback.data.split('_')[1])
    await show_semester_grades(callback, state, sem)
    await callback.answer()

def _get_today_day() -> Optional[str]:
    weekday = datetime.now().weekday()
    if weekday < len(DAYS_ORDER):
        return DAYS_ORDER[weekday]
    return None

async def get_cached_schedule(course: int, group: int) -> list[Lesson]:
    key = f'{course}_{group}'
    now = datetime.now().timestamp()
    entry = SCHEDULE_CACHE.get(key)
    if entry and now - entry['timestamp'] < SCHEDULE_TTL:
        return entry['data']
    lessons = await asyncio.to_thread(parse_group_schedule, group, course)
    SCHEDULE_CACHE[key] = {'data': lessons, 'timestamp': now}
    return lessons

def _schedule_keyboard(week: str, active_day: str | None=None) -> InlineKeyboardMarkup:
    week_row = [InlineKeyboardButton(text='▶ Числитель' if week == 'num' else 'Числитель', callback_data='sched_num'), InlineKeyboardButton(text='▶ Знаменатель' if week == 'den' else 'Знаменатель', callback_data='sched_den')]
    short_days = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб']
    day_buttons = []
    for short, full in zip(short_days, DAYS_ORDER):
        mark = '▶ ' if full == active_day else ''
        day_buttons.append(InlineKeyboardButton(text=f'{mark}{short}', callback_data=f'sched_day_{week}_{full}'))
    return InlineKeyboardMarkup(inline_keyboard=[week_row, day_buttons[:3], day_buttons[3:], [InlineKeyboardButton(text='📅 Сегодня', callback_data=f'sched_today_{week}'), InlineKeyboardButton(text='🔄 Обновить', callback_data=f'sched_refresh_{week}')], [InlineKeyboardButton(text='⬅️ Назад', callback_data='back_menu')]])
_LESSON_NUMS = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣']

def _format_day_schedule(lessons: list[Lesson], day: str, subgroup_num: int, week: str) -> str:
    day_lessons = [lesson for lesson in lessons if lesson.day == day]
    week_label = 'числитель' if week == 'num' else 'знаменатель'
    header = f'📅 <b>{day}</b>  ·  <i>{week_label}</i>'
    if not day_lessons:
        return f'{header}\n\nПар нет 🎉'
    parts = [header]
    n = 0
    for lesson in day_lessons:
        formatted = format_lesson_for_subgroup(lesson, subgroup_num, week)
        if not formatted:
            continue
        num = _LESSON_NUMS[n] if n < len(_LESSON_NUMS) else f'{n + 1}.'
        n += 1
        parts.append(f'{num} <code>{html.escape(lesson.time)}</code>\n{formatted}')
    if n == 0:
        parts.append('Пар нет 🎉')
    return '\n\n'.join(parts)

@router.callback_query(F.data == 'schedule')
async def show_schedule(callback: CallbackQuery):
    await callback.answer()
    profile = await get_user_profile(callback.from_user.id)
    if not profile:
        await callback.message.edit_text('⚙️ Сначала настрой профиль — укажи курс и группу.', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⚙️ Настроить', callback_data='profile_edit')], [InlineKeyboardButton(text='⬅️ Назад', callback_data='back_menu')]]))
        return
    await callback.message.edit_text('⏳ Загружаю расписание...')
    try:
        lessons = await get_cached_schedule(profile['course'], profile['group'])
    except Exception as e:
        await callback.message.edit_text(f'❌ Не удалось загрузить расписание:\n{str(e)[:200]}', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ Назад', callback_data='back_menu')]]))
        return
    today = _get_today_day()
    days_with_lessons = [d for d in DAYS_ORDER if any((lesson.day == d for lesson in lessons))]
    active_day = today if today and today in days_with_lessons else days_with_lessons[0] if days_with_lessons else DAYS_ORDER[0]
    week = 'num'
    subgroup = profile.get('subgroup', 0)
    text = _format_day_schedule(lessons, active_day, subgroup, week)
    await callback.message.edit_text(text, reply_markup=_schedule_keyboard(week, active_day), parse_mode='HTML')

@router.callback_query(F.data.in_({'sched_num', 'sched_den'}))
async def switch_week(callback: CallbackQuery):
    await callback.answer()
    week = 'num' if callback.data == 'sched_num' else 'den'
    profile = await get_user_profile(callback.from_user.id)
    if not profile:
        return
    try:
        lessons = await get_cached_schedule(profile['course'], profile['group'])
    except Exception as e:
        await callback.answer(f'Ошибка: {e}', show_alert=True)
        return
    msg_text = callback.message.text or ''
    active_day = next((d for d in DAYS_ORDER if d in msg_text), DAYS_ORDER[0])
    subgroup = profile.get('subgroup', 0)
    await callback.message.edit_text(_format_day_schedule(lessons, active_day, subgroup, week), reply_markup=_schedule_keyboard(week, active_day), parse_mode='HTML')

@router.callback_query(F.data.startswith('sched_day_'))
async def show_day(callback: CallbackQuery):
    await callback.answer()
    parts = callback.data.split('_', 3)
    week, day = (parts[2], parts[3])
    profile = await get_user_profile(callback.from_user.id)
    if not profile:
        return
    try:
        lessons = await get_cached_schedule(profile['course'], profile['group'])
    except Exception as e:
        await callback.answer(f'Ошибка: {e}', show_alert=True)
        return
    subgroup = profile.get('subgroup', 0)
    await callback.message.edit_text(_format_day_schedule(lessons, day, subgroup, week), reply_markup=_schedule_keyboard(week, day), parse_mode='HTML')

@router.callback_query(F.data.startswith('sched_today_'))
async def show_today(callback: CallbackQuery):
    await callback.answer()
    week = callback.data.split('_')[2]
    profile = await get_user_profile(callback.from_user.id)
    if not profile:
        return
    today = _get_today_day()
    if not today:
        await callback.answer('Сегодня воскресенье — пар нет 🎉', show_alert=True)
        return
    try:
        lessons = await get_cached_schedule(profile['course'], profile['group'])
    except Exception as e:
        await callback.answer(f'Ошибка: {e}', show_alert=True)
        return
    subgroup = profile.get('subgroup', 0)
    await callback.message.edit_text(_format_day_schedule(lessons, today, subgroup, week), reply_markup=_schedule_keyboard(week, today), parse_mode='HTML')

@router.callback_query(F.data.startswith('sched_refresh_'))
async def refresh_schedule(callback: CallbackQuery):
    await callback.answer('🔄 Обновляю...')
    week = callback.data.split('_')[2]
    profile = await get_user_profile(callback.from_user.id)
    if not profile:
        return
    SCHEDULE_CACHE.pop(f"{profile['course']}_{profile['group']}", None)
    try:
        lessons = await get_cached_schedule(profile['course'], profile['group'])
    except Exception as e:
        await callback.message.edit_text(f'❌ Ошибка обновления:\n{str(e)[:200]}', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ Назад', callback_data='back_menu')]]))
        return
    msg_text = callback.message.text or ''
    active_day = next((d for d in DAYS_ORDER if d in msg_text), None)
    if not active_day:
        active_day = _get_today_day() or DAYS_ORDER[0]
    subgroup = profile.get('subgroup', 0)
    await callback.message.edit_text(_format_day_schedule(lessons, active_day, subgroup, week), reply_markup=_schedule_keyboard(week, active_day), parse_mode='HTML')

@router.callback_query(F.data == 'calc_attendance')
async def start_calc_attendance(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    profile = await get_user_profile(callback.from_user.id)
    if not profile or not profile.get('brs_username'):
        await callback.message.edit_text('Для доступа к БРС нужно войти. Введи свой логин от БРС (например, s0000000):')
        await state.set_state(BRSAuth.waiting_for_username)
        return
    try:
        rows = await get_cached_brs_data(callback.from_user.id)
        if not rows:
            await callback.message.edit_text('❌ Нет данных БРС', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ Назад', callback_data='back_menu')]]))
            return
        buttons = []
        for i, row in enumerate(rows[:10]):
            text = f"{row.subject[:30]} ({row.attendance_pct or '—'}%)"
            buttons.append([InlineKeyboardButton(text=text, callback_data=f'choose_subj_{i}')])
        buttons.append([InlineKeyboardButton(text='⬅️ Назад', callback_data='back_menu')])
        await state.update_data(subjects_list=rows)
        await callback.message.edit_text('🧮 Выбери предмет:\n\n_Текущая посещаемость показана в скобках_', reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode='Markdown')
    except Exception as e:
        logger.error(f'Ошибка при загрузке для калькулятора: {e}')
        await callback.message.edit_text(f'❌ Ошибка: {str(e)[:100]}', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ Назад', callback_data='back_menu')]]))

@router.callback_query(F.data.startswith('choose_subj_'))
async def choose_subject(callback: CallbackQuery, state: FSMContext):
    subject_idx = int(callback.data.split('_')[2])
    data = await state.get_data()
    subjects = data.get('subjects_list', [])
    if subject_idx >= len(subjects):
        await callback.answer('❌ Предмет не найден')
        return
    row = subjects[subject_idx]
    await state.update_data(selected_subject=row)
    penalty_info = get_attendance_penalty(row.weighted_score)
    current_text = f"📚 **{row.subject}**\n\n**Текущие данные:**\nПосещаемость: {row.attendance_pct or '—'}%\nВзвешенный балл: {row.weighted_score or '—'} {penalty_info['grade']}\nСтатус: {penalty_info['description']}\n\nСколько пар еще пропустишь?"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ Назад', callback_data='calc_attendance')]])
    await callback.message.edit_text(current_text, reply_markup=keyboard, parse_mode='Markdown')
    await state.set_state(CalcAttendance.waiting_for_future_skips)
    await callback.answer()

@router.message(CalcAttendance.waiting_for_future_skips)
async def calc_future_skips(message: Message, state: FSMContext):
    try:
        future_skips = int(message.text)
        await state.update_data(future_skips=future_skips)
        await state.set_state(CalcAttendance.waiting_for_future_total)
        await message.answer('Сколько всего еще будет пар в этом году? (примерно)', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Не знаю', callback_data='future_total_unknown')]]))
    except ValueError:
        await message.answer('❌ Введи число, например: 2')

def _build_attendance_result_text(row, future_skips: int, future_total: int, is_estimated: bool) -> str:
    current_pct = row.attendance_pct or 85.0
    result = simulate_attendance_change(current_pct=current_pct, classes_held=20, future_total=future_total, skips=future_skips)
    note = f'_(оставшихся пар: {future_total} — примерная оценка)_\n\n' if is_estimated else ''
    return f"📊 **Результат для {row.subject}**\n\n{note}**Сейчас:**\nПосещаемость: {result['current_pct']}% ({grade_by_percentage(result['current_pct'])})\nВзвешенный балл: {result['current_weighted']}\n\n**После {future_skips} пропусков:**\nПосещаемость: {result['new_pct']}% ({grade_by_percentage(result['new_pct'])})\nВзвешенный балл: {result['new_weighted']}\nИзменение: {result['change']} баллов\n\n**Статус:** {result['description']} {result['grade']}"
_CALC_RESULT_KEYBOARD = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🔄 Еще раз', callback_data='calc_attendance')], [InlineKeyboardButton(text='⬅️ Главное меню', callback_data='back_menu')]])

@router.callback_query(StateFilter(CalcAttendance.waiting_for_future_total), F.data == 'future_total_unknown')
async def future_total_unknown(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    row = data.get('selected_subject')
    future_skips = data.get('future_skips')
    if not row:
        await callback.message.edit_text('❌ Ошибка: предмет не выбран', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ Назад', callback_data='back_menu')]]))
        await state.clear()
        return
    result_text = _build_attendance_result_text(row, future_skips, future_total=30, is_estimated=True)
    await callback.message.edit_text(result_text, reply_markup=_CALC_RESULT_KEYBOARD, parse_mode='Markdown')
    await state.clear()

@router.message(CalcAttendance.waiting_for_future_total)
async def calc_future_total(message: Message, state: FSMContext):
    try:
        future_total = int(message.text)
        if future_total <= 0:
            await message.answer('❌ Введи положительное число, например: 15')
            return
    except ValueError:
        await message.answer('❌ Введи число, например: 15')
        return
    data = await state.get_data()
    row = data.get('selected_subject')
    future_skips = data.get('future_skips')
    if not row:
        await message.answer('❌ Ошибка: предмет не выбран. Начни заново.', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ Назад', callback_data='back_menu')]]))
        await state.clear()
        return
    result_text = _build_attendance_result_text(row, future_skips, future_total, is_estimated=False)
    await message.answer(result_text, reply_markup=_CALC_RESULT_KEYBOARD, parse_mode='Markdown')
    await state.clear()

@router.callback_query(F.data == 'reminder')
async def show_reminder(callback: CallbackQuery):
    reminder_text = '⏰ **Установить напоминание**\n\nФункция в разработке.\n\nСкоро ты сможешь здесь:\n🔔 Установить напоминание о паре\n🔔 Напоминание о сдаче ДЗ\n🔔 Напоминание об экзамене'
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ Назад', callback_data='back_menu')]])
    await callback.message.edit_text(reminder_text, reply_markup=keyboard, parse_mode='Markdown')
    await callback.answer()

@router.callback_query(F.data == 'faq')
async def show_faq(callback: CallbackQuery):
    if not FAQ_ITEMS:
        await callback.message.edit_text('❓ FAQ пуст. Добавь вопросы в data/faq.json.', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ Назад', callback_data='back_menu')]]))
        await callback.answer()
        return
    buttons = [[InlineKeyboardButton(text=item['question'], callback_data=f'faq_{i}')] for i, item in enumerate(FAQ_ITEMS)]
    buttons.append([InlineKeyboardButton(text='⬅️ Назад', callback_data='back_menu')])
    await callback.message.edit_text('❓ <b>Часто задаваемые вопросы</b>\n\nВыбери вопрос:', reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode='HTML')
    await callback.answer()

@router.callback_query(F.data.startswith('faq_'))
async def show_faq_answer(callback: CallbackQuery):
    idx = int(callback.data.split('_')[1])
    if idx >= len(FAQ_ITEMS):
        await callback.answer('❌ Вопрос не найден')
        return
    item = FAQ_ITEMS[idx]
    text = f"❓ <b>{html.escape(item['question'])}</b>\n\n{html.escape(item['answer'])}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ К вопросам', callback_data='faq')]])
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode='HTML')
    await callback.answer()

@router.callback_query(F.data == 'back_menu')
async def back_to_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text('👋 **Главное меню**\n\nВыбери что тебе нужно:', reply_markup=get_main_keyboard(), parse_mode='Markdown')
    await callback.answer()