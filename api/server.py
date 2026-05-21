import json
import logging
import os
import sys
import asyncio
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.staticfiles import StaticFiles as _SF
from starlette.types import Scope, Receive, Send
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database.db import get_user_profile, save_user_profile, get_reminder_settings, save_reminder_settings, get_brs_cookies, save_brs_cookies, save_brs_credentials
from utils.brs_parser import brs_login, fetch_lessons_stats, fetch_and_parse_brs
from utils.calculators import get_attendance_penalty, simulate_attendance_change, grade_by_percentage
from utils.schedule_parser import parse_group_schedule, fetch_sheet_rows, get_available_groups

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title='Student Helper API')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])
BRS_CACHE: dict = {}
BRS_LOCKS: dict = {}
BRS_TTL = 3600
SCHEDULE_CACHE: dict = {}
SCHEDULE_TTL = 86400
GROUPS_CACHE: dict = {'data': None, 'timestamp': None}
_FAQ_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'faq.json')

def _load_faq() -> list:
    try:
        with open(_FAQ_PATH, encoding='utf-8') as f:
            return json.load(f).get('faq', [])
    except FileNotFoundError:
        return []
FAQ_ITEMS = _load_faq()

class ProfileBody(BaseModel):
    course: int
    group: int
    subgroup: int = 0

class AttendanceCalcBody(BaseModel):
    current_pct: float
    classes_held: int
    future_total: int
    skips: int

class ReminderBody(BaseModel):
    enabled: bool
    minutes_before: int = 15

class BrsCredentialsBody(BaseModel):
    username: str
    password: str
    student_id: str

@app.get('/api/health')
def health():
    return {'status': 'ok'}

@app.get('/api/faq')
def get_faq():
    return FAQ_ITEMS

@app.get('/api/profile/{telegram_id}')
async def api_get_profile(telegram_id: int):
    profile = await get_user_profile(telegram_id)
    if not profile:
        raise HTTPException(status_code=404, detail='Profile not found')
    return profile

@app.post('/api/profile/{telegram_id}')
async def api_save_profile(telegram_id: int, body: ProfileBody):
    await save_user_profile(telegram_id, body.course, body.group, body.subgroup)
    return {'ok': True}

@app.post('/api/brs/credentials/{telegram_id}')
async def api_save_brs_credentials(telegram_id: int, body: BrsCredentialsBody):
    await save_brs_credentials(telegram_id, body.username, body.password, body.student_id)
    if telegram_id in BRS_CACHE:
        del BRS_CACHE[telegram_id]
    return {'ok': True}

@app.get('/api/schedule/groups')
async def api_get_groups():
    now = datetime.now().timestamp()
    if GROUPS_CACHE['data'] and now - (GROUPS_CACHE['timestamp'] or 0) < SCHEDULE_TTL:
        return GROUPS_CACHE['data']
    rows = fetch_sheet_rows()
    data = get_available_groups(rows)
    GROUPS_CACHE['data'] = data
    GROUPS_CACHE['timestamp'] = now
    return {str(k): v for k, v in data.items()}

@app.get('/api/schedule')
async def api_get_schedule(course: int, group: int):
    key = f'{course}_{group}'
    now = datetime.now().timestamp()
    entry = SCHEDULE_CACHE.get(key)
    if entry and now - entry['timestamp'] < SCHEDULE_TTL:
        return entry['data']
    try:
        lessons = parse_group_schedule(group, course)
    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=str(e))
    result = [{'day': lesson.day, 'time': lesson.time, 'sub1_num': lesson.sub1_num, 'sub1_den': lesson.sub1_den, 'sub2_num': lesson.sub2_num, 'sub2_den': lesson.sub2_den} for lesson in lessons]
    SCHEDULE_CACHE[key] = {'data': result, 'timestamp': now}
    return result

@app.get('/api/brs')
async def api_get_brs(telegram_id: int):
    profile = await get_user_profile(telegram_id)
    if not profile or not profile.get('brs_username') or (not profile.get('brs_password')) or (not profile.get('student_id')):
        raise HTTPException(status_code=503, detail='BRS credentials not configured for user')

    now = datetime.now().timestamp()
    
    # 1. Если есть в кэше, сразу возвращаем (даже если старое)
    if telegram_id in BRS_CACHE and BRS_CACHE[telegram_id].get('data'):
        # Если данные свежие, просто возвращаем
        if now - BRS_CACHE[telegram_id]['timestamp'] < BRS_TTL:
            return BRS_CACHE[telegram_id]['data']
        # Если старые, возвращаем их и запускаем обновление в фоне
        asyncio.create_task(_refresh_brs_data(telegram_id, profile))
        return BRS_CACHE[telegram_id]['data']

    # 2. Если данных в кэше нет совсем, ждем их загрузки (вынужденная блокировка)
    if telegram_id not in BRS_LOCKS:
        BRS_LOCKS[telegram_id] = asyncio.Lock()
    
    async with BRS_LOCKS[telegram_id]:
        # Повторная проверка внутри лока (вдруг кто-то уже загрузил)
        if telegram_id in BRS_CACHE and BRS_CACHE[telegram_id].get('data'):
            return BRS_CACHE[telegram_id]['data']
            
        data = await _fetch_brs_sync(telegram_id, profile)
        return data

