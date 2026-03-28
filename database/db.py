from typing import Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from database.models import Base, User, ReminderSettings

engine = create_engine(
    "sqlite:///bot.db",
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def init_db() -> None:
    """Создаёт таблицы и мигрирует новые колонки если нужно."""
    Base.metadata.create_all(engine)
    # Мягкая миграция: добавляем колонки если их нет (SQLite не поддерживает IF NOT EXISTS для колонок)
    with engine.connect() as conn:
        for col, typedef in [
            ("course_number", "INTEGER"),
            ("group_number", "INTEGER"),
            ("subgroup_number", "INTEGER"),
        ]:
            try:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} {typedef}"))
                conn.commit()
            except Exception:
                pass  # колонка уже существует


def get_user_profile(telegram_id: int) -> Optional[dict]:
    """Возвращает профиль пользователя или None если не настроен."""
    session = SessionLocal()
    try:
        user = session.query(User).filter_by(telegram_id=telegram_id).first()
        if not user or user.course_number is None or user.group_number is None:
            return None
        return {
            "course": user.course_number,
            "group": user.group_number,
            "subgroup": user.subgroup_number or 0,
        }
    finally:
        session.close()


def save_user_profile(telegram_id: int, course: int, group: int, subgroup: int = 0) -> None:
    """Сохраняет или обновляет профиль пользователя."""
    session = SessionLocal()
    try:
        user = session.query(User).filter_by(telegram_id=telegram_id).first()
        if user:
            user.course_number = course
            user.group_number = group
            user.subgroup_number = subgroup
        else:
            user = User(
                telegram_id=telegram_id,
                course_number=course,
                group_number=group,
                subgroup_number=subgroup,
            )
            session.add(user)
        session.commit()
    finally:
        session.close()


def get_reminder_settings(telegram_id: int) -> dict:
    """Возвращает настройки напоминаний пользователя."""
    session = SessionLocal()
    try:
        s = session.query(ReminderSettings).filter_by(telegram_id=telegram_id).first()
        if not s:
            return {"enabled": False, "minutes_before": 15}
        return {"enabled": bool(s.enabled), "minutes_before": s.minutes_before}
    finally:
        session.close()


def save_reminder_settings(telegram_id: int, enabled: bool, minutes_before: int) -> None:
    """Сохраняет настройки напоминаний."""
    session = SessionLocal()
    try:
        s = session.query(ReminderSettings).filter_by(telegram_id=telegram_id).first()
        if s:
            s.enabled = enabled
            s.minutes_before = minutes_before
        else:
            s = ReminderSettings(telegram_id=telegram_id, enabled=enabled, minutes_before=minutes_before)
            session.add(s)
        session.commit()
    finally:
        session.close()


def get_reminder_users() -> list:
    """Возвращает всех пользователей с включёнными напоминаниями + их профиль."""
    session = SessionLocal()
    try:
        settings = session.query(ReminderSettings).filter_by(enabled=True).all()
        result = []
        for s in settings:
            user = session.query(User).filter_by(telegram_id=s.telegram_id).first()
            if user and user.course_number and user.group_number:
                result.append({
                    "telegram_id": s.telegram_id,
                    "minutes_before": s.minutes_before,
                    "course": user.course_number,
                    "group": user.group_number,
                    "subgroup": user.subgroup_number or 0,
                })
        return result
    finally:
        session.close()
