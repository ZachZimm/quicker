"""Separate rental identity from expense tags on existing review rows."""

import json
from time import time
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "b7123a90f011"
down_revision = "9b9eb6571ac7"
branch_labels = None
depends_on = None

# The installation's frozen migration mapping is private application data.
# Keep legacy_units separate from later edits to the current property directory.
from quicker.private_profile import load_private_profile

UNITS = load_private_profile().get("legacy_units", {})


def upgrade():
    connection = op.get_bind()
    for row in (
        connection.execute(sa.text("SELECT id, data, revision, status FROM candidates")).mappings().all()
    ):
        data = json.loads(row["data"])
        if "unit" in data:
            continue
        data.update(unit="unresolved", unit_evidence=None)
        if data.get("tag") in UNITS.get(data.get("property"), []):
            data.update(unit=data["tag"], tag=None, unit_evidence="Existing Quicken unit tag.")
        elif data.get("document_type") == "tax" and data.get("property_assignment") == "verified_parcel":
            data.update(unit="whole_property", unit_evidence="Verified parcel covers the property.")
        revision_number = row["revision"] + 1
        connection.execute(
            sa.text("UPDATE candidates SET data=:data, revision=:revision WHERE id=:id"),
            {"data": json.dumps(data), "revision": revision_number, "id": row["id"]},
        )
        connection.execute(
            sa.text(
                "INSERT INTO audit (id, candidate_id, created, action, snapshot) "
                "VALUES (:id, :candidate_id, :created, :action, :snapshot)"
            ),
            {
                "id": str(uuid4()),
                "candidate_id": row["id"],
                "created": int(time()),
                "action": "unit_fields_migrated",
                "snapshot": json.dumps({"data": data, "revision": revision_number, "status": row["status"]}),
            },
        )


def downgrade():
    # Keep additive JSON fields and audit history; no bookkeeping data is discarded.
    pass
