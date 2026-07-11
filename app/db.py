from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import Base, TrackerMember, User
from app.security import hash_password


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@contextmanager
def db_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_database() -> None:
    Base.metadata.create_all(engine)
    ensure_user_columns()
    with db_session() as session:
        admin = session.query(User).filter(User.email == settings.admin_email.lower()).one_or_none()
        if admin is None:
            admin = User(
                email=settings.admin_email.lower(),
                name=settings.admin_name,
                password_hash=hash_password(settings.admin_password),
                default_currency="USD",
                theme="light",
                is_admin=True,
                is_active=True,
            )
            session.add(admin)
        elif not admin.is_admin:
            admin.is_admin = True
            admin.is_active = True

        # Touch the class so SQLAlchemy imports the membership table before metadata creation in frozen reloads.
        _ = TrackerMember.__tablename__


def ensure_user_columns() -> None:
    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("users")}
    statements: list[str] = []
    if "theme" not in columns:
        statements.append("ALTER TABLE users ADD COLUMN theme VARCHAR(12) NOT NULL DEFAULT 'light'")
    if "is_active" not in columns:
        if engine.dialect.name == "postgresql":
            statements.append("ALTER TABLE users ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT true")
        else:
            statements.append("ALTER TABLE users ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1")
    if not statements:
        return
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
