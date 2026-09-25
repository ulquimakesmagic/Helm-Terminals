import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def uid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    """Naive UTC, so SQLite and Postgres behave the same."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


@event.listens_for(Base, "init", propagate=True)
def _assign_id(target, args, kwargs) -> None:
    """Give every row its id at construction, so it can be referenced (e.g. in the activity log) before flush."""
    if "id" in type(target).__table__.c and kwargs.get("id") is None:
        kwargs["id"] = uid()


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    totp_secret_enc: Mapped[str | None] = mapped_column(String(255), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    channel: Mapped[str] = mapped_column(String(8), default="web")  # web | bot


class Folder(Base):
    __tablename__ = "folders"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    color: Mapped[str] = mapped_column(String(16), default="#6366f1")


class Wallet(Base):
    __tablename__ = "wallets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    network: Mapped[str] = mapped_column(String(16))
    address: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # managed | watch
    signer_ref: Mapped[str | None] = mapped_column(Text, nullable=True)  # never returned by the API
    label: Mapped[str] = mapped_column(String(80), default="")
    folder_id: Mapped[str | None] = mapped_column(ForeignKey("folders.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("user_id", "network", "address"),)


class Funder(Base):
    __tablename__ = "funders"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    network: Mapped[str] = mapped_column(String(16))
    wallet_id: Mapped[str] = mapped_column(ForeignKey("wallets.id"))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    label: Mapped[str] = mapped_column(String(80), default="Funder")


class Launch(Base):
    __tablename__ = "launches"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    network: Mapped[str] = mapped_column(String(16))
    launchpad: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft|deploying|live|failed|archived
    name: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(16))
    description: Mapped[str] = mapped_column(Text, default="")
    image_blob: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    socials: Mapped[dict] = mapped_column(JSON, default=dict)
    launchpad_params: Mapped[dict] = mapped_column(JSON, default=dict)
    mint_address: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    dev_wallet_id: Mapped[str | None] = mapped_column(ForeignKey("wallets.id"), nullable=True)
    dev_buy_sol: Mapped[float] = mapped_column(default=0.0)
    imported: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    deployed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class LaunchWallet(Base):
    __tablename__ = "launch_wallets"
    launch_id: Mapped[str] = mapped_column(ForeignKey("launches.id"), primary_key=True)
    wallet_id: Mapped[str] = mapped_column(ForeignKey("wallets.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), default="holder")  # dev | holder


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    launch_id: Mapped[str | None] = mapped_column(ForeignKey("launches.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(24))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|submitted|confirmed|failed|partial
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    channel: Mapped[str] = mapped_column(String(8), default="web")  # where the confirmation came from
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("user_id", "idempotency_key"),)


class Transaction(Base):
    __tablename__ = "transactions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    wallet_id: Mapped[str | None] = mapped_column(ForeignKey("wallets.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(24))
    signature: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sol_delta_lamports: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # measured, includes fees
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Watch(Base):
    __tablename__ = "watchlist"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    network: Mapped[str] = mapped_column(String(16))
    address: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(80), default="")
    bookmarked: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (UniqueConstraint("user_id", "network", "address"),)


class Activity(Base):
    __tablename__ = "activity"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    ref_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    ref_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TelegramLink(Base):
    """One Telegram account linked to one Helm user. Removed on unlink."""

    __tablename__ = "telegram_links"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LinkCode(Base):
    """Short-lived one-time code shown in the web app and sent to the bot. Only its hash is stored."""

    __tablename__ = "link_codes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Referral(Base):
    __tablename__ = "referrals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    max_uses: Mapped[int] = mapped_column(Integer, default=1)
