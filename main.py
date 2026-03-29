import asyncio
import html
import json
import logging
import os
import psutil
from typing import Optional
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import StateFilter, Command
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import BOT_TOKEN, BRS_USERNAME, BRS_PASSWORD, BRS_STUDENT_ID, WEBAPP_URL, API_PORT
from utils.calculators import (
    get_attendance_penalty,
    simulate_attendance_change,
    grade_by_percentage,
)
from utils.brs_helpers import get_brs_data, group_by_semester
from utils.schedule_parser import (
    parse_group_schedule, fetch_sheet_rows, get_available_groups,
    format_lesson_for_subgroup, group_by_day, has_subgroups, DAYS_ORDER, Lesson,
)
from database.db import init_db, get_user_profile, save_user_profile, get_reminder_users

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============ FAQ ============

_FAQ_PATH = os.path.join(os.path.dirname(__file__), "data", "faq.json")

def _load_faq() -> list[dict]:
    try:
        with open(_FAQ_PATH, encoding="utf-8") as f:
            return json.load(f).get("faq", [])
    except FileNotFoundError:
        logger.warning("data/faq.json не найден, FAQ будет пустым")
        return []

FAQ_ITEMS: list[dict] = _load_faq()

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Кэш БРС данных в памяти бота (обновляется каждый час)
BRS_CACHE = {"data": None, "timestamp": None}
CACHE_TTL = 3600  # 1 час

# Кэш расписания: ключ = "course_group", обновляется раз в сутки
SCHEDULE_CACHE: dict = {}   # {"3_10": {"data": [...], "timestamp": ...}}
SCHEDULE_TTL = 86400  # 24 часа

# Кэш доступных групп из таблицы (курс→[группы])
GROUPS_CACHE: dict = {"data": None, "timestamp": None}


# ============ FSM STATES ============

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


# ============ ГЛАВНОЕ МЕНЮ ============

def get_main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📚 Оценки из БРС", callback_data="brs_grades")],
        [InlineKeyboardButton(text="📅 Расписание", callback_data="schedule")],
        [InlineKeyboardButton(text="🧮 Влияние пропусков", callback_data="calc_attendance")],
        [InlineKeyboardButton(text="⏰ Напоминание", callback_data="reminder")],
        [InlineKeyboardButton(text="❓ FAQ", callback_data="faq")],
        [InlineKeyboardButton(text="⚙️ Профиль", callback_data="profile")],
    ])


@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    if WEBAPP_URL:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="🎓 Открыть приложение",
                web_app=types.WebAppInfo(url=WEBAPP_URL),
            )],
        ])
        await message.answer(
            "👋 Привет! Я помощник студента ВГУ ФКН.\n\n"
            "Нажми кнопку чтобы открыть приложение 👇",
            reply_markup=keyboard,
        )
    else:
        # fallback — старый текстовый режим пока WEBAPP_URL не настроен
        profile = await asyncio.to_thread(get_user_profile, message.from_user.id)
        if profile:
            subgroup_label = {0: "", 1: ", подгр. 1", 2: ", подгр. 2"}.get(profile.get("subgroup", 0), "")
            await message.answer(
                f"👋 Привет! {profile['course']} курс, {profile['group']} группа{subgroup_label}\n\n"
                "Выбери что нужно:",
                reply_markup=get_main_keyboard(),
                parse_mode="HTML",
            )
        else:
            await _start_profile_setup(message, state)


# ============ ПРОФИЛЬ / НАСТРОЙКА ============

async def _get_available_groups_cached() -> dict[int, list[int]]:
    """Возвращает доступные курсы и группы из таблицы (кэш 24ч)."""
    now = datetime.now().timestamp()
    if GROUPS_CACHE["data"] and now - (GROUPS_CACHE["timestamp"] or 0) < SCHEDULE_TTL:
        return GROUPS_CACHE["data"]
    rows = await asyncio.to_thread(fetch_sheet_rows)
    data = get_available_groups(rows)
    GROUPS_CACHE["data"] = data
    GROUPS_CACHE["timestamp"] = now
    return data


