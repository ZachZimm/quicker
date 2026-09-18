"""Durable desktop runs and fresh export events."""

import sqlalchemy as sa
from alembic import op

revision = "d9345c12b233"
down_revision = "c8234b01a122"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "desktop_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("device_id", sa.String(), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index("ix_desktop_runs_device_id", "desktop_runs", ["device_id"])
    op.create_table(
        "export_events",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("device_id", sa.String(), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("sha256", sa.String(), sa.ForeignKey("reference_exports.sha256"), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )


def downgrade():
    op.drop_table("export_events")
    op.drop_table("desktop_runs")
