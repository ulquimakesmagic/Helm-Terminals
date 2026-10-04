"""Separate treasury receipt proof from manual review.

Revision ID: 42c9a01f0002
Revises: 42c9a01f0001
"""
from alembic import op
import sqlalchemy as sa

revision = "42c9a01f0002"
down_revision = "42c9a01f0001"
branch_labels = depends_on = None


def upgrade():
    op.add_column("revenue_events", sa.Column("receipt_evidence", sa.JSON(), nullable=True))
    op.add_column("revenue_events", sa.Column("reviewed_at", sa.DateTime(), nullable=True))
    # Old manual annotations are preserved, but do not certify a payment.
    op.execute("UPDATE revenue_events SET reviewed_at = reconciled_at")
    op.execute("UPDATE revenue_events SET status = 'pending', estimated = true, reconciled_at = NULL "
               "WHERE source = 'dex_trade_fee' AND status = 'received'")


def downgrade():
    # Never reinstate an unproven received balance on rollback.
    op.drop_column("revenue_events", "reviewed_at")
    op.drop_column("revenue_events", "receipt_evidence")
