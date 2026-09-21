"""Track repeated document resource failures separately from analysis attempts."""

import sqlalchemy as sa
from alembic import op

revision = "f1567e34d455"
down_revision = "e0456d23c344"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("jobs", sa.Column("resource_failures", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    op.drop_column("jobs", "resource_failures")
