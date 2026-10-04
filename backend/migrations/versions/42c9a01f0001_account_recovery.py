"""Account recovery state and single-use credentials; no private key storage.

Revision ID: 42c9a01f0001
Revises: 11d712bf1eb8
"""
from alembic import op
import sqlalchemy as sa

revision = "42c9a01f0001"
down_revision = "11d712bf1eb8"
branch_labels = depends_on = None


def upgrade():
    op.create_table("account_security",
        sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("email_verified_at", sa.DateTime(), nullable=True),
        sa.Column("recovery_hold_until", sa.DateTime(), nullable=True))
    op.create_table("recovery_tokens",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("purpose", sa.String(24), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True))
    op.create_index("ix_recovery_tokens_user_id", "recovery_tokens", ["user_id"])
    op.create_table("recovery_codes",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("used_at", sa.DateTime(), nullable=True))
    op.create_index("ix_recovery_codes_user_id", "recovery_codes", ["user_id"])


def downgrade():
    # Recovery credentials are discarded; account/wallet data remains intact.
    op.drop_table("recovery_codes")
    op.drop_table("recovery_tokens")
    op.drop_table("account_security")
