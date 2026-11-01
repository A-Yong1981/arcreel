import hashlib
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.auth_models import (
    AuditLog,
    DeviceVerification,
    RecoveryCode,
    TotpReplay,
    TotpSecret,
)
from app.security import bind_token as bind_service
from app.security import session as session_service
from app.security.totp import (
    decrypt_secret,
    generate_recovery_codes,
    generate_secret,
    encrypt_secret,
    hash_value,
    is_ip_limited,
    is_user_locked,
    otpauth_uri,
    record_attempt,
    verify_code,
)

router = APIRouter(prefix="/auth", tags=["auth"])
bearer = HTTPAuthorizationCredentials
_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_session(
    credentials=Depends(_bearer_scheme), db: Session = Depends(get_db)
):
    if credentials is None:
        raise HTTPException(status_code=401, detail="UNAUTHORIZED")
    return session_service.get_session_by_token(db, credentials.credentials)


class TotpVerifyIn(BaseModel):
    user_id: str
    code: str


class BindRequestIn(BaseModel):
    device_name: str


class BindConfirmIn(BaseModel):
    token: str
    device_name: str
    user_agent: str = ""
    ip: str = ""


class UnbindIn(BaseModel):
    device_id: str
    totp_code: str = ""
    confirmed_by_device_id: str = ""


def _device_id(user_agent: str, ip: str, device_name: str) -> str:
    return hashlib.sha256(f"{user_agent}|{ip}|{device_name}".encode("utf-8")).hexdigest()


@router.post("/totp/setup")
def totp_setup(user_id: str, account: str, db: Session = Depends(get_db)):
    secret = generate_secret()
    db.merge(TotpSecret(user_id=user_id, encrypted_secret=encrypt_secret(secret)))
    db.commit()
    return {"otpauth_uri": otpauth_uri(secret, account), "secret_shown_once": True}


@router.post("/totp/verify")
def totp_verify(payload: TotpVerifyIn, request: Request, db: Session = Depends(get_db)):
    now = datetime.utcnow()
    ip = request.client.host if request.client else ""
    if is_user_locked(db, payload.user_id, now) or is_ip_limited(db, ip, now):
        raise HTTPException(status_code=429, detail="TOO_MANY_ATTEMPTS")
    stored = db.query(TotpSecret).filter(TotpSecret.user_id == payload.user_id).first()
    if stored is None:
        record_attempt(db, payload.user_id, ip, False)
        raise HTTPException(status_code=401, detail="TOTP_NOT_BOUND")
    secret = decrypt_secret(stored.encrypted_secret)
    step = verify_code(secret, payload.code)
    if step is None:
        record_attempt(db, payload.user_id, ip, False)
        raise HTTPException(status_code=401, detail="INVALID_TOTP")
    replay = (
        db.query(TotpReplay)
        .filter(TotpReplay.user_id == payload.user_id, TotpReplay.time_step == step)
        .first()
    )
    if replay is not None:
        raise HTTPException(status_code=401, detail="TOTP_REUSED")
    db.add(TotpReplay(user_id=payload.user_id, time_step=step))
    record_attempt(db, payload.user_id, ip, True)
    db.commit()
    return {"verified": True}


@router.post("/totp/verify-and-login")
def totp_verify_and_login(payload: TotpVerifyIn, request: Request, db: Session = Depends(get_db)):
    result = totp_verify(payload, request, db)
    ip = request.client.host if request.client else ""
    device_id = _device_id(request.headers.get("user-agent", ""), ip, "primary")
    device = (
        db.query(DeviceVerification)
        .filter(
            DeviceVerification.user_id == payload.user_id,
            DeviceVerification.device_id == device_id,
        )
        .first()
    )
    if device is None:
        device = DeviceVerification(
            user_id=payload.user_id,
            device_id=device_id,
            device_name="primary",
            user_agent=request.headers.get("user-agent", ""),
            ip=ip,
            bind_method="manual",
            status="active",
        )
        db.add(device)
    device.last_verified_at = datetime.utcnow()
    db.add(AuditLog(user_id=payload.user_id, device_id=device_id, action="totp_login", ip=ip))
    db.commit()
    token = session_service.create_session(db, payload.user_id, device_id)
    return {"verified": result["verified"], "session_token": token, "device_id": device_id}


@router.post("/recovery-codes/generate")
def recovery_codes_generate(user_id: str, db: Session = Depends(get_db)):
    codes = generate_recovery_codes()
    db.query(RecoveryCode).filter(RecoveryCode.user_id == user_id).delete()
    for code in codes:
        db.add(RecoveryCode(user_id=user_id, code_hash=hash_value(code)))
    db.commit()
    return {"recovery_codes": codes, "shown_once": True}


