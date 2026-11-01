from datetime import datetime
from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class DeviceVerification(Base):
    __tablename__ = "device_verifications"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, index=True)
    device_id = Column(String(64), nullable=False, index=True)
    device_name = Column(String(128), nullable=False, default="")
    user_agent = Column(String(512), nullable=False, default="")
    ip = Column(String(64), nullable=False, default="")
    bind_method = Column(String(16), nullable=False)
    status = Column(String(16), nullable=False, default="active")
    first_verified_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_verified_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    revoked_at = Column(DateTime, nullable=True)
    revoked_by = Column(String(64), nullable=True)


class BindToken(Base):
    __tablename__ = "bind_tokens"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True)
    device_name = Column(String(128), nullable=False, default="")
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class TotpReplay(Base):
    __tablename__ = "totp_replay"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, index=True)
    time_step = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class RecoveryCode(Base):
    __tablename__ = "recovery_codes"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, index=True)
    code_hash = Column(String(64), nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AuthAttempt(Base):
    __tablename__ = "auth_attempts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, index=True)
    ip = Column(String(64), nullable=False, index=True)
    success = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class SessionRecord(Base):
    __tablename__ = "sessions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, index=True)
    device_id = Column(String(64), nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True)
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, index=True)
    device_id = Column(String(64), nullable=True)
    action = Column(String(64), nullable=False)
    ip = Column(String(64), nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class TotpSecret(Base):
    __tablename__ = "totp_secrets"
    user_id = Column(String(64), primary_key=True)
    encrypted_secret = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
