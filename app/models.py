from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, JSON, Numeric, String, Text, UniqueConstraint
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
    theme: Mapped[str] = mapped_column(String(12), default="light")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    two_factor_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    two_factor_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    two_factor_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    memberships: Mapped[list[TrackerMember]] = relationship(back_populates="user", cascade="all, delete-orphan")
    sessions: Mapped[list[SessionToken]] = relationship(back_populates="user", cascade="all, delete-orphan")
    two_factor_challenges: Mapped[list[TwoFactorChallenge]] = relationship(back_populates="user", cascade="all, delete-orphan")
    monthly_shares: Mapped[list[TrackerMonthlyShare]] = relationship(back_populates="user", cascade="all, delete-orphan")
    bank_connections: Mapped[list[BankConnection]] = relationship(back_populates="user", cascade="all, delete-orphan")


class SessionToken(Base):
    __tablename__ = "session_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="sessions")


class TwoFactorChallenge(Base):
    __tablename__ = "two_factor_challenges"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(40), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="two_factor_challenges")


class Tracker(Base):
    __tablename__ = "trackers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    default_currency: Mapped[str] = mapped_column(String(3), default="USD")
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    members: Mapped[list[TrackerMember]] = relationship(back_populates="tracker", cascade="all, delete-orphan")
    monthly_shares: Mapped[list[TrackerMonthlyShare]] = relationship(back_populates="tracker", cascade="all, delete-orphan")
    categories: Mapped[list[Category]] = relationship(back_populates="tracker", cascade="all, delete-orphan")
    expenses: Mapped[list[Expense]] = relationship(back_populates="tracker", cascade="all, delete-orphan")
    csv_configs: Mapped[list[CsvImportConfig]] = relationship(back_populates="tracker", cascade="all, delete-orphan")
    bank_connections: Mapped[list[BankConnection]] = relationship(back_populates="tracker", cascade="all, delete-orphan")


class TrackerMember(Base):
    __tablename__ = "tracker_members"
    __table_args__ = (UniqueConstraint("tracker_id", "user_id", name="uq_tracker_member"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tracker_id: Mapped[int] = mapped_column(ForeignKey("trackers.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(40), default="member")
    share_percent: Mapped[Decimal] = mapped_column(Numeric(9, 6), default=Decimal("0"))

    tracker: Mapped[Tracker] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")


class TrackerMonthlyShare(Base):
    __tablename__ = "tracker_monthly_shares"
    __table_args__ = (UniqueConstraint("tracker_id", "user_id", "month", name="uq_tracker_monthly_share"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tracker_id: Mapped[int] = mapped_column(ForeignKey("trackers.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    month: Mapped[str] = mapped_column(String(7), index=True)
    share_percent: Mapped[Decimal] = mapped_column(Numeric(9, 6), default=Decimal("0"))

    tracker: Mapped[Tracker] = relationship(back_populates="monthly_shares")
    user: Mapped[User] = relationship(back_populates="monthly_shares")


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("tracker_id", "name", name="uq_tracker_category"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tracker_id: Mapped[int] = mapped_column(ForeignKey("trackers.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    color: Mapped[str] = mapped_column(String(20), default="#f1b84b")

    tracker: Mapped[Tracker] = relationship(back_populates="categories")
    expenses: Mapped[list[Expense]] = relationship(back_populates="category")


class Expense(Base):
    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    tracker_id: Mapped[int] = mapped_column(ForeignKey("trackers.id", ondelete="CASCADE"), index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    paid_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    date: Mapped[date] = mapped_column(Date, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    currency: Mapped[str] = mapped_column(String(3))
    description: Mapped[str] = mapped_column(Text, default="")
    is_shared: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    tracker: Mapped[Tracker] = relationship(back_populates="expenses")
    category: Mapped[Category] = relationship(back_populates="expenses")
    paid_by: Mapped[User] = relationship()
    bank_transactions: Mapped[list[BankTransaction]] = relationship(back_populates="expense")


class CsvImportConfig(Base):
    __tablename__ = "csv_import_configs"
    __table_args__ = (UniqueConstraint("tracker_id", "name", name="uq_tracker_csv_config"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tracker_id: Mapped[int] = mapped_column(ForeignKey("trackers.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    field_map: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    invert_amount: Mapped[bool] = mapped_column(Boolean, default=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    tracker: Mapped[Tracker] = relationship(back_populates="csv_configs")
    created_by: Mapped[User] = relationship()


class BankConnection(Base):
    __tablename__ = "bank_connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    tracker_id: Mapped[int] = mapped_column(ForeignKey("trackers.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(40), default="plaid")
    provider_item_id: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    institution_name: Mapped[str] = mapped_column(String(160), default="Bank")
    encrypted_access_token: Mapped[str] = mapped_column(Text)
    sync_cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="active")
    error_message: Mapped[str] = mapped_column(Text, default="")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    tracker: Mapped[Tracker] = relationship(back_populates="bank_connections")
    user: Mapped[User] = relationship(back_populates="bank_connections")
    accounts: Mapped[list[BankAccount]] = relationship(back_populates="connection", cascade="all, delete-orphan")


class BankAccount(Base):
    __tablename__ = "bank_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    bank_connection_id: Mapped[int] = mapped_column(ForeignKey("bank_connections.id", ondelete="CASCADE"), index=True)
    provider_account_id: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    mask: Mapped[str] = mapped_column(String(20), default="")
    type: Mapped[str] = mapped_column(String(40), default="")
    subtype: Mapped[str] = mapped_column(String(40), default="")
    currency: Mapped[str] = mapped_column(String(3), default="CAD")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    connection: Mapped[BankConnection] = relationship(back_populates="accounts")
    transactions: Mapped[list[BankTransaction]] = relationship(back_populates="account", cascade="all, delete-orphan")


class BankTransaction(Base):
    __tablename__ = "bank_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    bank_account_id: Mapped[int] = mapped_column(ForeignKey("bank_accounts.id", ondelete="CASCADE"), index=True)
    provider_transaction_id: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    authorized_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    name: Mapped[str] = mapped_column(Text, default="")
    merchant_name: Mapped[str] = mapped_column(String(180), default="")
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    currency: Mapped[str] = mapped_column(String(3), default="CAD")
    pending: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(40), default="ready")
    expense_id: Mapped[int | None] = mapped_column(ForeignKey("expenses.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    account: Mapped[BankAccount] = relationship(back_populates="transactions")
    expense: Mapped[Expense | None] = relationship(back_populates="bank_transactions")
