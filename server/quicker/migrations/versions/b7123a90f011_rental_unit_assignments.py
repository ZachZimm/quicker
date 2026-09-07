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

# Frozen migration data; later profile edits must not change this migration.
UNITS = {
    "Bell St.": ["1008 Bell", "2 Bell"],
    "1810 G Street": ["G Street"],
    "Holman Circle": ["Holman 07", "Holman 09"],
    "Holman Way": ["Holman 83", "Holman 85"],
    "West 6th Street": ["1375-75", "1375-77"],
}


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
