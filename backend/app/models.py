import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, LargeBinary, Numeric, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, ExactRawAmount


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


class AccountSecurity(Base):
    __tablename__ = "account_security"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    recovery_hold_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RecoveryToken(Base):
    __tablename__ = "recovery_tokens"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    purpose: Mapped[str] = mapped_column(String(24))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RecoveryCode(Base):
    __tablename__ = "recovery_codes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


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
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|submitted|confirmed|failed|partial|unconfirmed
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


class Automation(Base):
    """Archived schedule records only. The execution engine and all mutation endpoints were removed."""
    __tablename__ = "automations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    launch_id: Mapped[str] = mapped_column(ForeignKey("launches.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="scheduled", index=True)
    side: Mapped[str] = mapped_column(String(8), default="buy")
    wallet_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    order_sol: Mapped[float] = mapped_column(default=0.0)
    sell_percent: Mapped[float] = mapped_column(default=0.0)
    total_orders: Mapped[int] = mapped_column(Integer, default=1)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=300)
    slippage_pct: Mapped[float] = mapped_column(default=5.0)
    next_run_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    runs_completed: Mapped[int] = mapped_column(Integer, default=0)
    last_job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(64))
    preflight_hash: Mapped[str] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("user_id", "idempotency_key"),)


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
    max_uses: Mapped[int] = mapped_column(Integer, default=1000)


class ReferralLink(Base):
    """Who referred whom. One row per referred user, written once at sign-up."""
    __tablename__ = "referral_links"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    referred_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    referrer_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    code: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PayoutAddress(Base):
    """Where a user's referral earnings are paid, per network."""
    __tablename__ = "payout_addresses"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    network: Mapped[str] = mapped_column(String(16), default="solana")
    address: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("user_id", "network"),)


class FeeRecord(Base):
    """One trading fee: what the platform and the referrer received, and how it was paid."""
    __tablename__ = "fee_records"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    payer_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    wallet_id: Mapped[str | None] = mapped_column(ForeignKey("wallets.id"), nullable=True)
    network: Mapped[str] = mapped_column(String(16), default="solana")
    side: Mapped[str] = mapped_column(String(8))
    basis_lamports: Mapped[int] = mapped_column(BigInteger)  # the SOL amount the fee was computed on
    platform_lamports: Mapped[int] = mapped_column(BigInteger, default=0)
    referrer_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    referral_lamports: Mapped[int] = mapped_column(BigInteger, default=0)
    referral_status: Mapped[str] = mapped_column(String(12), default="none")  # none | paid | owed
    mode: Mapped[str] = mapped_column(String(12), default="in_tx")  # in_tx | follow_up
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | confirmed | failed
    signature: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Profile(Base):
    """How a user presents themselves. Private by default; a public page only exists once they opt in."""
    __tablename__ = "profiles"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    display_name: Mapped[str] = mapped_column(String(40), default="")
    handle: Mapped[str | None] = mapped_column(String(20), unique=True, index=True, nullable=True)
    bio: Mapped[str] = mapped_column(String(280), default="")
    x_handle: Mapped[str] = mapped_column(String(16), default="")
    telegram_handle: Mapped[str] = mapped_column(String(32), default="")
    website: Mapped[str] = mapped_column(String(120), default="")
    avatar_hue: Mapped[int] = mapped_column(Integer, default=265)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


def _declare_fk_dependencies() -> None:
    """Tell SQLAlchemy's unit of work which table depends on which.

    The models use plain foreign-key columns with no relationship(), so on flush SQLAlchemy has no dependency
    information and may INSERT a child row (e.g. an activity) before its parent (the user). SQLite ignores that;
    Postgres rejects it. One private many-to-one relationship per foreign key fixes the ordering for every route.
    """
    by_table = {m.local_table.name: m.class_ for m in Base.registry.mappers}
    for cls in list(by_table.values()):
        for fk in cls.__table__.foreign_keys:
            target = by_table.get(fk.column.table.name)
            if target is None:
                continue
            setattr(
                cls,
                f"_dep_{fk.parent.name}",
                relationship(target, foreign_keys=[fk.parent], lazy="raise", viewonly=False),
            )


class AuthFailure(Base):
    """A throttled authentication or registration attempt, keyed by the target/IP bucket. Rows older than the
    window are ignored and pruned opportunistically."""
    __tablename__ = "auth_failures"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    key: Mapped[str] = mapped_column(String(160), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class TotpUse(Base):
    """A 2FA time step already accepted for a user. A code can't be replayed within its validity window."""
    __tablename__ = "totp_uses"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    step: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("user_id", "step"),)


