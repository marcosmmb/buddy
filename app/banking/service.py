from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from litestar.exceptions import HTTPException
from sqlalchemy.orm import Session, joinedload

from app.banking.crypto import decrypt_token, encrypt_token
from app.banking.plaid import PlaidApiError, PlaidClient
from app.models import BankAccount, BankConnection, BankTransaction, Category, Expense, Tracker, User, utcnow
from app.schemas import BankTransactionImportItem
from app.services import get_tracker_for_user


def parse_plaid_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def plaid_currency(transaction: dict[str, Any], account: BankAccount | None = None, fallback: str = "CAD") -> str:
    return transaction.get("iso_currency_code") or transaction.get("unofficial_currency_code") or (account.currency if account is not None else None) or fallback


def serialize_bank_connection(connection: BankConnection) -> dict[str, Any]:
    return {
        "id": connection.id,
        "tracker_id": connection.tracker_id,
        "user_id": connection.user_id,
        "provider": connection.provider,
        "institution_name": connection.institution_name,
        "status": connection.status,
        "error_message": connection.error_message,
        "last_synced_at": connection.last_synced_at.isoformat() if connection.last_synced_at else None,
        "accounts": [serialize_bank_account(account) for account in connection.accounts],
    }


def serialize_bank_account(account: BankAccount) -> dict[str, Any]:
    return {
        "id": account.id,
        "connection_id": account.bank_connection_id,
        "name": account.name,
        "mask": account.mask,
        "type": account.type,
        "subtype": account.subtype,
        "currency": account.currency,
        "enabled": account.enabled,
    }


def serialize_bank_transaction(transaction: BankTransaction, default_paid_by: User) -> dict[str, Any]:
    return {
        "id": transaction.id,
        "connection_id": transaction.account.bank_connection_id,
        "account_id": transaction.bank_account_id,
        "account": transaction.account.name,
        "institution_name": transaction.account.connection.institution_name,
        "date": transaction.date.isoformat(),
        "authorized_date": transaction.authorized_date.isoformat() if transaction.authorized_date else None,
        "name": transaction.name,
        "merchant_name": transaction.merchant_name,
        "amount": float(transaction.amount),
        "currency": transaction.currency,
        "pending": transaction.pending,
        "status": transaction.status,
        "expense_id": transaction.expense_id,
        "default_paid_by_id": default_paid_by.id,
        "default_paid_by": default_paid_by.name,
        "description": transaction.merchant_name or transaction.name,
    }


def load_bank_connection_for_user(session: Session, tracker_id: int, connection_id: int, user: User) -> BankConnection:
    if get_tracker_for_user(session, tracker_id, user) is None:
        raise HTTPException(status_code=404, detail="Tracker not found")
    connection = (
        session.query(BankConnection)
        .options(joinedload(BankConnection.accounts))
        .filter(BankConnection.id == connection_id, BankConnection.tracker_id == tracker_id)
        .one_or_none()
    )
    if connection is None:
        raise HTTPException(status_code=404, detail="Bank connection not found")
    return connection


def normalized_review_days(days: int) -> int:
    return min(max(days, 1), 730)


def list_review_bank_transactions(session: Session, tracker_id: int, days: int) -> list[BankTransaction]:
    cutoff = utcnow().date() - timedelta(days=normalized_review_days(days) - 1)
    return (
        session.query(BankTransaction)
        .options(joinedload(BankTransaction.account).joinedload(BankAccount.connection))
        .join(BankAccount)
        .join(BankConnection)
        .filter(
            BankConnection.tracker_id == tracker_id,
            BankTransaction.date >= cutoff,
            BankTransaction.amount > 0,
            BankTransaction.expense_id.is_(None),
            BankTransaction.status.notin_(["imported", "pending", "removed"]),
        )
        .order_by(BankTransaction.date.desc(), BankTransaction.id.desc())
        .all()
    )


