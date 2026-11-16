"""Audited trading-fee waivers and username-sized referral codes."""
from alembic import op
import sqlalchemy as sa
import uuid

revision = '42c9a01f0007'
down_revision = '42c9a01f0006'
branch_labels = depends_on = None


def upgrade():
    op.create_table('trade_fee_exemptions',
        sa.Column('user_id', sa.String(32), sa.ForeignKey('users.id'), primary_key=True),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('reason', sa.String(300), nullable=False),
        sa.Column('changed_by', sa.String(32), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False))
    for table in ('referrals', 'referral_links'):
        with op.batch_alter_table(table) as batch:
            batch.alter_column('code', existing_type=sa.String(16), type_=sa.String(32), existing_nullable=False)
    bind = op.get_bind()
    for user_id, handle in bind.execute(sa.text('SELECT user_id, handle FROM profiles WHERE handle IS NOT NULL')):
        if not handle:
            continue
        code = handle.upper()
        if bind.execute(sa.text('SELECT id FROM referrals WHERE upper(code) = :code'), {'code': code}).first():
            continue  # Never steal a historical code belonging to someone else.
        bind.execute(sa.text('INSERT INTO referrals (id, owner_user_id, code, uses, max_uses) VALUES (:id, :owner, :code, 0, 1000)'),
                     {'id': uuid.uuid4().hex, 'owner': user_id, 'code': code})


def downgrade():
    # Retain username-sized historical attribution. Truncating codes could change ownership.
    op.drop_table('trade_fee_exemptions')