async def _start_profile_setup(target: Message, state: FSMContext):
    """Запускает FSM выбора курса."""
    await state.set_state(ProfileSetup.choosing_course)
    buttons = [
        [InlineKeyboardButton(text=f"{c} курс", callback_data=f"setup_course_{c}")]
        for c in range(1, 6)
    ]
    await target.answer(
        "👋 Привет! Я помощник студента ВГУ ФКН.\n\n"
        "Давай настроим профиль. На каком курсе ты учишься?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@dp.callback_query(ProfileSetup.choosing_course, F.data.startswith("setup_course_"))
async def setup_choose_course(callback: CallbackQuery, state: FSMContext):
    course = int(callback.data.split("_")[2])
    await callback.answer()
    await state.update_data(course=course)
    await state.set_state(ProfileSetup.choosing_group)

    await callback.message.edit_text("⏳ Загружаю список групп...")
    try:
        available = await _get_available_groups_cached()
    except Exception as e:
        await callback.message.edit_text(f"❌ Не удалось загрузить таблицу:\n{e}")
        await state.clear()
        return

    groups = available.get(course, [])
    if not groups:
        await callback.message.edit_text(f"❌ Группы для {course} курса не найдены в таблице.")
        await state.clear()
        return

    # Кнопки групп по 5 в ряд
    rows = [groups[i:i+5] for i in range(0, len(groups), 5)]
    buttons = [
        [InlineKeyboardButton(text=str(g), callback_data=f"setup_group_{g}") for g in row]
        for row in rows
    ]
    await callback.message.edit_text(
        f"<b>{course} курс</b> — выбери группу:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )


@dp.callback_query(ProfileSetup.choosing_group, F.data.startswith("setup_group_"))
async def setup_choose_group(callback: CallbackQuery, state: FSMContext):
    group = int(callback.data.split("_")[2])
    data = await state.get_data()
    course = data["course"]
    await callback.answer()
    await state.update_data(group=group)
    await state.set_state(ProfileSetup.choosing_subgroup)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="1️⃣ Подгруппа 1", callback_data="setup_subgroup_1"),
            InlineKeyboardButton(text="2️⃣ Подгруппа 2", callback_data="setup_subgroup_2"),
        ],
        [InlineKeyboardButton(text="👥 Вся группа", callback_data="setup_subgroup_0")],
    ])
    await callback.message.edit_text(
        f"<b>{course} курс, {group} группа</b>\n\n"
        "Ты в какой подгруппе?",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@dp.callback_query(ProfileSetup.choosing_subgroup, F.data.startswith("setup_subgroup_"))
async def setup_choose_subgroup(callback: CallbackQuery, state: FSMContext):
    subgroup = int(callback.data.split("_")[2])
    data = await state.get_data()
    course = data["course"]
    group = data["group"]
    await callback.answer()
    await state.clear()

    await asyncio.to_thread(save_user_profile, callback.from_user.id, course, group, subgroup)

    subgroup_label = {0: "вся группа", 1: "1 подгруппа", 2: "2 подгруппа"}.get(subgroup, "")
    await callback.message.edit_text(
        f"✅ Профиль сохранён!\n\n"
        f"<b>{course} курс, {group} группа</b> — {subgroup_label}\n\n"
        "Выбери что нужно:",
        reply_markup=get_main_keyboard(),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "profile")
async def show_profile(callback: CallbackQuery, state: FSMContext):
    profile = await asyncio.to_thread(get_user_profile, callback.from_user.id)
    if profile:
        subgroup_label = {0: "вся группа", 1: "1 подгруппа", 2: "2 подгруппа"}.get(
            profile.get("subgroup", 0), "не указана"
        )
        text = (
            f"⚙️ <b>Профиль</b>\n\n"
            f"📚 Курс: <b>{profile['course']}</b>\n"
            f"👥 Группа: <b>{profile['group']}</b>\n"
            f"🔢 Подгруппа: <b>{subgroup_label}</b>"
        )
    else:
        text = "⚙️ Профиль не настроен"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить профиль", callback_data="profile_edit")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_menu")],
    ])
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "profile_edit")
async def profile_edit(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(ProfileSetup.choosing_course)
    buttons = [
        [InlineKeyboardButton(text=f"{c} курс", callback_data=f"setup_course_{c}")]
        for c in range(1, 6)
    ]
    await callback.message.edit_text(
        "На каком курсе ты учишься?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


# ============ БРС ИНТЕГРАЦИЯ ============

async def get_cached_brs_data():
    """Получает данные БРС с кэшированием"""
    now = datetime.now().timestamp()

    # Если кэш еще свежий (не старше 1 часа)
    if (BRS_CACHE["data"] is not None and
            BRS_CACHE["timestamp"] is not None and
            now - BRS_CACHE["timestamp"] < CACHE_TTL):
        return BRS_CACHE["data"]

    # Иначе загружаем новые данные
    logger.info("⏳ Загружаю БРС (не в кэше)...")
    rows = await asyncio.to_thread(get_brs_data, BRS_USERNAME, BRS_PASSWORD, BRS_STUDENT_ID)

    if rows:
        BRS_CACHE["data"] = rows
        BRS_CACHE["timestamp"] = now
        logger.info(f"✅ БРС обновлены: {len(rows)} предметов")

    return rows


@dp.callback_query(F.data == "brs_grades")
async def show_brs_grades(callback: CallbackQuery, state: FSMContext):
    """Показать оценки из БРС"""
    await callback.message.edit_text("⏳ Загружаю данные из БРС...")
    await callback.answer()

    try:
        rows = await get_cached_brs_data()

        if not rows:
            await callback.message.edit_text(
                "❌ Не удалось загрузить данные из БРС.\n"
                "Проверь логин/пароль в .env",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_menu")],
                ])
            )
            return

        # Группируем по семестрам
        grouped = group_by_semester(rows)
        latest_sem = max(grouped.keys())

        # Сохраняем в FSM для навигации
        await state.update_data(
            semesters=grouped,
            current_semester=latest_sem
        )

        # Показываем текущий семестр
        await show_semester_grades(callback, state, latest_sem)

    except Exception as e:
        logger.error(f"Ошибка БРС: {e}")
        await callback.message.edit_text(
            f"❌ Ошибка при загрузке БРС:\n`{str(e)[:100]}`",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_menu")],
            ]),
            parse_mode="Markdown"
        )


