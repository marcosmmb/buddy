from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from litestar.exceptions import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Category, User
from app.routes.banking import require_bank_link_two_factor
from app.schemas import ExpenseCreatePayload, RegisterPayload
from app.security import hash_password, new_token, verify_password
from app.two_factor import (
    consume_bank_link_challenge,
    consume_login_challenge,
    create_bank_link_challenge,
    create_login_challenge,
    encrypt_totp_secret,
    generate_totp_secret,
    provisioning_qr_svg,
    totp_code,
    verify_totp_code,
)
from app.utils import normalize_currency, normalize_month, require_admin, validate_expense_payload, validate_share_total
from tests.helpers import make_category, make_member, make_tracker, make_user


class FakeSession:
    def __init__(self, category: Category | None) -> None:
        self.category = category

    def get(self, model: type[object], object_id: int) -> object | None:
        if model is Category and self.category is not None and self.category.id == object_id:
            return self.category
        return None


class SecurityTests(unittest.TestCase):
    def test_password_hash_round_trip_and_rejects_bad_input(self) -> None:
        encoded = hash_password("correct horse")

        self.assertTrue(verify_password("correct horse", encoded))
        self.assertFalse(verify_password("wrong", encoded))
        self.assertFalse(verify_password("correct horse", "not-a-valid-hash"))

    def test_new_token_is_urlsafe_and_unique(self) -> None:
        first = new_token()
        second = new_token()

        self.assertNotEqual(first, second)
        self.assertGreater(len(first), 40)
        self.assertNotIn("/", first)

    def test_totp_code_verification_accepts_current_window_only(self) -> None:
        secret = generate_totp_secret()
        code = totp_code(secret, at_time=1_800_000_000)

        self.assertTrue(verify_totp_code(secret, code, at_time=1_800_000_000))
        self.assertTrue(verify_totp_code(secret, code, at_time=1_800_000_030))
        self.assertFalse(verify_totp_code(secret, "000000", at_time=1_800_000_000))
        self.assertFalse(verify_totp_code(secret, code, at_time=1_800_000_120))

    def test_provisioning_qr_svg_returns_inline_svg(self) -> None:
        svg = provisioning_qr_svg("otpauth://totp/Buddy:test@example.test?secret=ABC")

        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("viewBox=", svg)
        self.assertIn("<path", svg)

    def test_login_challenge_consumes_valid_two_factor_code_once(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        secret = generate_totp_secret()

        with Session() as session:
            user = User(
                id=1,
                email="marcos@example.test",
                name="Marcos",
                password_hash="x",
                two_factor_secret=encrypt_totp_secret(secret),
                two_factor_enabled=True,
            )
            session.add(user)
            session.flush()
            challenge = create_login_challenge(session, user)
            token = challenge.token

            self.assertEqual(consume_login_challenge(session, token, totp_code(secret)).id, user.id)
            self.assertIsNone(consume_login_challenge(session, token, totp_code(secret)))

    def test_bank_link_challenge_is_bound_to_user_and_consumed_once(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)

        with Session() as session:
            user = User(id=1, email="marcos@example.test", name="Marcos", password_hash="x")
            other_user = User(id=2, email="other@example.test", name="Other", password_hash="x")
            session.add_all([user, other_user])
            session.flush()
            challenge = create_bank_link_challenge(session, user)
            token = challenge.token

            self.assertFalse(consume_bank_link_challenge(session, other_user, token))
            self.assertTrue(consume_bank_link_challenge(session, user, token))
            self.assertFalse(consume_bank_link_challenge(session, user, token))

    def test_bank_link_requires_enabled_two_factor_and_valid_code(self) -> None:
        secret = generate_totp_secret()
        user = make_user(1, "Marcos")
        user.two_factor_secret = encrypt_totp_secret(secret)
        user.two_factor_enabled = True

        require_bank_link_two_factor(user, totp_code(secret))
        with self.assertRaises(HTTPException) as bad_code:
            require_bank_link_two_factor(user, "123456")
        self.assertEqual(bad_code.exception.status_code, 401)

        user.two_factor_enabled = False
        with self.assertRaises(HTTPException) as disabled:
            require_bank_link_two_factor(user, totp_code(secret))
        self.assertEqual(disabled.exception.status_code, 403)


class ValidationTests(unittest.TestCase):
    def test_normalize_currency_accepts_supported_codes_case_insensitively(self) -> None:
        self.assertEqual(normalize_currency(" cad "), "CAD")

    def test_normalize_currency_rejects_unsupported_codes(self) -> None:
        with self.assertRaises(HTTPException) as context:
            normalize_currency("zzz")

        self.assertEqual(context.exception.status_code, 400)

    def test_normalize_month_requires_year_month_format(self) -> None:
        self.assertEqual(normalize_month("2026-07"), "2026-07")
        with self.assertRaises(HTTPException):
            normalize_month("07-2026")

    def test_validate_share_total_allows_exactly_100_and_rejects_more(self) -> None:
        validate_share_total([{"share_percent": 40}, {"share_percent": 60}])

        with self.assertRaises(HTTPException) as context:
            validate_share_total([{"share_percent": 80}, {"share_percent": 21}])

        self.assertEqual(context.exception.status_code, 400)

    def test_require_admin_rejects_non_admin_users(self) -> None:
        with self.assertRaises(HTTPException) as context:
            require_admin(make_user(1, "Member", is_admin=False))

        self.assertEqual(context.exception.status_code, 403)
        require_admin(make_user(2, "Admin", is_admin=True))

    def test_validate_expense_payload_accepts_tracker_member_and_category(self) -> None:
        user = make_user(1, "Marcos")
        tracker = make_tracker(make_member(user))
        category = make_category(1, "Groceries", tracker_id=tracker.id)
        payload = ExpenseCreatePayload(
            date=date(2026, 7, 11),
            category_id=category.id,
            amount=Decimal("10.00"),
            paid_by_id=user.id,
            description="Market",
            is_shared=True,
        )

        validate_expense_payload(FakeSession(category), tracker, tracker.id, payload)

    def test_validate_expense_payload_rejects_non_member_payer(self) -> None:
        user = make_user(1, "Marcos")
        tracker = make_tracker(make_member(user))
        category = make_category(1, "Groceries", tracker_id=tracker.id)
        payload = ExpenseCreatePayload(
            date=date(2026, 7, 11),
            category_id=category.id,
            amount=Decimal("10.00"),
            paid_by_id=999,
            description="Market",
            is_shared=True,
        )

        with self.assertRaises(HTTPException) as context:
            validate_expense_payload(FakeSession(category), tracker, tracker.id, payload)

        self.assertEqual(context.exception.status_code, 400)

    def test_validate_expense_payload_rejects_category_from_another_tracker(self) -> None:
        user = make_user(1, "Marcos")
        tracker = make_tracker(make_member(user), tracker_id=1)
        category = make_category(1, "Groceries", tracker_id=2)
        payload = ExpenseCreatePayload(
            date=date(2026, 7, 11),
            category_id=category.id,
            amount=Decimal("10.00"),
            paid_by_id=user.id,
            description="Market",
            is_shared=True,
        )

        with self.assertRaises(HTTPException) as context:
            validate_expense_payload(FakeSession(category), tracker, tracker.id, payload)

        self.assertEqual(context.exception.status_code, 400)


class SchemaTests(unittest.TestCase):
    def test_register_payload_normalizes_email_and_currency(self) -> None:
        payload = RegisterPayload(email=" USER@Example.TEST ", name="User", password="password123", default_currency="cad", is_admin=True)

        self.assertEqual(payload.email, "user@example.test")
        self.assertEqual(payload.default_currency, "CAD")
        self.assertTrue(payload.is_admin)


if __name__ == "__main__":
    unittest.main()