async def _refresh_brs_data(telegram_id: int, profile: dict):
    if telegram_id not in BRS_LOCKS:
        BRS_LOCKS[telegram_id] = asyncio.Lock()
    if BRS_LOCKS[telegram_id].locked():
        return
    async with BRS_LOCKS[telegram_id]:
        await _fetch_brs_sync(telegram_id, profile)

async def _fetch_brs_sync(telegram_id: int, profile: dict):
    cookies_json = await get_brs_cookies(telegram_id)
    cookies_dict = json.loads(cookies_json) if cookies_json else None
    
    rows, new_cookies = await fetch_and_parse_brs(
        profile['student_id'], 
        profile['brs_username'], 
        profile['brs_password'], 
        cookies_dict
    )
    if new_cookies:
        await save_brs_cookies(telegram_id, json.dumps(new_cookies))
        
    if not rows:
        raise HTTPException(status_code=502, detail='Failed to load BRS data')
        
    result = []
    for row in rows:
        penalty = get_attendance_penalty(row.weighted_score)
        result.append({
            'subject': row.subject, 'semester': row.semester, 'control': row.control, 
            'teacher': row.teacher_short, 'att1': row.att1, 'att2': row.att2, 
            'att3': row.att3, 'attendance_pct': row.attendance_pct, 
            'weighted_score': row.weighted_score, 'exam_score': row.exam_score, 
            'final_score': row.final_score, 'final_text': row.final_text, 
            'grade_icon': penalty['grade'], 'grade_desc': penalty['description'], 
            'lessons_url': row.lessons_url
        })
    BRS_CACHE[telegram_id] = {'data': result, 'timestamp': datetime.now().timestamp()}
    return result
LESSONS_CACHE: dict = {}
LESSONS_TTL = 3600

@app.get('/api/brs/lessons')
async def api_get_lessons(telegram_id: int, lessons_url: str):
    profile = await get_user_profile(telegram_id)
    if not profile or not profile.get('brs_username') or (not profile.get('brs_password')):
        raise HTTPException(status_code=503, detail='BRS credentials not configured for user')
    now = datetime.now().timestamp()
    cache_key = f'{telegram_id}_{lessons_url}'
    if cache_key in LESSONS_CACHE:
        entry = LESSONS_CACHE[cache_key]
        if now - entry['timestamp'] < LESSONS_TTL:
            return entry['data']
    cookies_json = await get_brs_cookies(telegram_id)
    cookies_dict = json.loads(cookies_json) if cookies_json else None
    client, valid_cookies = await brs_login(profile['brs_username'], profile['brs_password'], cookies_dict)
    if valid_cookies:
        await save_brs_cookies(telegram_id, json.dumps(valid_cookies))
    try:
        stats = await fetch_lessons_stats(lessons_url, client)
    finally:
        await client.aclose()
    data = {'total': stats.total, 'attended': stats.attended, 'skipped': stats.skipped}
    LESSONS_CACHE[cache_key] = {'data': data, 'timestamp': now}
    return data

@app.get('/api/reminders/{telegram_id}')
async def api_get_reminders(telegram_id: int):
    return await get_reminder_settings(telegram_id)

@app.post('/api/reminders/{telegram_id}')
async def api_save_reminders(telegram_id: int, body: ReminderBody):
    await save_reminder_settings(telegram_id, body.enabled, body.minutes_before)
    return {'ok': True}

@app.post('/api/calc/attendance')
def api_calc_attendance(body: AttendanceCalcBody):
    result = simulate_attendance_change(current_pct=body.current_pct, classes_held=body.classes_held, future_total=body.future_total, skips=body.skips)
    result['current_grade'] = grade_by_percentage(result['current_pct'])
    result['new_grade'] = grade_by_percentage(result['new_pct'])
    return result

class NoCacheStaticFiles(_SF):

    async def __call__(self, scope: Scope, receive: Receive, send: Send):

        async def no_cache_send(message):
            if message['type'] == 'http.response.start':
                headers = dict(message.get('headers', []))
                headers[b'cache-control'] = b'no-store, max-age=0'
                message['headers'] = list(headers.items())
            await send(message)
        await super().__call__(scope, receive, no_cache_send)
_WEBAPP_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'webapp')
if os.path.isdir(_WEBAPP_DIR):
    app.mount('/', NoCacheStaticFiles(directory=_WEBAPP_DIR, html=True), name='webapp')