async def show_semester_grades(callback: CallbackQuery, state: FSMContext, semester: int):
    """Показывает оценки конкретного семестра"""
    data = await state.get_data()
    grouped = data.get("semesters", {})

    if semester not in grouped:
        await callback.answer("Нет данных для этого семестра")
        return

    sem_rows = grouped[semester]

    # Формируем текст
    lines = [f"📊 **Семестр {semester}**\n"]

    for row in sem_rows:
        penalty_info = get_attendance_penalty(row.weighted_score)

        att_str = ""
        if row.attendance_pct:
            att_str = f"{row.attendance_pct}%"
            if row.weighted_score is not None:
                att_str += f" ({row.weighted_score})"
        else:
            att_str = "—"

        final_score_display = ""
        if row.final_score:
            final_score_display = f"{row.final_score}"
        if row.final_text:
            final_score_display += f" {row.final_text}"

        lines.append(
            f"{penalty_info['grade']} **{row.subject}**\n"
            f"   Посещаемость: {att_str}\n"
            f"   Оценка: {final_score_display or '—'}"
        )

    buttons = []
    semesters = sorted(grouped.keys(), reverse=True)  # например: [6, 5, 4, 3...]

    if len(semesters) > 1:
        sem_idx = semesters.index(semester)

        # Вперёд = к более новому семестру (в reverse=True это индекс - 1)
        if sem_idx > 0:
            next_sem = semesters[sem_idx - 1]
            buttons.append([
                InlineKeyboardButton(text=f"Семестр {next_sem} ➡️", callback_data=f"sem_{next_sem}")
            ])

        # Назад = к более старому семестру (в reverse=True это индекс + 1)
        if sem_idx < len(semesters) - 1:
            prev_sem = semesters[sem_idx + 1]
            buttons.append([
                InlineKeyboardButton(text=f"⬅️ Семестр {prev_sem}", callback_data=f"sem_{prev_sem}")
            ])

    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_menu")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=keyboard,
        parse_mode="Markdown"
    )


