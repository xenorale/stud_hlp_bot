from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from config import WEBAPP_URL

def get_main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='📚 Оценки из БРС', callback_data='brs_grades')], [InlineKeyboardButton(text='📅 Расписание', callback_data='schedule')], [InlineKeyboardButton(text='🧮 Влияние пропусков', callback_data='calc_attendance')], [InlineKeyboardButton(text='⏰ Напоминание', callback_data='reminder')], [InlineKeyboardButton(text='❓ FAQ', callback_data='faq')], [InlineKeyboardButton(text='⚙️ Профиль', callback_data='profile')]])

def get_webapp_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🎓 Открыть приложение', web_app={'url': WEBAPP_URL} if WEBAPP_URL else None)]])