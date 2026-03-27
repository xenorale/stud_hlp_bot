from typing import Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from database.models import Base, User

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