@dp.callback_query(F.data.startswith("sem_"))
async def navigate_semester(callback: CallbackQuery, state: FSMContext):
    """Переходит между семестрами"""
    sem = int(callback.data.split("_")[1])
    await show_semester_grades(callback, state, sem)
    await callback.answer()


# ============ РАСПИСАНИЕ ============

def _get_today_day() -> Optional[str]:
    """Возвращает название сегодняшнего дня по-русски или None (воскресенье)."""
    weekday = datetime.now().weekday()  # 0=Пн, 6=Вс
    if weekday < len(DAYS_ORDER):
        return DAYS_ORDER[weekday]
    return None


async def get_cached_schedule(course: int, group: int) -> list[Lesson]:
    """Возвращает расписание для (курс, группа) с кэшем на 24 часа."""
    key = f"{course}_{group}"
    now = datetime.now().timestamp()
    entry = SCHEDULE_CACHE.get(key)
    if entry and now - entry["timestamp"] < SCHEDULE_TTL:
        return entry["data"]

    lessons = await asyncio.to_thread(parse_group_schedule, group, course)
    SCHEDULE_CACHE[key] = {"data": lessons, "timestamp": now}
    return lessons


def _schedule_keyboard(week: str, active_day: str | None = None) -> InlineKeyboardMarkup:
    """Клавиатура расписания: числитель/знаменатель + дни + Сегодня + Обновить."""
    week_row = [
        InlineKeyboardButton(
            text="▶ Числитель" if week == "num" else "Числитель",
            callback_data="sched_num",
        ),
        InlineKeyboardButton(
            text="▶ Знаменатель" if week == "den" else "Знаменатель",
            callback_data="sched_den",
        ),
    ]
    short_days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб"]
    day_buttons = []
    for short, full in zip(short_days, DAYS_ORDER):
        mark = "▶ " if full == active_day else ""
        day_buttons.append(
            InlineKeyboardButton(
                text=f"{mark}{short}",
                callback_data=f"sched_day_{week}_{full}",
            )
        )

    return InlineKeyboardMarkup(inline_keyboard=[
        week_row,
        day_buttons[:3],
        day_buttons[3:],
        [
            InlineKeyboardButton(text="📅 Сегодня", callback_data=f"sched_today_{week}"),
            InlineKeyboardButton(text="🔄 Обновить", callback_data=f"sched_refresh_{week}"),
        ],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_menu")],
    ])


_LESSON_NUMS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣"]


def _format_day_schedule(
    lessons: list[Lesson], day: str, subgroup_num: int, week: str
) -> str:
    """Красиво форматирует расписание на день с учётом подгруппы и недели."""
    day_lessons = [l for l in lessons if l.day == day]
    week_label = "числитель" if week == "num" else "знаменатель"
    header = f"📅 <b>{day}</b>  ·  <i>{week_label}</i>"

    if not day_lessons:
        return f"{header}\n\nПар нет 🎉"

    parts = [header]
    n = 0
    for lesson in day_lessons:
        formatted = format_lesson_for_subgroup(lesson, subgroup_num, week)
        if not formatted:
            continue
        num = _LESSON_NUMS[n] if n < len(_LESSON_NUMS) else f"{n + 1}."
        n += 1
        parts.append(f"{num} <code>{html.escape(lesson.time)}</code>\n{formatted}")

    if n == 0:
        parts.append("Пар нет 🎉")

    return "\n\n".join(parts)


async def _get_profile_or_warn(callback: CallbackQuery) -> Optional[dict]:
    profile = await asyncio.to_thread(get_user_profile, callback.from_user.id)
    if not profile:
        await callback.message.edit_text(
            "⚙️ Сначала настрой профиль — укажи курс и группу.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⚙️ Настроить", callback_data="profile_edit")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_menu")],
            ]),
        )
        await callback.answer()
    return profile


