from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.settings import get_settings


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    task: Mapped[str] = mapped_column(Text)
    repository: Mapped[str] = mapped_column(String(255))
    base_branch: Mapped[str] = mapped_column(String(255))
    slack_channel_id: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class RunEvent(Base):
    __tablename__ = "run_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    event_key: Mapped[str] = mapped_column(String(255), unique=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    data: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class ApprovalKey(Base):
    __tablename__ = "approval_keys"
    user_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    public_key: Mapped[bytes] = mapped_column(LargeBinary(32))
    key_fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"
    __table_args__ = (UniqueConstraint("run_id", "action_key", name="uq_approval_run_action"),)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    action_key: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(128))
    summary: Mapped[dict] = mapped_column(JSONB)
    signing_payload: Mapped[dict] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class PurchaseIntent(Base):
    __tablename__ = "purchase_intents"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    merchant_id: Mapped[str] = mapped_column(String(255), index=True)
    order_id: Mapped[str] = mapped_column(String(255))
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    merchant_category: Mapped[str] = mapped_column(String(80))
    cart_payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    stripe_card_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exchange_idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    exchange_request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    exchange_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    exchange_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exchanged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class MerchantCredential(Base):
    __tablename__ = "merchant_credentials"
    merchant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    origin: Mapped[str] = mapped_column(String(500))
    signing_key_id: Mapped[str] = mapped_column(String(128))
    signing_public_key: Mapped[bytes] = mapped_column(LargeBinary(32))
    secret_hash: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class OAuthCredential(Base):
    __tablename__ = "oauth_credentials"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_oauth_user_provider"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    nonce: Mapped[bytes] = mapped_column(LargeBinary(12))
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scopes: Mapped[list] = mapped_column(JSONB, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    if get_settings().app_env == "development":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)


async def session_scope() -> AsyncIterator[AsyncSession]:
    async with Session() as session:
        yield session


async def append_event(session: AsyncSession, run_id: UUID, event_key: str, event_type: str, data: dict) -> None:
    stmt = pg_insert(RunEvent).values(run_id=run_id, event_key=event_key, event_type=event_type, data=data)
    await session.execute(stmt.on_conflict_do_nothing(index_elements=[RunEvent.event_key]))

