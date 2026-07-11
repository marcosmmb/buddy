from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
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
    with db_session() as session:
        admin = session.query(User).filter(User.email == settings.admin_email.lower()).one_or_none()
        if admin is None:
            admin = User(
                email=settings.admin_email.lower(),
                name=settings.admin_name,
                password_hash=hash_password(settings.admin_password),
                default_currency="USD",
                is_admin=True,
            )
            session.add(admin)
        elif not admin.is_admin:
            admin.is_admin = True

        # Touch the class so SQLAlchemy imports the membership table before metadata creation in frozen reloads.
        _ = TrackerMember.__tablename__