@dp.callback_query(F.data == "schedule")
async def show_schedule(callback: CallbackQuery):
    """Показать расписание — открывает ближайший день с парами."""
    await callback.answer()

    profile = await _get_profile_or_warn(callback)
    if not profile:
        return

    await callback.message.edit_text("⏳ Загружаю расписание...")

    try:
        lessons = await get_cached_schedule(profile["course"], profile["group"])
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Не удалось загрузить расписание:\n{str(e)[:200]}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_menu")]
            ])
        )
        return

    # Открываем сегодня если есть пары, иначе первый день с парами
    today = _get_today_day()
    days_with_lessons = [d for d in DAYS_ORDER if any(l.day == d for l in lessons)]
    active_day = today if (today and today in days_with_lessons) else (days_with_lessons[0] if days_with_lessons else DAYS_ORDER[0])

    week = "num"
    subgroup = profile.get("subgroup", 0)
    text = _format_day_schedule(lessons, active_day, subgroup, week)
    await callback.message.edit_text(
        text,
        reply_markup=_schedule_keyboard(week, active_day),
        parse_mode="HTML"
    )


@dp.callback_query(F.data.in_({"sched_num", "sched_den"}))
async def switch_week(callback: CallbackQuery):
    await callback.answer()
    week = "num" if callback.data == "sched_num" else "den"
    profile = await asyncio.to_thread(get_user_profile, callback.from_user.id)
    if not profile:
        return
    try:
        lessons = await get_cached_schedule(profile["course"], profile["group"])
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)
        return
    msg_text = callback.message.text or ""
    active_day = next((d for d in DAYS_ORDER if d in msg_text), DAYS_ORDER[0])
    subgroup = profile.get("subgroup", 0)
    await callback.message.edit_text(
        _format_day_schedule(lessons, active_day, subgroup, week),
        reply_markup=_schedule_keyboard(week, active_day),
        parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("sched_day_"))
async def show_day(callback: CallbackQuery):
    await callback.answer()
    # формат: sched_day_{week}_{day}
    parts = callback.data.split("_", 3)
    week, day = parts[2], parts[3]
    profile = await asyncio.to_thread(get_user_profile, callback.from_user.id)
    if not profile:
        return
    try:
        lessons = await get_cached_schedule(profile["course"], profile["group"])
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)
        return
    subgroup = profile.get("subgroup", 0)
    await callback.message.edit_text(
        _format_day_schedule(lessons, day, subgroup, week),
        reply_markup=_schedule_keyboard(week, day),
        parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("sched_today_"))
async def show_today(callback: CallbackQuery):
    await callback.answer()
    week = callback.data.split("_")[2]
    profile = await asyncio.to_thread(get_user_profile, callback.from_user.id)
    if not profile:
        return
    today = _get_today_day()
    if not today:
        await callback.answer("Сегодня воскресенье — пар нет 🎉", show_alert=True)
        return
    try:
        lessons = await get_cached_schedule(profile["course"], profile["group"])
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)
        return
    subgroup = profile.get("subgroup", 0)
    await callback.message.edit_text(
        _format_day_schedule(lessons, today, subgroup, week),
        reply_markup=_schedule_keyboard(week, today),
        parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("sched_refresh_"))
async def refresh_schedule(callback: CallbackQuery):
    await callback.answer("🔄 Обновляю...")
    week = callback.data.split("_")[2]
    profile = await asyncio.to_thread(get_user_profile, callback.from_user.id)
    if not profile:
        return
    SCHEDULE_CACHE.pop(f"{profile['course']}_{profile['group']}", None)
    try:
        lessons = await get_cached_schedule(profile["course"], profile["group"])
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Ошибка обновления:\n{str(e)[:200]}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_menu")]
            ])
        )
        return
    msg_text = callback.message.text or ""
    active_day = next((d for d in DAYS_ORDER if d in msg_text), None)
    if not active_day:
        active_day = _get_today_day() or DAYS_ORDER[0]
    subgroup = profile.get("subgroup", 0)
    await callback.message.edit_text(
        _format_day_schedule(lessons, active_day, subgroup, week),
        reply_markup=_schedule_keyboard(week, active_day),
        parse_mode="HTML"
    )