@router.post("/recovery-code/use")
def recovery_code_use(user_id: str, code: str, db: Session = Depends(get_db)):
    row = (
        db.query(RecoveryCode)
        .filter(
            RecoveryCode.user_id == user_id,
            RecoveryCode.code_hash == hash_value(code),
            RecoveryCode.used_at.is_(None),
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=401, detail="INVALID_RECOVERY_CODE")
    row.used_at = datetime.utcnow()
    db.commit()
    return {"verified": True}


@router.post("/bind/request")
def bind_request(payload: BindRequestIn, request: Request, db: Session = Depends(get_db), session=Depends(get_current_session)):
    return bind_service.issue_bind_token(db, session.user_id, payload.device_name, str(request.base_url))


@router.post("/bind/confirm")
def bind_confirm(payload: BindConfirmIn, request: Request, db: Session = Depends(get_db)):
    try:
        record = bind_service.consume_bind_token(db, payload.token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    ip = payload.ip or (request.client.host if request.client else "")
    device_id = _device_id(payload.user_agent, ip, payload.device_name)
    now = datetime.utcnow()
    device = (
        db.query(DeviceVerification)
        .filter(
            DeviceVerification.user_id == record.user_id,
            DeviceVerification.device_id == device_id,
        )
        .first()
    )
    if device is None:
        device = DeviceVerification(
            user_id=record.user_id,
            device_id=device_id,
            device_name=payload.device_name,
            user_agent=payload.user_agent,
            ip=ip,
            bind_method="qr",
            status="active",
            first_verified_at=now,
            last_verified_at=now,
        )
        db.add(device)
    else:
        device.status = "active"
        device.last_verified_at = now
    db.add(AuditLog(user_id=record.user_id, device_id=device_id, action="device_bind", ip=ip))
    db.commit()
    token = session_service.create_session(db, record.user_id, device_id)
    return {"bound": True, "device_id": device_id, "session_token": token}


@router.get("/devices")
def list_devices(db: Session = Depends(get_db), session=Depends(get_current_session)):
    rows = (
        db.query(DeviceVerification)
        .filter(DeviceVerification.user_id == session.user_id)
        .order_by(DeviceVerification.last_verified_at.desc())
        .all()
    )
    return [
        {
            "id": row.id,
            "user_id": row.user_id,
            "device_id": row.device_id,
            "device_name": row.device_name,
            "user_agent": row.user_agent,
            "ip": row.ip,
            "bind_method": row.bind_method,
            "status": row.status,
            "first_verified_at": row.first_verified_at,
            "last_verified_at": row.last_verified_at,
            "revoked_at": row.revoked_at,
            "revoked_by": row.revoked_by,
        }
        for row in rows
    ]


@router.post("/unbind")
def unbind(payload: UnbindIn, request: Request, db: Session = Depends(get_db), session=Depends(get_current_session)):
    ip = request.client.host if request.client else ""
    device = (
        db.query(DeviceVerification)
        .filter(
            DeviceVerification.user_id == session.user_id,
            DeviceVerification.device_id == payload.device_id,
        )
        .first()
    )
    if device is None:
        raise HTTPException(status_code=404, detail="DEVICE_NOT_FOUND")
    confirmed = False
    if payload.totp_code:
        stored = db.query(TotpSecret).filter(TotpSecret.user_id == session.user_id).first()
        if stored is not None and verify_code(decrypt_secret(stored.encrypted_secret), payload.totp_code) is not None:
            confirmed = True
    if payload.confirmed_by_device_id:
        other = (
            db.query(DeviceVerification)
            .filter(
                DeviceVerification.user_id == session.user_id,
                DeviceVerification.device_id == payload.confirmed_by_device_id,
                DeviceVerification.status == "active",
            )
            .first()
        )
        if other is not None and payload.confirmed_by_device_id != payload.device_id:
            confirmed = True
    if not confirmed:
        raise HTTPException(status_code=403, detail="UNBIND_REQUIRES_SECOND_CONFIRMATION")
    device.status = "revoked"
    device.revoked_at = datetime.utcnow()
    device.revoked_by = session.user_id
    db.add(AuditLog(user_id=session.user_id, device_id=payload.device_id, action="device_unbind", ip=ip))
    db.commit()
    session_service.revoke_device_sessions(db, session.user_id, payload.device_id)
    return {"revoked": True, "device_id": payload.device_id}
