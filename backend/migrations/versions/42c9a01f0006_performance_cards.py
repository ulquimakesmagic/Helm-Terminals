"""Private performance-card preferences and encrypted background media."""
from alembic import op
import sqlalchemy as sa

revision='42c9a01f0006'
down_revision='42c9a01f0005'
branch_labels=depends_on=None


def upgrade():
    op.create_table('performance_cards',
        sa.Column('user_id',sa.String(32),sa.ForeignKey('users.id'),primary_key=True),
        sa.Column('preferences',sa.JSON(),nullable=False),
        sa.Column('media_cipher',sa.LargeBinary(),nullable=True),
        sa.Column('media_info',sa.JSON(),nullable=True))


def downgrade():
    op.drop_table('performance_cards')
