"""Durable nonce reservations survive worker restarts.
Revision ID: 42c9a01f0003
Revises: 42c9a01f0002
"""
from alembic import op
import sqlalchemy as sa

revision = "42c9a01f0003"
down_revision = "42c9a01f0002"
branch_labels = depends_on = None


def upgrade():
    op.create_table("execution_nonces", sa.Column("key", sa.String(90), primary_key=True),
                    sa.Column("next_nonce", sa.BigInteger(), nullable=False),
                    sa.Column("updated_at", sa.DateTime(), nullable=False))


def downgrade():
    op.drop_table("execution_nonces")