# --- Admin, treasury and revenue ---------------------------------------------------------------------


class AdminRole(Base):
    """A privileged role bound to a user ID. Superadmin binding also requires the configured owner ID and designated
    email (see services/admin.py), and once bound it never moves to another user ID."""
    __tablename__ = "admin_roles"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(16))  # superadmin | viewer
    granted_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AdminAudit(Base):
    """Append-only record of admin access and privileged actions. The API has no way to change or delete rows; on
    Postgres a trigger refuses UPDATE and DELETE too."""
    __tablename__ = "admin_audit"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    actor_user_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    action: Mapped[str] = mapped_column(String(48), index=True)
    target: Mapped[str | None] = mapped_column(String(120), nullable=True)
    changes: Mapped[dict] = mapped_column(JSON, default=dict)  # {field: [old, new]}; never secrets
    context: Mapped[dict] = mapped_column(JSON, default=dict)  # ip, user agent, session ref
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class PlatformSetting(Base):
    """An admin-set value that overrides the environment default (fee rates). Absent = the env value applies."""
    __tablename__ = "platform_settings"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class TreasuryAddress(Base):
    """Where Helm's own revenue share is sent, per network. Receive-only: Helm never holds its keys."""
    __tablename__ = "treasury_addresses"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    network: Mapped[str] = mapped_column(String(16), unique=True)  # solana | evm (default for EVM chains) | base | ...
    address: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(80), default="")
    updated_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class RevenueEvent(Base):
    """One piece of Helm revenue, in its original asset and network. `status`:
    received  - moved on-chain to a treasury address in a confirmed transaction Helm sent
    pending   - in a transaction that hasn't confirmed
    configured - provider recipient configured; no accrual or payment established (amount must be unknown)
    claimable - held by a provider (Relay, a launchpad fee locker) until someone claims it
    failed    - signed fee transaction reached a verified terminal failure; not revenue received
    `reconciled_at` is set when an admin has matched it against the treasury's on-chain inflows."""
    __tablename__ = "revenue_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    source: Mapped[str] = mapped_column(String(24), index=True)  # trade_fee | dex_trade_fee | route_fee | creator_share | partner
    network: Mapped[str] = mapped_column(String(16), index=True)
    asset: Mapped[str] = mapped_column(String(16))  # SOL, ETH, BNB, USDC ...
    # Integer base units (up to 78 digits: 18-decimal amounts overflow BIGINT). None when the provider doesn't say.
    amount_raw: Mapped[int | None] = mapped_column(ExactRawAmount(), nullable=True)
    decimals: Mapped[int] = mapped_column(Integer, default=9)
    estimated: Mapped[bool] = mapped_column(Boolean, default=False)  # computed from a quote, not read on-chain
    status: Mapped[str] = mapped_column(String(12), index=True)
    destination: Mapped[str | None] = mapped_column(String(64), nullable=True)
    launch_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    payer_user_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)  # tx signature / hash / claim id
    fee_record_id: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    note: Mapped[str] = mapped_column(String(200), default="")
    receipt_evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class AccountStatus(Base):
    """Suspension state for a user. No row = active."""
    __tablename__ = "account_status"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(12), default="active")  # active | suspended
    reason: Mapped[str] = mapped_column(String(300), default="")
    changed_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ExecutionNonce(Base):
    __tablename__ = "execution_nonces"
    key: Mapped[str] = mapped_column(String(90), primary_key=True)
    next_nonce: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# Two statements, run separately: asyncpg refuses several commands in one prepared statement.
_IMMUTABLE_FN = """CREATE OR REPLACE FUNCTION helm_admin_audit_immutable() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'admin_audit is append-only'; END; $$ LANGUAGE plpgsql"""
_IMMUTABLE_TRIGGER = """CREATE TRIGGER helm_admin_audit_no_change BEFORE UPDATE OR DELETE ON admin_audit
FOR EACH ROW EXECUTE FUNCTION helm_admin_audit_immutable()"""
for _ddl in (_IMMUTABLE_FN, _IMMUTABLE_TRIGGER):
    event.listen(AdminAudit.__table__, "after_create",
                 __import__("sqlalchemy").DDL(_ddl).execute_if(dialect="postgresql"))


