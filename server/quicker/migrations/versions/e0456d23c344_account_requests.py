"""Confirmed account creation requests and latest-year account defaults."""

import json
import time
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "e0456d23c344"
down_revision = "d9345c12b233"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "account_requests",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("device_id", sa.String(), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("file_identity", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("account_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index("ix_account_requests_device_id", "account_requests", ["device_id"])
    conn = op.get_bind()
    routes = {}
    for prop, account in conn.execute(sa.text("SELECT property, account FROM routes ORDER BY year")):
        routes[prop] = account
    # Existing reviewed rows must use the same default as newly extracted rows.
    # Locked/entered rows and explicit account choices are never rewritten.
    for row in conn.execute(
        sa.text("SELECT id, revision, data FROM candidates WHERE status IN ('review', 'approved')")
    ):
        data = json.loads(row.data)
        if data.get("account_override"):
            continue
        account = routes.get(data.get("property"))
        if account == data.get("account"):
            continue
        data.update(account=account, duplicate_acknowledged=False)
        revision = row.revision + 1
        conn.execute(
            sa.text("UPDATE candidates SET data=:data, revision=:revision, status='review' WHERE id=:id"),
            {"data": json.dumps(data), "revision": revision, "id": row.id},
        )
        conn.execute(
            sa.text(
                "INSERT INTO audit (id, candidate_id, created, action, snapshot) "
                "VALUES (:id, :candidate, :created, 'latest_account_default', :snapshot)"
            ),
            {
                "id": str(uuid4()),
                "candidate": row.id,
                "created": int(time.time()),
                "snapshot": json.dumps({"revision": revision, "status": "review", "data": data}),
            },
        )


def downgrade():
    op.drop_table("account_requests")
