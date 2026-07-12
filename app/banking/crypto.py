from __future__ import annotations

from app.crypto import decrypt_value, encrypt_value

def encrypt_token(value: str) -> str:
    return encrypt_value(value)


def decrypt_token(value: str) -> str:
    return decrypt_value(value)
