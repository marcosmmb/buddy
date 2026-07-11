from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    default_currency: Mapped[str] = mapped_column(String(3), default="USD")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    memberships: Mapped[list[TrackerMember]] = relationship(back_populates="user", cascade="all, delete-orphan")
    sessions: Mapped[list[SessionToken]] = relationship(back_populates="user", cascade="all, delete-orphan")


class SessionToken(Base):
    __tablename__ = "session_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="sessions")


class Tracker(Base):
    __tablename__ = "trackers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    default_currency: Mapped[str] = mapped_column(String(3), default="USD")
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    members: Mapped[list[TrackerMember]] = relationship(back_populates="tracker", cascade="all, delete-orphan")
    categories: Mapped[list[Category]] = relationship(back_populates="tracker", cascade="all, delete-orphan")
    expenses: Mapped[list[Expense]] = relationship(back_populates="tracker", cascade="all, delete-orphan")


class TrackerMember(Base):
    __tablename__ = "tracker_members"
    __table_args__ = (UniqueConstraint("tracker_id", "user_id", name="uq_tracker_member"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tracker_id: Mapped[int] = mapped_column(ForeignKey("trackers.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(40), default="member")
    share_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"))

    tracker: Mapped[Tracker] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("tracker_id", "name", name="uq_tracker_category"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tracker_id: Mapped[int] = mapped_column(ForeignKey("trackers.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    color: Mapped[str] = mapped_column(String(20), default="#4677ff")

    tracker: Mapped[Tracker] = relationship(back_populates="categories")
    expenses: Mapped[list[Expense]] = relationship(back_populates="category")


class Expense(Base):
    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    tracker_id: Mapped[int] = mapped_column(ForeignKey("trackers.id", ondelete="CASCADE"), index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    paid_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    date: Mapped[date] = mapped_column(Date, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3))
    description: Mapped[str] = mapped_column(Text, default="")
    is_shared: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    tracker: Mapped[Tracker] = relationship(back_populates="expenses")
    category: Mapped[Category] = relationship(back_populates="expenses")
    paid_by: Mapped[User] = relationship()
