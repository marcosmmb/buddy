from __future__ import annotations

from typing import Annotated, Any

from litestar import Controller, Request, delete, get, post
from litestar.exceptions import HTTPException
from litestar.params import Body
from sqlalchemy.orm import joinedload

from app.banking.plaid import PlaidClient
from app.banking.service import (
    create_bank_connection,
    import_bank_transactions,
    list_review_bank_transactions,
    load_bank_connection_for_user,
    normalized_review_days,
    serialize_bank_connection,
    serialize_bank_transaction,
    sync_bank_connection,
)
from app.config import settings
from app.db import db_session
from app.models import BankConnection, User
from app.schemas import BankLinkTokenPayload, BankTokenExchangePayload, BankTransactionImportPayload
from app.services import get_tracker_for_user
from app.two_factor import consume_bank_link_challenge, create_bank_link_challenge, verify_user_totp
from app.utils import require_user


def require_bank_link_two_factor(user: User, code: str | None) -> None:
    if not user.two_factor_enabled:
        raise HTTPException(status_code=403, detail="Enable 2FA before connecting a bank account")
    if not code or not verify_user_totp(user, code):
        raise HTTPException(status_code=401, detail="Enter a valid 2FA code to connect a bank account")


class BankingController(Controller):
    path = "/api/trackers/{tracker_id:int}/bank"

    @get("/config")
    def config(self, request: Request, tracker_id: int) -> dict[str, Any]:
        user = require_user(request)
        with db_session() as session:
            if get_tracker_for_user(session, tracker_id, user) is None:
                raise HTTPException(status_code=404, detail="Tracker not found")
        return {"plaid_configured": settings.plaid_configured, "plaid_env": settings.plaid_env}

    @post("/link-token")
    def link_token(self, request: Request, tracker_id: int, data: Annotated[BankLinkTokenPayload | None, Body()] = None) -> dict[str, str]:
        user = require_user(request)
        if not settings.plaid_configured:
            raise HTTPException(status_code=400, detail="Plaid is not configured")
        with db_session() as session:
            tracker = get_tracker_for_user(session, tracker_id, user)
            if tracker is None:
                raise HTTPException(status_code=404, detail="Tracker not found")
            db_user = session.get(User, user.id)
            if db_user is None:
                raise HTTPException(status_code=404, detail="User not found")
            require_bank_link_two_factor(db_user, data.two_factor_code if data is not None else None)
            bank_link_challenge = create_bank_link_challenge(session, db_user)
            return {"link_token": PlaidClient().create_link_token(user, tracker), "bank_link_token": bank_link_challenge.token}

    @post("/exchange-token")
    def exchange_token(self, request: Request, tracker_id: int, data: Annotated[BankTokenExchangePayload, Body()]) -> dict[str, Any]:
        user = require_user(request)
        with db_session() as session:
            tracker = get_tracker_for_user(session, tracker_id, user)
            if tracker is None:
                raise HTTPException(status_code=404, detail="Tracker not found")
            if not consume_bank_link_challenge(session, user, data.bank_link_token):
                raise HTTPException(status_code=401, detail="Verify 2FA before connecting a bank account")
            connection = create_bank_connection(session, tracker, user, data.public_token, data.institution_name)
            session.flush()
            connection = (
                session.query(BankConnection)
                .options(joinedload(BankConnection.accounts))
                .filter(BankConnection.id == connection.id)
                .one()
            )
            return serialize_bank_connection(connection)

    @get("/connections")
    def connections(self, request: Request, tracker_id: int) -> list[dict[str, Any]]:
        user = require_user(request)
        with db_session() as session:
            if get_tracker_for_user(session, tracker_id, user) is None:
                raise HTTPException(status_code=404, detail="Tracker not found")
            rows = (
                session.query(BankConnection)
                .options(joinedload(BankConnection.accounts))
                .filter(BankConnection.tracker_id == tracker_id)
                .order_by(BankConnection.created_at.desc())
                .all()
            )
            return [serialize_bank_connection(connection) for connection in rows]

    @post("/connections/{connection_id:int}/sync")
    def sync_connection(self, request: Request, tracker_id: int, connection_id: int, days: int = 30) -> dict[str, Any]:
        user = require_user(request)
        with db_session() as session:
            connection = load_bank_connection_for_user(session, tracker_id, connection_id, user)
            counts = sync_bank_connection(session, connection)
            return {"status": "ok", "days": normalized_review_days(days), **counts}

    @delete("/connections/{connection_id:int}", status_code=200)
    def delete_connection(self, request: Request, tracker_id: int, connection_id: int) -> dict[str, str]:
        user = require_user(request)
        with db_session() as session:
            connection = load_bank_connection_for_user(session, tracker_id, connection_id, user)
            session.delete(connection)
            return {"status": "ok"}

    @get("/transactions")
    def transactions(self, request: Request, tracker_id: int, days: int = 30) -> list[dict[str, Any]]:
        user = require_user(request)
        with db_session() as session:
            tracker = get_tracker_for_user(session, tracker_id, user)
            if tracker is None:
                raise HTTPException(status_code=404, detail="Tracker not found")
            default_user_by_connection = {connection.id: connection.user for connection in tracker.bank_connections}
            return [
                serialize_bank_transaction(transaction, default_user_by_connection.get(transaction.account.bank_connection_id, user))
                for transaction in list_review_bank_transactions(session, tracker_id, days)
            ]

    @post("/transactions/import")
    def import_transactions(self, request: Request, tracker_id: int, data: Annotated[BankTransactionImportPayload, Body()]) -> dict[str, Any]:
        user = require_user(request)
        with db_session() as session:
            tracker = get_tracker_for_user(session, tracker_id, user)
            if tracker is None:
                raise HTTPException(status_code=404, detail="Tracker not found")
            return import_bank_transactions(session, tracker, user, data.transactions)
