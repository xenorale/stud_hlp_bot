from typing import Optional
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from database.models import Base, User, ReminderSettings
engine = create_async_engine('sqlite+aiosqlite:///bot.db', connect_args={'check_same_thread': False})
SessionLocal = async_sessionmaker(bind=engine, autocommit=False, autoflush=False, class_=AsyncSession)

async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with engine.connect() as conn:
        for col, typedef in [('course_number', 'INTEGER'), ('group_number', 'INTEGER'), ('subgroup_number', 'INTEGER'), ('brs_username', 'VARCHAR'), ('brs_password', 'VARCHAR'), ('brs_cookies', 'VARCHAR')]:
            try:
                await conn.execute(text(f'ALTER TABLE users ADD COLUMN {col} {typedef}'))
                await conn.commit()
            except Exception:
                pass

async def get_user_profile(telegram_id: int) -> Optional[dict]:
    async with SessionLocal() as session:
        result = await session.execute(select(User).filter_by(telegram_id=telegram_id))
        user = result.scalar_one_or_none()
        if not user or user.course_number is None or user.group_number is None:
            return None
        return {'course': user.course_number, 'group': user.group_number, 'subgroup': user.subgroup_number or 0, 'brs_username': user.brs_username, 'brs_password': user.brs_password, 'student_id': user.student_id}

async def save_user_profile(telegram_id: int, course: int, group: int, subgroup: int=0) -> None:
    async with SessionLocal() as session:
        result = await session.execute(select(User).filter_by(telegram_id=telegram_id))
        user = result.scalar_one_or_none()
        if user:
            user.course_number = course
            user.group_number = group
            user.subgroup_number = subgroup
        else:
            user = User(telegram_id=telegram_id, course_number=course, group_number=group, subgroup_number=subgroup)
            session.add(user)
        await session.commit()

async def save_brs_credentials(telegram_id: int, username: str, password: str, student_id: str) -> None:
    async with SessionLocal() as session:
        result = await session.execute(select(User).filter_by(telegram_id=telegram_id))
        user = result.scalar_one_or_none()
        if user:
            user.brs_username = username
            user.brs_password = password
            user.student_id = student_id
        else:
            user = User(telegram_id=telegram_id, brs_username=username, brs_password=password, student_id=student_id)
            session.add(user)
        await session.commit()

async def get_reminder_settings(telegram_id: int) -> dict:
    async with SessionLocal() as session:
        result = await session.execute(select(ReminderSettings).filter_by(telegram_id=telegram_id))
        s = result.scalar_one_or_none()
        if not s:
            return {'enabled': False, 'minutes_before': 15}
        return {'enabled': bool(s.enabled), 'minutes_before': s.minutes_before}

async def save_reminder_settings(telegram_id: int, enabled: bool, minutes_before: int) -> None:
    async with SessionLocal() as session:
        result = await session.execute(select(ReminderSettings).filter_by(telegram_id=telegram_id))
        s = result.scalar_one_or_none()
        if s:
            s.enabled = enabled
            s.minutes_before = minutes_before
        else:
            s = ReminderSettings(telegram_id=telegram_id, enabled=enabled, minutes_before=minutes_before)
            session.add(s)
        await session.commit()

async def get_all_brs_users() -> list:
    async with SessionLocal() as session:
        result = await session.execute(text('SELECT telegram_id, brs_username, brs_password, student_id FROM users WHERE brs_username IS NOT NULL AND brs_password IS NOT NULL'))
        return [{'telegram_id': row[0], 'brs_username': row[1], 'brs_password': row[2], 'student_id': row[3]} for row in result.all()]

async def get_reminder_users() -> list:
    async with SessionLocal() as session:
        result = await session.execute(select(ReminderSettings).filter_by(enabled=True))
        settings = result.scalars().all()
        users_result = []
        for s in settings:
            u_result = await session.execute(select(User).filter_by(telegram_id=s.telegram_id))
            user = u_result.scalar_one_or_none()
            if user and user.course_number and user.group_number:
                users_result.append({'telegram_id': s.telegram_id, 'minutes_before': s.minutes_before, 'course': user.course_number, 'group': user.group_number, 'subgroup': user.subgroup_number or 0})
        return users_result

async def get_brs_cookies(telegram_id: int) -> Optional[str]:
    async with SessionLocal() as session:
        result = await session.execute(select(User).filter_by(telegram_id=telegram_id))
        user = result.scalar_one_or_none()
        if user and user.brs_cookies:
            return user.brs_cookies
        return None

async def save_brs_cookies(telegram_id: int, cookies_json: str) -> None:
    async with SessionLocal() as session:
        result = await session.execute(select(User).filter_by(telegram_id=telegram_id))
        user = result.scalar_one_or_none()
        if user:
            user.brs_cookies = cookies_json
            await session.commit()