"""Versioned Quicken reference export backups."""

import sqlalchemy as sa
from alembic import op

revision = "c8234b01a122"
down_revision = "b7123a90f011"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "reference_exports",
        sa.Column("sha256", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("created", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("source_modified", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("coverage", sa.JSON(), nullable=False),
    )


def downgrade():
    op.drop_table("reference_exports")
