"""Retire unattended trading and public profiles without deleting financial history."""
from alembic import op
import sqlalchemy as sa

revision = '42c9a01f0005'
down_revision = '42c9a01f0004'
branch_labels = depends_on = None


def upgrade():
    # No signer is called. Submitted/unconfirmed jobs and every receipt are retained for reconciliation.
    op.execute(sa.text("UPDATE automations SET status='cancelled', "
                       "error='Trading automation has been permanently retired' "
                       "WHERE status IN ('scheduled','executing','paused')"))
    op.execute(sa.text("UPDATE jobs SET status='failed' "
                       "WHERE kind IN ('scheduled_buy','scheduled_sell') "
                       "AND status IN ('queued','pending') "
                       "AND NOT EXISTS (SELECT 1 FROM transactions WHERE transactions.job_id=jobs.id "
                       "AND transactions.signature IS NOT NULL)"))
    op.execute(sa.text("UPDATE jobs SET status='unconfirmed' "
                       "WHERE kind IN ('scheduled_buy','scheduled_sell') AND status IN ('queued','pending') "
                       "AND EXISTS (SELECT 1 FROM transactions WHERE transactions.job_id=jobs.id "
                       "AND transactions.signature IS NOT NULL)"))
    op.execute(sa.text('DELETE FROM automation_launch_slots'))
    op.execute(sa.text('UPDATE profiles SET is_public=false'))


def downgrade():
    # Never reactivate historical schedules or republish private accounts during rollback.
    pass
