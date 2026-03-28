import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Позволяем импортировать из корня проекта
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import BRS_USERNAME, BRS_PASSWORD, BRS_STUDENT_ID
from database.db import get_user_profile, save_user_profile, get_reminder_settings, save_reminder_settings
from utils.brs_helpers import get_brs_data, group_by_semester
from utils.brs_parser import brs_login, fetch_lessons_stats
from utils.calculators import get_attendance_penalty, simulate_attendance_change, grade_by_percentage
from utils.schedule_parser import (
    parse_group_schedule, fetch_sheet_rows, get_available_groups,
    DAYS_ORDER,
)

logger = logging.getLogger(__name__)

app = FastAPI(title="Student Helper API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Кэш ────────────────────────────────────────────────────────────────────

BRS_CACHE: dict = {"data": None, "timestamp": None}
BRS_TTL = 3600

SCHEDULE_CACHE: dict = {}
SCHEDULE_TTL = 86400

GROUPS_CACHE: dict = {"data": None, "timestamp": None}

_FAQ_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "faq.json")


def _load_faq() -> list:
    try:
        with open(_FAQ_PATH, encoding="utf-8") as f:
            return json.load(f).get("faq", [])
    except FileNotFoundError:
        return []


FAQ_ITEMS = _load_faq()


# ─── Модели запросов ─────────────────────────────────────────────────────────

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


# ─── Эндпоинты ───────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/faq")
def get_faq():
    return FAQ_ITEMS


@app.get("/api/profile/{telegram_id}")
def api_get_profile(telegram_id: int):
    profile = get_user_profile(telegram_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


@app.post("/api/profile/{telegram_id}")
def api_save_profile(telegram_id: int, body: ProfileBody):
    save_user_profile(telegram_id, body.course, body.group, body.subgroup)
    return {"ok": True}


@app.get("/api/schedule/groups")
async def api_get_groups():
    now = datetime.now().timestamp()
    if GROUPS_CACHE["data"] and now - (GROUPS_CACHE["timestamp"] or 0) < SCHEDULE_TTL:
        return GROUPS_CACHE["data"]
    rows = await asyncio.to_thread(fetch_sheet_rows)
    data = get_available_groups(rows)
    GROUPS_CACHE["data"] = data
    GROUPS_CACHE["timestamp"] = now
    # ключи превращаем в строки для JSON
    return {str(k): v for k, v in data.items()}


@app.get("/api/schedule")
async def api_get_schedule(course: int, group: int):
    key = f"{course}_{group}"
    now = datetime.now().timestamp()
    entry = SCHEDULE_CACHE.get(key)
    if entry and now - entry["timestamp"] < SCHEDULE_TTL:
        return entry["data"]

    lessons = await asyncio.to_thread(parse_group_schedule, group, course)
    result = [
        {
            "day": l.day,
            "time": l.time,
            "sub1_num": l.sub1_num,
            "sub1_den": l.sub1_den,
            "sub2_num": l.sub2_num,
            "sub2_den": l.sub2_den,
        }
        for l in lessons
    ]
    SCHEDULE_CACHE[key] = {"data": result, "timestamp": now}
    return result


@app.get("/api/brs")
async def api_get_brs():
    if not BRS_USERNAME or not BRS_PASSWORD or not BRS_STUDENT_ID:
        raise HTTPException(status_code=503, detail="BRS credentials not configured")

    now = datetime.now().timestamp()
    if BRS_CACHE["data"] and now - (BRS_CACHE["timestamp"] or 0) < BRS_TTL:
        return BRS_CACHE["data"]

    rows = await asyncio.to_thread(get_brs_data, BRS_USERNAME, BRS_PASSWORD, BRS_STUDENT_ID)
    if not rows:
        raise HTTPException(status_code=502, detail="Failed to load BRS data")

    result = []
    for row in rows:
        penalty = get_attendance_penalty(row.weighted_score)
        result.append({
            "subject": row.subject,
            "semester": row.semester,
            "control": row.control,
            "teacher": row.teacher_short,
            "att1": row.att1,
            "att2": row.att2,
            "att3": row.att3,
            "attendance_pct": row.attendance_pct,
            "weighted_score": row.weighted_score,
            "exam_score": row.exam_score,
            "final_score": row.final_score,
            "final_text": row.final_text,
            "grade_icon": penalty["grade"],
            "grade_desc": penalty["description"],
            "lessons_url": row.lessons_url,
        })

    BRS_CACHE["data"] = result
    BRS_CACHE["timestamp"] = now
    return result


LESSONS_CACHE: dict = {}
LESSONS_TTL = 3600


@app.get("/api/brs/lessons")
async def api_get_lessons(lessons_url: str):
    """Возвращает статистику пар по lessons_url из БРС."""
    if not BRS_USERNAME or not BRS_PASSWORD:
        raise HTTPException(status_code=503, detail="BRS credentials not configured")

    now = datetime.now().timestamp()
    if lessons_url in LESSONS_CACHE:
        entry = LESSONS_CACHE[lessons_url]
        if now - entry["timestamp"] < LESSONS_TTL:
            return entry["data"]

    session = await asyncio.to_thread(brs_login, BRS_USERNAME, BRS_PASSWORD)
    stats = await asyncio.to_thread(fetch_lessons_stats, lessons_url, session)
    data = {"total": stats.total, "attended": stats.attended, "skipped": stats.skipped}
    LESSONS_CACHE[lessons_url] = {"data": data, "timestamp": now}
    return data


@app.get("/api/reminders/{telegram_id}")
def api_get_reminders(telegram_id: int):
    return get_reminder_settings(telegram_id)


@app.post("/api/reminders/{telegram_id}")
def api_save_reminders(telegram_id: int, body: ReminderBody):
    save_reminder_settings(telegram_id, body.enabled, body.minutes_before)
    return {"ok": True}


@app.post("/api/calc/attendance")
def api_calc_attendance(body: AttendanceCalcBody):
    result = simulate_attendance_change(
        current_pct=body.current_pct,
        classes_held=body.classes_held,
        future_total=body.future_total,
        skips=body.skips,
    )
    result["current_grade"] = grade_by_percentage(result["current_pct"])
    result["new_grade"] = grade_by_percentage(result["new_pct"])
    return result


# ─── Статика (webapp) ─────────────────────────────────────────────────────────

from starlette.staticfiles import StaticFiles as _SF
from starlette.responses import Response
from starlette.types import Scope, Receive, Send

class NoCacheStaticFiles(_SF):
    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        async def no_cache_send(message):
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers", []))
                headers[b"cache-control"] = b"no-store, max-age=0"
                message["headers"] = list(headers.items())
            await send(message)
        await super().__call__(scope, receive, no_cache_send)

_WEBAPP_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "webapp")
if os.path.isdir(_WEBAPP_DIR):
    app.mount("/", NoCacheStaticFiles(directory=_WEBAPP_DIR, html=True), name="webapp")
