import hashlib
import secrets
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.auth_models import DeviceVerification, SessionRecord

SESSION_TTL_HOURS = 12


def hash_value(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def create_session(db: Session, user_id: str, device_id: str) -> str:
    token = secrets.token_urlsafe(32)
    db.add(
        SessionRecord(
            user_id=user_id,
            device_id=device_id,
            token_hash=hash_value(token),
            expires_at=datetime.utcnow() + timedelta(hours=SESSION_TTL_HOURS),
        )
    )
    db.commit()
    return token


def revoke_device_sessions(db: Session, user_id: str, device_id: str) -> None:
    now = datetime.utcnow()
    rows = (
        db.query(SessionRecord)
        .filter(
            SessionRecord.user_id == user_id,
            SessionRecord.device_id == device_id,
            SessionRecord.revoked_at.is_(None),
        )
        .all()
    )
    for row in rows:
        row.revoked_at = now
    db.commit()


def get_session_by_token(db: Session, token: str) -> SessionRecord:
    record = (
        db.query(SessionRecord)
        .filter(SessionRecord.token_hash == hash_value(token))
        .first()
    )
    if record is None or record.revoked_at is not None:
        raise HTTPException(status_code=401, detail="UNAUTHORIZED")
    if record.expires_at < datetime.utcnow():
        raise HTTPException(status_code=401, detail="UNAUTHORIZED")
    device = (
        db.query(DeviceVerification)
        .filter(
            DeviceVerification.user_id == record.user_id,
            DeviceVerification.device_id == record.device_id,
        )
        .first()
    )
    if device is None or device.status != "active":
        raise HTTPException(status_code=401, detail="DEVICE_REVOKED")
    return record
