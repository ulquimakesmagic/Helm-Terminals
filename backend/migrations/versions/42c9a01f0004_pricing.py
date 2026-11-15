"""Asset-aware platform charges and explicit launch exemptions."""
from alembic import op
import sqlalchemy as sa
from decimal import Decimal

revision = "42c9a01f0004"
down_revision = "42c9a01f0003"
branch_labels = depends_on = None


def upgrade():
    op.add_column("automation_terms", sa.Column("fee_terms", sa.JSON(), nullable=True))
    raw = sa.String(78).with_variant(sa.Numeric(78, 0), "postgresql")
    if op.get_bind().dialect.name == "sqlite":
        for table, columns in (("revenue_events", ("amount_raw",)), ("automation_limits", ("sell_total_raw", "sell_order_raw"))):
            with op.batch_alter_table(table) as batch:
                for column in columns:
                    batch.alter_column(column, existing_type=sa.Numeric(78, 0), type_=sa.String(78))
            # SQLite may render a pre-existing large NUMERIC as scientific notation while casting.
            # Expand that representation; precision already lost by an old REAL value is not recoverable.
            bind = op.get_bind()
            for column in columns:
                values = bind.execute(sa.text(f"SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL")).all()
                for identifier, value in values:
                    number = Decimal(str(value))
                    if not number.is_finite() or number != number.to_integral_value():
                        raise ValueError(f"Non-integer legacy raw amount in {table}.{column}; review before migrating")
                    bind.execute(sa.text(f"UPDATE {table} SET {column}=:value WHERE id=:id"),
                                 {"value": str(int(number)), "id": identifier})
    op.create_table("launch_fee_exemptions",
        sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False), sa.Column("reason", sa.String(300), nullable=False),
        sa.Column("changed_by", sa.String(32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False))
    op.create_table("platform_charges",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("reference_key", sa.String(120), nullable=False, unique=True),
        sa.Column("job_id", sa.String(32), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("launch_id", sa.String(32), sa.ForeignKey("launches.id")),
        sa.Column("wallet_id", sa.String(32), sa.ForeignKey("wallets.id"), nullable=False),
        sa.Column("payer_user_id", sa.String(32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False), sa.Column("network", sa.String(16), nullable=False),
        sa.Column("asset", sa.String(16), nullable=False), sa.Column("token", sa.String(120)),
        sa.Column("decimals", sa.Integer(), nullable=False),
        sa.Column("amount_raw", raw, nullable=False),
        sa.Column("platform_raw", raw, nullable=False),
        sa.Column("referral_raw", raw, nullable=False),
        sa.Column("referrer_user_id", sa.String(32), sa.ForeignKey("users.id")),
        sa.Column("referral_status", sa.String(12), nullable=False),
        sa.Column("status", sa.String(16), nullable=False), sa.Column("destination", sa.String(120), nullable=False),
        sa.Column("signature", sa.String(120)), sa.Column("terms", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False))
    for col in ("job_id", "launch_id", "payer_user_id", "referrer_user_id", "status"):
        op.create_index(f"ix_platform_charges_{col}", "platform_charges", [col])


def downgrade():
    op.drop_table("platform_charges")
    op.drop_table("launch_fee_exemptions")
    op.drop_column("automation_terms", "fee_terms")
