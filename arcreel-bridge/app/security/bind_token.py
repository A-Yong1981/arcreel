import secrets
from datetime import datetime, timedelta

from app.models.auth_models import BindToken
from app.security.totp import hash_value

TTL_SECONDS = 120


def issue_bind_token(db, user_id: str, device_name: str, base_url: str) -> dict:
    token = secrets.token_urlsafe(32)
    db.add(
        BindToken(
            user_id=user_id,
            token_hash=hash_value(token),
            device_name=device_name,
            expires_at=datetime.utcnow() + timedelta(seconds=TTL_SECONDS),
        )
    )
    db.commit()
    return {"bind_url": f"{base_url.rstrip('/')}/bind?t={token}", "ttl_seconds": TTL_SECONDS}


def consume_bind_token(db, token: str) -> BindToken:
    record = (
        db.query(BindToken).filter(BindToken.token_hash == hash_value(token)).first()
    )
    if record is None:
        raise ValueError("BIND_TOKEN_INVALID")
    if record.used_at is not None:
        raise ValueError("BIND_TOKEN_USED")
    if record.expires_at < datetime.utcnow():
        raise ValueError("BIND_TOKEN_EXPIRED")
    record.used_at = datetime.utcnow()
    db.commit()
    return record
