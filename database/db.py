from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import Base

engine = create_engine(
    "sqlite:///bot.db",
    connect_args={"check_same_thread": False},  # нужно для SQLite + async окружения
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def init_db() -> None:
    """Создаёт все таблицы если их ещё нет. Вызывать один раз при старте бота."""
    Base.metadata.create_all(engine)


def get_session():
    """Контекстный менеджер сессии для использования в обработчиках."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