def create_bank_connection(
    session: Session,
    tracker: Tracker,
    user: User,
    public_token: str,
    institution_name: str,
    plaid_client: PlaidClient | None = None,
) -> BankConnection:
    plaid_client = plaid_client or PlaidClient()
    token_data = plaid_client.exchange_public_token(public_token)
    access_token = token_data["access_token"]
    item_id = token_data["item_id"]
    connection = session.query(BankConnection).filter(BankConnection.provider_item_id == item_id).one_or_none()
    if connection is None:
        connection = BankConnection(
            tracker_id=tracker.id,
            user_id=user.id,
            provider="plaid",
            provider_item_id=item_id,
            institution_name=institution_name.strip() or "Bank",
            encrypted_access_token=encrypt_token(access_token),
        )
        session.add(connection)
    else:
        connection.tracker_id = tracker.id
        connection.user_id = user.id
        connection.institution_name = institution_name.strip() or connection.institution_name
        connection.encrypted_access_token = encrypt_token(access_token)
        connection.status = "active"
        connection.error_message = ""
    session.flush()
    upsert_accounts(session, connection, plaid_client.get_accounts(access_token))
    sync_bank_connection(session, connection, plaid_client)
    return connection


def upsert_accounts(session: Session, connection: BankConnection, accounts: list[dict[str, Any]]) -> None:
    existing = {
        account.provider_account_id: account
        for account in session.query(BankAccount).filter(BankAccount.bank_connection_id == connection.id).all()
    }
    for row in accounts:
        provider_account_id = row["account_id"]
        account = existing.get(provider_account_id)
        if account is None:
            account = BankAccount(bank_connection_id=connection.id, provider_account_id=provider_account_id, name=row.get("name") or "Account")
            session.add(account)
        account.name = row.get("name") or account.name
        account.mask = row.get("mask") or ""
        account.type = row.get("type") or ""
        account.subtype = row.get("subtype") or ""
        account.currency = row.get("balances", {}).get("iso_currency_code") or row.get("balances", {}).get("unofficial_currency_code") or "CAD"
    session.flush()