# ============ КАЛЬКУЛЯТОР ПОСЕЩАЕМОСТИ ============

@dp.callback_query(F.data == "calc_attendance")
async def start_calc_attendance(callback: CallbackQuery, state: FSMContext):
    """Начать калькулятор посещаемости — выбор предмета"""
    await callback.answer()

    try:
        rows = await get_cached_brs_data()

        if not rows:
            await callback.message.edit_text(
                "❌ Нет данных БРС",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_menu")],
                ])
            )
            return

        # Создаем кнопки с предметами
        buttons = []
        for i, row in enumerate(rows[:10]):
            text = f"{row.subject[:30]} ({row.attendance_pct or '—'}%)"
            buttons.append([
                InlineKeyboardButton(text=text, callback_data=f"choose_subj_{i}")
            ])

        buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_menu")])

        # Сохраняем список предметов в state
        await state.update_data(subjects_list=rows)

        await callback.message.edit_text(
            "🧮 Выбери предмет:\n\n"
            "_Текущая посещаемость показана в скобках_",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.error(f"Ошибка при загрузке для калькулятора: {e}")
        await callback.message.edit_text(
            f"❌ Ошибка: {str(e)[:100]}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_menu")],
            ])
        )

@dp.callback_query(F.data.startswith("choose_subj_"))
async def choose_subject(callback: CallbackQuery, state: FSMContext):
    """Выбран предмет — показываем текущие данные"""
    subject_idx = int(callback.data.split("_")[2])
    data = await state.get_data()
    subjects = data.get("subjects_list", [])

    if subject_idx >= len(subjects):
        await callback.answer("❌ Предмет не найден")
        return

    row = subjects[subject_idx]
    await state.update_data(selected_subject=row)

    penalty_info = get_attendance_penalty(row.weighted_score)

    current_text = (
        f"📚 **{row.subject}**\n\n"
        f"**Текущие данные:**\n"
        f"Посещаемость: {row.attendance_pct or '—'}%\n"
        f"Взвешенный балл: {row.weighted_score or '—'} {penalty_info['grade']}\n"
        f"Статус: {penalty_info['description']}\n\n"
        f"Сколько пар еще пропустишь?"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="calc_attendance")],
    ])

    await callback.message.edit_text(current_text, reply_markup=keyboard, parse_mode="Markdown")
    await state.set_state(CalcAttendance.waiting_for_future_skips)
    await callback.answer()


@dp.message(CalcAttendance.waiting_for_future_skips)
async def calc_future_skips(message: Message, state: FSMContext):
    """Получить количество планируемых пропусков"""
    try:
        future_skips = int(message.text)
        await state.update_data(future_skips=future_skips)

        await state.set_state(CalcAttendance.waiting_for_future_total)

        await message.answer(
            "Сколько всего еще будет пар в этом году? (примерно)",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Не знаю", callback_data="future_total_unknown")],
            ])
        )
    except ValueError:
        await message.answer("❌ Введи число, например: 2")


def _build_attendance_result_text(row, future_skips: int, future_total: int, is_estimated: bool) -> str:
    """Строит текст результата калькулятора посещаемости"""
    current_pct = row.attendance_pct or 85.0

    result = simulate_attendance_change(
        current_pct=current_pct,
        classes_held=20,
        future_total=future_total,
        skips=future_skips,
    )

    note = f"_(оставшихся пар: {future_total} — примерная оценка)_\n\n" if is_estimated else ""

    return (
        f"📊 **Результат для {row.subject}**\n\n"
        f"{note}"
        f"**Сейчас:**\n"
        f"Посещаемость: {result['current_pct']}% ({grade_by_percentage(result['current_pct'])})\n"
        f"Взвешенный балл: {result['current_weighted']}\n\n"
        f"**После {future_skips} пропусков:**\n"
        f"Посещаемость: {result['new_pct']}% ({grade_by_percentage(result['new_pct'])})\n"
        f"Взвешенный балл: {result['new_weighted']}\n"
        f"Изменение: {result['change']} баллов\n\n"
        f"**Статус:** {result['description']} {result['grade']}"
    )


