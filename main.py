import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import StateFilter, Command
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import BOT_TOKEN, BRS_USERNAME, BRS_PASSWORD, BRS_STUDENT_ID
from utils.calculators import (
    get_attendance_penalty,
    simulate_attendance_change,
    grade_by_percentage,
)
from utils.brs_helpers import get_brs_data, group_by_semester
from database.db import init_db

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


# ============ FSM STATES ============

class CalcAttendance(StatesGroup):
    selecting_subject = State()
    waiting_for_future_skips = State()
    waiting_for_future_total = State()


class SetReminder(StatesGroup):
    waiting_for_subject = State()
    waiting_for_time = State()


# ============ ГЛАВНОЕ МЕНЮ ============

def get_main_keyboard():
    """Создает главное меню"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📚 Оценки из БРС", callback_data="brs_grades")],
        [InlineKeyboardButton(text="📅 Расписание", callback_data="schedule")],
        [InlineKeyboardButton(text="🧮 Влияние пропусков", callback_data="calc_attendance")],
        [InlineKeyboardButton(text="⏰ Напоминание", callback_data="reminder")],
        [InlineKeyboardButton(text="❓ FAQ", callback_data="faq")],
    ])
    return keyboard


@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Команда /start"""
    await message.answer(
        "👋 Привет! Я помощник студента ВГУ.\n\n"
        "Я умею:\n"
        "✅ Показать твои оценки из БРС\n"
        "✅ Показать расписание на неделю\n"
        "✅ Посчитать как пропуски повлияют на посещаемость\n"
        "✅ Установить напоминание о паре\n\n"
        "Выбери что тебе нужно:",
        reply_markup=get_main_keyboard()
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

@dp.callback_query(F.data == "schedule")
async def show_schedule(callback: CallbackQuery):
    """Показать расписание"""
    schedule_text = (
        "📅 **Твое расписание на неделю:**\n\n"
        "**Понедельник:**\n"
        "9:00-10:30 - Базы данных (ауд. 350, очно, преп. Петров И.И.)\n"
        "11:00-12:30 - Python (онлайн, преп. Сидоров А.А.)\n\n"
        "**Вторник:**\n"
        "10:00-11:30 - ООП (ауд. 401, очно, преп. Иванов В.В.)\n\n"
        "**Среда:**\n"
        "14:00-15:30 - Физика (ауд. 250, очно, преп. Волков П.П.)"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏰ Напоминание", callback_data="reminder")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_menu")],
    ])
    await callback.message.edit_text(schedule_text, reply_markup=keyboard, parse_mode="Markdown")
    await callback.answer()


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
    """Показать FAQ из data/faq.json"""
    if FAQ_ITEMS:
        lines = ["❓ **Часто задаваемые вопросы**\n"]
        for item in FAQ_ITEMS:
            lines.append(f"**{item['question']}**\n{item['answer']}")
        faq_text = "\n\n".join(lines)
    else:
        faq_text = "❓ **FAQ**\n\nРаздел пуст. Добавь вопросы в data/faq.json."

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_menu")],
    ])
    await callback.message.edit_text(faq_text, reply_markup=keyboard, parse_mode="Markdown")
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


# ============ ЗАПУСК БОТА ============

async def main():
    init_db()
    logger.info("✅ База данных инициализирована (bot.db)")
    logger.info("🤖 Бот запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())