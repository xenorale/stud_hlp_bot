import asyncio
import logging
import os
import psutil
from datetime import datetime, timedelta
from typing import Optional
from aiogram import Bot, Dispatcher
import html
from config import BOT_TOKEN, API_PORT
from bot.handlers import router, get_cached_schedule
from utils.schedule_parser import DAYS_ORDER, format_lesson_for_subgroup
from database.db import init_db, get_reminder_users, get_all_brs_users
from bot.handlers import get_cached_brs_data, BRS_CACHE

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
dp.include_router(router)

def _detect_week_now() -> str:
    now = datetime.now()
    year = now.year if now.month >= 9 else now.year - 1
    sem_start = datetime(year, 9, 1)
    monday = sem_start - timedelta(days=sem_start.weekday())
    week_num = (now - monday).days // 7 + 1
    return 'num' if week_num % 2 == 1 else 'den'

def _parse_lesson_start(time_str: str) -> Optional[tuple]:
    try:
        start = time_str.split('-')[0].strip()
        sep = '.' if '.' in start else ':'
        h, m = start.split(sep)
        return (int(h), int(m))
    except Exception:
        return None

_reminder_sent: set = set()
_reminder_last_day: str = ''

async def reminder_loop():
    global _reminder_sent, _reminder_last_day
    while True:
        await asyncio.sleep(30)
        try:
            now = datetime.now()
            today_str = now.strftime('%Y-%m-%d')
            if _reminder_last_day != today_str:
                _reminder_sent.clear()
                _reminder_last_day = today_str
            
            weekday = now.weekday()
            if weekday >= 6:
                continue
            
            today_day_name = DAYS_ORDER[weekday]
            users = await get_reminder_users()
            week = _detect_week_now()
            
            for user in users:
                tid = user['telegram_id']
                mins = user['minutes_before']
                try:
                    lessons = get_cached_schedule(user['course'], user['group'])
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
                        text = format_lesson_for_subgroup(lesson, user['subgroup'], week)
                        if text:
                            try:
                                await bot.send_message(tid, f'⏰ Через {mins} мин пара!\n\n📚 {html.escape(text.split('\n')[0].strip())}\n🕐 {lesson.time}', parse_mode='HTML')
                            except Exception as e:
                                logger.warning(f'Reminder error {tid}: {e}')
        except Exception as e:
            logger.error(f'Reminder loop failure: {e}')

async def grade_check_loop():
    while True:
        await asyncio.sleep(3600)
        try:
            users = await get_all_brs_users()
            for user in users:
                tid = user['telegram_id']
                old_data = None
                if tid in BRS_CACHE and BRS_CACHE[tid]['data']:
                    old_data = {row.subject: row.final_score for row in BRS_CACHE[tid]['data'] if row.final_score is not None}
                
                if tid in BRS_CACHE:
                    BRS_CACHE[tid]['timestamp'] = 0
                
                try:
                    new_rows = await get_cached_brs_data(tid)
                    if not new_rows or old_data is None:
                        continue
                    for row in new_rows:
                        if row.final_score is not None:
                            old_score = old_data.get(row.subject)
                            if old_score is not None and old_score != row.final_score:
                                try:
                                    await bot.send_message(tid, f'🎉 <b>Изменилась оценка!</b>\n\n📚 {html.escape(row.subject)}\nБыло: <b>{old_score}</b> ➡️ Стало: <b>{row.final_score}</b>', parse_mode='HTML')
                                except Exception as e:
                                    logger.warning(f'Notification error {tid}: {e}')
                except Exception as e:
                    logger.warning(f'BRS sync error {tid}: {e}')
        except Exception as e:
            logger.error(f'Grade loop failure: {e}')

async def main():
    import uvicorn
    from api.server import app as fastapi_app
    
    await init_db()
    
    port = int(os.environ.get('PORT', API_PORT))
    config = uvicorn.Config(fastapi_app, host='0.0.0.0', port=port, log_level='warning')
    server = uvicorn.Server(config)
    
    await asyncio.gather(server.serve(), dp.start_polling(bot), reminder_loop(), grade_check_loop())

def free_port(port: int):
    for conn in psutil.net_connections(kind='inet'):
        if conn.laddr.port == port and conn.status == 'LISTEN':
            try:
                psutil.Process(conn.pid).terminate()
            except Exception:
                pass

if __name__ == '__main__':
    free_port(int(os.environ.get('PORT', API_PORT)))
    asyncio.run(main())