_CALC_RESULT_KEYBOARD = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🔄 Еще раз", callback_data="calc_attendance")],
    [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="back_menu")],
])


@dp.callback_query(StateFilter(CalcAttendance.waiting_for_future_total), F.data == "future_total_unknown")
async def future_total_unknown(callback: CallbackQuery, state: FSMContext):
    """Пользователь не знает сколько пар — считаем с дефолтным значением"""
    await callback.answer()

    data = await state.get_data()
    row = data.get("selected_subject")
    future_skips = data.get("future_skips")

    if not row:
        await callback.message.edit_text(
            "❌ Ошибка: предмет не выбран",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_menu")],
            ])
        )
        await state.clear()
        return

    result_text = _build_attendance_result_text(row, future_skips, future_total=30, is_estimated=True)
    await callback.message.edit_text(result_text, reply_markup=_CALC_RESULT_KEYBOARD, parse_mode="Markdown")
    await state.clear()


@dp.message(CalcAttendance.waiting_for_future_total)
async def calc_future_total(message: Message, state: FSMContext):
    """Получить количество оставшихся пар от пользователя"""
    try:
        future_total = int(message.text)
        if future_total <= 0:
            await message.answer("❌ Введи положительное число, например: 15")
            return
    except ValueError:
        await message.answer("❌ Введи число, например: 15")
        return

    data = await state.get_data()
    row = data.get("selected_subject")
    future_skips = data.get("future_skips")

    if not row:
        await message.answer(
            "❌ Ошибка: предмет не выбран. Начни заново.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_menu")],
            ])
        )
        await state.clear()
        return

    result_text = _build_attendance_result_text(row, future_skips, future_total, is_estimated=False)
    await message.answer(result_text, reply_markup=_CALC_RESULT_KEYBOARD, parse_mode="Markdown")
    await state.clear()


# ============ НАПОМИНАНИЯ ============

@dp.callback_query(F.data == "reminder")
async def show_reminder(callback: CallbackQuery):
    """Показать информацию о напоминаниях"""
    reminder_text = (
        "⏰ **Установить напоминание**\n\n"
        "Функция в разработке.\n\n"
        "Скоро ты сможешь здесь:\n"
        "🔔 Установить напоминание о паре\n"
        "🔔 Напоминание о сдаче ДЗ\n"
        "🔔 Напоминание об экзамене"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_menu")],
    ])
    await callback.message.edit_text(reminder_text, reply_markup=keyboard, parse_mode="Markdown")
    await callback.answer()


# ============ СПРАВКА ============

@dp.callback_query(F.data == "faq")
async def show_faq(callback: CallbackQuery):
    """Показать список вопросов FAQ кнопками"""
    if not FAQ_ITEMS:
        await callback.message.edit_text(
            "❓ FAQ пуст. Добавь вопросы в data/faq.json.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_menu")],
            ])
        )
        await callback.answer()
        return

    buttons = [
        [InlineKeyboardButton(text=item["question"], callback_data=f"faq_{i}")]
        for i, item in enumerate(FAQ_ITEMS)
    ]
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_menu")])

    await callback.message.edit_text(
        "❓ <b>Часто задаваемые вопросы</b>\n\nВыбери вопрос:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("faq_"))
async def show_faq_answer(callback: CallbackQuery):
    """Показать ответ на конкретный вопрос"""
    idx = int(callback.data.split("_")[1])
    if idx >= len(FAQ_ITEMS):
        await callback.answer("❌ Вопрос не найден")
        return

    item = FAQ_ITEMS[idx]
    text = f"❓ <b>{html.escape(item['question'])}</b>\n\n{html.escape(item['answer'])}"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ К вопросам", callback_data="faq")],
    ])
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