def sync_bank_connection(session: Session, connection: BankConnection, plaid_client: PlaidClient | None = None) -> dict[str, int]:
    plaid_client = plaid_client or PlaidClient()
    access_token = decrypt_token(connection.encrypted_access_token)
    account_by_provider_id = {
        account.provider_account_id: account
        for account in session.query(BankAccount).filter(BankAccount.bank_connection_id == connection.id).all()
    }
    old_cursor = connection.sync_cursor
    cursor = connection.sync_cursor
    counts = {"added": 0, "modified": 0, "removed": 0}

    while True:
        try:
            data = plaid_client.sync_transactions(access_token, cursor)
        except PlaidApiError as exc:
            if exc.error_code == "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION" and cursor != old_cursor:
                cursor = old_cursor
                continue
            connection.status = "error"
            connection.error_message = str(exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        for transaction in data.get("added", []):
            upsert_transaction(session, connection, account_by_provider_id, transaction)
            counts["added"] += 1
        for transaction in data.get("modified", []):
            upsert_transaction(session, connection, account_by_provider_id, transaction)
            counts["modified"] += 1
        for removed in data.get("removed", []):
            db_transaction = (
                session.query(BankTransaction)
                .filter(BankTransaction.provider_transaction_id == removed.get("transaction_id"))
                .one_or_none()
            )
            if db_transaction is not None:
                db_transaction.status = "removed"
                counts["removed"] += 1
        cursor = data.get("next_cursor", cursor)
        if not data.get("has_more"):
            break

    connection.sync_cursor = cursor
    connection.status = "active"
    connection.error_message = ""
    connection.last_synced_at = utcnow()
    session.flush()
    return counts


def upsert_transaction(
    session: Session,
    connection: BankConnection,
    account_by_provider_id: dict[str, BankAccount],
    transaction: dict[str, Any],
) -> BankTransaction:
    account = account_by_provider_id.get(transaction["account_id"])
    if account is None:
        account = BankAccount(
            bank_connection_id=connection.id,
            provider_account_id=transaction["account_id"],
            name="Account",
            currency=transaction.get("iso_currency_code") or "CAD",
        )
        session.add(account)
        session.flush()
        account_by_provider_id[account.provider_account_id] = account

    db_transaction = (
        session.query(BankTransaction)
        .filter(BankTransaction.provider_transaction_id == transaction["transaction_id"])
        .one_or_none()
    )
    amount = Decimal(str(transaction.get("amount", "0")))
    pending = bool(transaction.get("pending"))
    status = "pending" if pending else "ready"
    if db_transaction is None:
        db_transaction = BankTransaction(
            bank_account_id=account.id,
            provider_transaction_id=transaction["transaction_id"],
            date=parse_plaid_date(transaction.get("date")) or utcnow().date(),
            amount=amount,
            currency=plaid_currency(transaction, account),
            status=status,
        )
        session.add(db_transaction)
    elif db_transaction.status not in {"imported", "removed"}:
        db_transaction.status = status
    db_transaction.bank_account_id = account.id
    db_transaction.date = parse_plaid_date(transaction.get("date")) or db_transaction.date
    db_transaction.authorized_date = parse_plaid_date(transaction.get("authorized_date"))
    db_transaction.name = transaction.get("name") or ""
    db_transaction.merchant_name = transaction.get("merchant_name") or ""
    db_transaction.amount = amount
    db_transaction.currency = plaid_currency(transaction, account)
    db_transaction.pending = pending
    db_transaction.raw_payload = transaction
    return db_transaction


def import_bank_transactions(
    session: Session,
    tracker: Tracker,
    user: User,
    items: list[BankTransactionImportItem],
) -> dict[str, Any]:
    imported = 0
    skipped: list[dict[str, Any]] = []
    member_ids = {member.user_id for member in tracker.members}
    payload_by_id = {item.transaction_id: item for item in items}
    transactions = (
        session.query(BankTransaction)
        .options(
            joinedload(BankTransaction.account).joinedload(BankAccount.connection),
            joinedload(BankTransaction.expense),
        )
        .filter(BankTransaction.id.in_(payload_by_id.keys()))
        .all()
    )
    for transaction in transactions:
        payload = payload_by_id[transaction.id]
        try:
            if transaction.account.connection.tracker_id != tracker.id:
                raise ValueError("Transaction does not belong to this tracker")
            if transaction.expense_id is not None or transaction.status == "imported":
                raise ValueError("Transaction is already tracked")
            if transaction.status == "removed":
                raise ValueError("Transaction was removed by the bank")
            if transaction.pending or transaction.status == "pending":
                raise ValueError("Pending transaction cannot be imported yet")
            if Decimal(transaction.amount) <= 0:
                raise ValueError("Only outgoing transactions can be imported as expenses")
            category = session.get(Category, payload.category_id)
            if category is None or category.tracker_id != tracker.id:
                raise ValueError("Category must belong to this tracker")
            paid_by_id = payload.paid_by_id or user.id
            if paid_by_id not in member_ids:
                raise ValueError("Payer must be a tracker member")
            expense = Expense(
                tracker_id=tracker.id,
                category_id=payload.category_id,
                paid_by_id=paid_by_id,
                date=transaction.date,
                amount=transaction.amount,
                currency=tracker.default_currency,
                description=(payload.description if payload.description is not None else transaction.merchant_name or transaction.name).strip(),
                is_shared=payload.is_shared,
            )
            session.add(expense)
            session.flush()
            transaction.expense_id = expense.id
            transaction.status = "imported"
            imported += 1
        except Exception as exc:
            skipped.append({"transaction_id": transaction.id, "reason": str(exc)})
    session.flush()
    return {"imported": imported, "skipped": skipped}
