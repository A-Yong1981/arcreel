import base64
import hashlib
import hmac
import os
import secrets
import string
import struct
import time
from datetime import datetime, timedelta

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ISSUER = "ArcReel"
DIGITS = 6
PERIOD = 30
WINDOW = 1
MAX_USER_FAILURES = 5
USER_LOCK_MINUTES = 15
MAX_IP_ATTEMPTS_MINUTES = 5
MAX_IP_ATTEMPTS = 10


def _encryption_key() -> bytes:
    raw = os.environ.get("TOTP_ENCRYPTION_KEY", "")
    if not raw:
        raise RuntimeError("TOTP_ENCRYPTION_KEY is not set")
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise RuntimeError("TOTP_ENCRYPTION_KEY must decode to 32 bytes")
    return key


def generate_secret() -> str:
    return base64.b32encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")


def encrypt_secret(secret: str) -> str:
    aes = AESGCM(_encryption_key())
    nonce = secrets.token_bytes(12)
    sealed = aes.encrypt(nonce, secret.encode("ascii"), None)
    return base64.b64encode(nonce + sealed).decode("ascii")


def decrypt_secret(stored: str) -> str:
    raw = base64.b64decode(stored)
    aes = AESGCM(_encryption_key())
    return aes.decrypt(raw[:12], raw[12:], None).decode("ascii")


def otpauth_uri(secret: str, account: str) -> str:
    return (
        f"otpauth://totp/{ISSUER}:{account}"
        f"?secret={secret}&issuer={ISSUER}&algorithm=SHA1&digits=6&period=30"
    )


def _hotp(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * ((8 - len(secret) % 8) % 8))
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** DIGITS)).zfill(DIGITS)


def verify_code(secret: str, code: str, now: int | None = None) -> int | None:
    if not code or len(code) != DIGITS or not code.isdigit():
        return None
    current = int((now if now is not None else time.time()) // PERIOD)
    for step in range(current - WINDOW, current + WINDOW + 1):
        if hmac.compare_digest(_hotp(secret, step), code):
            return step
    return None


def is_user_locked(db, user_id: str, now: datetime) -> bool:
    from app.models.auth_models import AuthAttempt

    cutoff = now - timedelta(minutes=USER_LOCK_MINUTES)
    rows = (
        db.query(AuthAttempt)
        .filter(AuthAttempt.user_id == user_id, AuthAttempt.created_at >= cutoff)
        .order_by(AuthAttempt.created_at.desc())
        .limit(MAX_USER_FAILURES)
        .all()
    )
    if len(rows) < MAX_USER_FAILURES:
        return False
    return all(row.success == 0 for row in rows)


def is_ip_limited(db, ip: str, now: datetime) -> bool:
    from app.models.auth_models import AuthAttempt

    cutoff = now - timedelta(minutes=MAX_IP_ATTEMPTS_MINUTES)
    count = (
        db.query(AuthAttempt)
        .filter(AuthAttempt.ip == ip, AuthAttempt.created_at >= cutoff)
        .count()
    )
    return count >= MAX_IP_ATTEMPTS


def record_attempt(db, user_id: str, ip: str, success: bool) -> None:
    from app.models.auth_models import AuthAttempt

    db.add(AuthAttempt(user_id=user_id, ip=ip, success=1 if success else 0))
    db.commit()


def generate_recovery_codes() -> list[str]:
    alphabet = "".join(c for c in string.ascii_uppercase + string.digits if c not in "01IO")
    codes = []
    while len(codes) < 10:
        code = "-".join(
            "".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(2)
        )
        if code not in codes:
            codes.append(code)
    return codes


def hash_value(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
