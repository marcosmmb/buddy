from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import segno
from sqlalchemy.orm import Session

from app.crypto import decrypt_value, encrypt_value
from app.models import TwoFactorChallenge, User, utcnow
from app.security import new_token


TOTP_DIGITS = 6
TOTP_PERIOD_SECONDS = 30
LOGIN_CHALLENGE_SECONDS = 10 * 60
BANK_LINK_CHALLENGE_SECONDS = 10 * 60


def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def encrypt_totp_secret(secret: str) -> str:
    return encrypt_value(secret)


def decrypt_totp_secret(secret: str) -> str:
    return decrypt_value(secret)


def grouped_secret(secret: str) -> str:
    compact = "".join(secret.split()).upper()
    return " ".join(compact[index : index + 4] for index in range(0, len(compact), 4))


def provisioning_uri(user: User, secret: str) -> str:
    label = quote(f"Buddy:{user.email}")
    issuer = quote("Buddy")
    return f"otpauth://totp/{label}?secret={secret}&issuer={issuer}&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_PERIOD_SECONDS}"


def provisioning_qr_svg(uri: str) -> str:
    return segno.make(uri, error="m").svg_inline(scale=5, border=2, dark="#16202a", light="#ffffff", omitsize=True)


def totp_code(secret: str, at_time: int | None = None) -> str:
    counter = int((at_time if at_time is not None else time.time()) // TOTP_PERIOD_SECONDS)
    key = base64.b32decode(_padded_secret(secret), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10**TOTP_DIGITS)).zfill(TOTP_DIGITS)


def verify_totp_code(secret: str, code: str, at_time: int | None = None, valid_window: int = 1) -> bool:
    candidate = "".join(str(code or "").split())
    if not candidate.isdigit() or len(candidate) != TOTP_DIGITS:
        return False
    current_time = at_time if at_time is not None else int(time.time())
    for offset in range(-valid_window, valid_window + 1):
        expected = totp_code(secret, current_time + offset * TOTP_PERIOD_SECONDS)
        if hmac.compare_digest(candidate, expected):
            return True
    return False


def create_login_challenge(session: Session, user: User) -> TwoFactorChallenge:
    return _create_challenge(session, user, "login", LOGIN_CHALLENGE_SECONDS)


def create_bank_link_challenge(session: Session, user: User) -> TwoFactorChallenge:
    return _create_challenge(session, user, "bank_link", BANK_LINK_CHALLENGE_SECONDS)


def consume_bank_link_challenge(session: Session, user: User, token: str) -> bool:
    challenge = (
        session.query(TwoFactorChallenge)
        .filter(TwoFactorChallenge.token == token, TwoFactorChallenge.user_id == user.id, TwoFactorChallenge.purpose == "bank_link")
        .one_or_none()
    )
    if challenge is None or challenge.consumed_at is not None or _is_expired(challenge.expires_at):
        return False
    challenge.consumed_at = utcnow()
    return True


def _create_challenge(session: Session, user: User, purpose: str, expires_in_seconds: int) -> TwoFactorChallenge:
    challenge = TwoFactorChallenge(
        token=new_token(),
        user_id=user.id,
        purpose=purpose,
        expires_at=utcnow() + timedelta(seconds=expires_in_seconds),
    )
    session.add(challenge)
    session.flush()
    return challenge


def consume_login_challenge(session: Session, token: str, code: str) -> User | None:
    challenge = session.query(TwoFactorChallenge).filter(TwoFactorChallenge.token == token, TwoFactorChallenge.purpose == "login").one_or_none()
    if challenge is None or challenge.consumed_at is not None or _is_expired(challenge.expires_at):
        return None
    user = session.get(User, challenge.user_id)
    if user is None or not user.is_active or not user.two_factor_enabled or not user.two_factor_secret:
        return None
    if not verify_user_totp(user, code):
        return None
    challenge.consumed_at = utcnow()
    return user


def verify_user_totp(user: User, code: str) -> bool:
    if not user.two_factor_enabled or not user.two_factor_secret:
        return False
    return verify_encrypted_totp_secret(user.two_factor_secret, code)


def verify_encrypted_totp_secret(encrypted_secret: str, code: str) -> bool:
    try:
        secret = decrypt_totp_secret(encrypted_secret)
    except Exception:
        return False
    return verify_totp_code(secret, code)


def _padded_secret(secret: str) -> str:
    compact = "".join(secret.split()).upper()
    return compact + "=" * ((8 - len(compact) % 8) % 8)


def _is_expired(value: datetime) -> bool:
    expires_at = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return expires_at <= utcnow()