# ============ НАВИГАЦИЯ ============

@dp.callback_query(F.data == "back_menu")
async def back_to_menu(callback: CallbackQuery, state: FSMContext):
    """Вернуться в главное меню"""
    await state.clear()
    await callback.message.edit_text(
        "👋 **Главное меню**\n\n"
        "Выбери что тебе нужно:",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )
    await callback.answer()


# ============ НАПОМИНАНИЯ (фоновый таск) ============

def _detect_week_now() -> str:
    """Определяет числитель/знаменатель по текущей дате."""
    now = datetime.now()
    year = now.year if now.month >= 9 else now.year - 1
    sem_start = datetime(year, 9, 1)
    monday = sem_start - timedelta(days=sem_start.weekday())
    week_num = (now - monday).days // 7 + 1
    return "num" if week_num % 2 == 1 else "den"


def _parse_lesson_start(time_str: str) -> Optional[tuple]:
    """Парсит '8.00-9.35' -> (8, 0)."""
    try:
        start = time_str.split("-")[0].strip()
        sep = "." if "." in start else ":"
        h, m = start.split(sep)
        return int(h), int(m)
    except Exception:
        return None


_reminder_sent: set = set()
_reminder_last_day: str = ""


async def reminder_loop():
    global _reminder_sent, _reminder_last_day
    while True:
        await asyncio.sleep(30)
        try:
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")

            if _reminder_last_day != today_str:
                _reminder_sent.clear()
                _reminder_last_day = today_str

            weekday = now.weekday()
            if weekday >= 6:
                continue
            today_day_name = DAYS_ORDER[weekday]

            users = await asyncio.to_thread(get_reminder_users)
            week = _detect_week_now()

            for user in users:
                tid = user["telegram_id"]
                mins = user["minutes_before"]
                try:
                    lessons = await get_cached_schedule(user["course"], user["group"])
                except Exception:
                    continue

                for lesson in lessons:
                    if lesson.day != today_day_name:
                        continue
                    hm = _parse_lesson_start(lesson.time)
                    if not hm:
                        continue
                    h, m = hm
                    lesson_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
                    delta = (lesson_dt - now).total_seconds() / 60
                    key = (tid, today_str, lesson.time)
                    if 0 <= delta <= mins + 1 and key not in _reminder_sent:
                        _reminder_sent.add(key)
                        text = format_lesson_for_subgroup(lesson, user["subgroup"], week)
                        first_line = (text or "Пара").split("\n")[0].strip()
                        try:
                            await bot.send_message(
                                tid,
                                f"⏰ Через {mins} мин начинается пара!\n\n"
                                f"📚 {html.escape(first_line)}\n"
                                f"🕐 {lesson.time}",
                                parse_mode="HTML",
                            )
                        except Exception as e:
                            logger.warning(f"Reminder send {tid}: {e}")
        except Exception as e:
            logger.error(f"Reminder loop error: {e}")


# ============ ЗАПУСК ============

async def main():
    import uvicorn
    from api.server import app as fastapi_app

    init_db()
    logger.info("✅ База данных инициализирована (bot.db)")

    port = int(os.environ.get("PORT", API_PORT))
    config = uvicorn.Config(fastapi_app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)

    logger.info(f"🌐 API сервер запускается на порту {port}")
    logger.info("🤖 Бот запущен...")

    await asyncio.gather(
        server.serve(),
        dp.start_polling(bot),
        reminder_loop(),
    )


def free_port(port: int):
    import signal
    for conn in psutil.net_connections(kind="inet"):
        if conn.laddr.port == port and conn.status == "LISTEN":
            try:
                psutil.Process(conn.pid).terminate()
                logger.info(f"🔪 Убит процесс {conn.pid}, занимавший порт {port}")
            except Exception as e:
                logger.warning(f"Не удалось завершить процесс {conn.pid}: {e}")


if __name__ == "__main__":
    free_port(int(os.environ.get("PORT", API_PORT)))
    asyncio.run(main())