class AutomationTerms(Base):
    """The Helm trading-fee rates a scheduled plan was confirmed at. If an admin later raises a rate, the plan stops
    instead of silently paying more; a lower rate simply applies. Plans created before this table use the env rates."""
    __tablename__ = "automation_terms"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    automation_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    trade_fee_bps_solana: Mapped[int] = mapped_column(Integer, default=0)
    trade_fee_bps_evm_dex: Mapped[int] = mapped_column(Integer, default=0)
    fee_terms: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AutomationLimits(Base):
    """The explicit limits a position plan was confirmed with (plans from before this table have none: they keep
    their original per-order size and run until done). One wallet per plan."""
    __tablename__ = "automation_limits"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    automation_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    wallet_id: Mapped[str] = mapped_column(String(32))
    budget: Mapped[float | None] = mapped_column(nullable=True)  # buys: total native coin the plan may spend
    sell_total_raw: Mapped[int | None] = mapped_column(ExactRawAmount(), nullable=True)  # sells: total tokens to sell
    sell_order_raw: Mapped[int | None] = mapped_column(ExactRawAmount(), nullable=True)  # sells: tokens per order
    token_decimals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_price_impact_pct: Mapped[float] = mapped_column(default=5.0)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LaunchAllocation(Base):
    """One line of a launch's declared supply allocation. Amounts are whole-token decimal strings so they reconcile
    exactly to the supply. `lock` = Helm refuses to move the locked part out of the recipient wallet, which only
    works for the owner's own Helm-managed wallets; any other schedule is a public declaration, not enforced."""
    __tablename__ = "launch_allocations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    launch_id: Mapped[str] = mapped_column(ForeignKey("launches.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    category: Mapped[str] = mapped_column(String(24))
    label: Mapped[str] = mapped_column(String(80), default="")
    recipient: Mapped[str] = mapped_column(String(64), default="")  # empty for "public sale / curve"
    amount: Mapped[str] = mapped_column(String(80))  # whole tokens, exact decimal string
    unlock: Mapped[str] = mapped_column(String(16), default="immediate")  # immediate | date | linear
    unlock_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # date: unlock time; linear: cliff
    unlock_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # linear: fully unlocked
    conditions: Mapped[str] = mapped_column(String(280), default="")
    lock: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual | dispersal
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)  # job id for dispersals
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AutomationLaunchSlot(Base):
    """At most one active position plan per launch, enforced by the database (unique launch_id) so two servers
    can't both accept a plan. The slot points at the plan holding it; a finished plan's slot can be taken over."""
    __tablename__ = "automation_launch_slots"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    launch_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    automation_id: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class LaunchFeeExemption(Base):
    __tablename__ = "launch_fee_exemptions"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    reason: Mapped[str] = mapped_column(String(300))
    changed_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class PlatformCharge(Base):
    """Exact, asset-aware platform payment and referral liability; never a quote relabelled as income."""
    __tablename__ = "platform_charges"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    reference_key: Mapped[str] = mapped_column(String(120), unique=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    launch_id: Mapped[str | None] = mapped_column(ForeignKey("launches.id"), nullable=True, index=True)
    wallet_id: Mapped[str] = mapped_column(ForeignKey("wallets.id"))
    payer_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    network: Mapped[str] = mapped_column(String(16))
    asset: Mapped[str] = mapped_column(String(16))
    token: Mapped[str | None] = mapped_column(String(120), nullable=True)
    decimals: Mapped[int] = mapped_column(Integer)
    amount_raw: Mapped[int] = mapped_column(ExactRawAmount())
    platform_raw: Mapped[int] = mapped_column(ExactRawAmount())
    referral_raw: Mapped[int] = mapped_column(ExactRawAmount(), default=0)
    referrer_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    referral_status: Mapped[str] = mapped_column(String(12), default="none")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    destination: Mapped[str] = mapped_column(String(120))
    signature: Mapped[str | None] = mapped_column(String(120), nullable=True)
    terms: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class PerformanceCard(Base):
    """Owner-only presentation preferences. Custom media is encrypted with the application's secret."""
    __tablename__ = "performance_cards"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    preferences: Mapped[dict] = mapped_column(JSON, default=dict)
    media_cipher: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    media_info: Mapped[dict | None] = mapped_column(JSON, nullable=True)


# Run after all models exist so ledger and archived schedule rows flush after their parents.
_declare_fk_dependencies()
