from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta
from decimal import Decimal

from litestar.exceptions import HTTPException
from sqlalchemy.orm import Session

from app.models import BankAccount, BankConnection, BankTransaction, Category, Expense, ExpenseDeletion, TrackerMember, User, utcnow


def delete_with_undo(session: Session, tracker_id: int, user_id: int, expense_ids: list[int]) -> dict:
    now = utcnow()
    session.query(ExpenseDeletion).filter(ExpenseDeletion.expires_at <= now).delete(synchronize_session=False)
    rows = session.query(Expense).filter(Expense.tracker_id == tracker_id, Expense.id.in_(expense_ids)).all()
    if not rows:
        return {"deleted": 0}
    snapshots = []
    for row in rows:
        snapshots.append({
            "category_id": row.category_id, "paid_by_id": row.paid_by_id,
            "date": row.date.isoformat(), "amount": str(row.amount), "currency": row.currency,
            "description": row.description, "is_shared": row.is_shared, "created_at": row.created_at.isoformat(),
            "bank_transaction_ids": [transaction.provider_transaction_id for transaction in row.bank_transactions],
        })
        session.delete(row)
    token = secrets.token_urlsafe(32)
    expires_at = now + timedelta(minutes=10)
    session.add(ExpenseDeletion(token=token, tracker_id=tracker_id, user_id=user_id, expenses=snapshots, expires_at=expires_at))
    session.flush()
    return {"deleted": len(rows), "undo_token": token, "undo_expires_at": expires_at.isoformat()}


def restore_deletion(session: Session, tracker_id: int, user_id: int, token: str) -> dict:
    query = session.query(ExpenseDeletion).filter(
        ExpenseDeletion.token == token, ExpenseDeletion.tracker_id == tracker_id,
        ExpenseDeletion.user_id == user_id, ExpenseDeletion.expires_at > utcnow(),
    )
    deletion = query.one_or_none()
    if deletion is None:
        raise HTTPException(status_code=404, detail="Undo expired or these expenses were already restored.")
    snapshots = deletion.expenses
    # Claim the token in the same transaction as restoration; concurrent undo is single-use.
    if query.delete(synchronize_session=False) != 1:
        raise HTTPException(status_code=409, detail="These expenses were already restored.")
    for saved in snapshots:
        category = session.query(Category).filter(Category.id == saved["category_id"], Category.tracker_id == tracker_id).one_or_none()
        member = session.query(TrackerMember).filter(TrackerMember.tracker_id == tracker_id, TrackerMember.user_id == saved["paid_by_id"]).one_or_none()
        if category is None or member is None or session.get(User, saved["paid_by_id"]) is None:
            raise HTTPException(status_code=409, detail="Cannot undo: a category or tracker member has been removed.")
        expense = Expense(
            tracker_id=tracker_id, category_id=saved["category_id"], paid_by_id=saved["paid_by_id"],
            date=date.fromisoformat(saved["date"]), amount=Decimal(saved["amount"]), currency=saved["currency"],
            description=saved["description"], is_shared=saved["is_shared"], created_at=datetime.fromisoformat(saved["created_at"]),
        )
        session.add(expense)
        session.flush()
        linked = session.query(BankTransaction).join(BankAccount).join(BankConnection).filter(
            BankConnection.tracker_id == tracker_id,
            BankTransaction.provider_transaction_id.in_(saved["bank_transaction_ids"]),
        ).all()
        if any(transaction.expense_id is not None for transaction in linked):
            raise HTTPException(status_code=409, detail="Cannot undo: a bank transaction has already been imported again.")
        for transaction in linked:
            transaction.expense_id = expense.id
    return {"restored": len(snapshots)}
