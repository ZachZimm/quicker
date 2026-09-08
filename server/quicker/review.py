import hashlib
import json
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from time import time
from uuid import uuid4

from sqlalchemy import select

from .catalog import catalog
from .contracts import Extraction, ReviewFields
from .db import Audit, Candidate, Route
from .matching import historical_matches, match_key, scope_compatible
from .profile import canonical_property, merchant_identity, property_from_address, washoe_property_from_parcel
from .rules import initial_assignments
from .units import initial_unit_assignment, property_units, quicken_tags, validate_unit


class ReviewError(ValueError):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


def signed_minor(value, refund=False):
    if value is None:
        return None
    try:
        amount = Decimal(str(value).replace("$", "").replace(",", "").replace("(", "-").replace(")", ""))
        if not amount.is_finite() or amount * 100 != (amount * 100).to_integral_value():
            return None
        minor = abs(int(amount * 100))
        if minor > 99999999999:
            return None
        return minor if refund else -minor
    except (ValueError, InvalidOperation):
        return None


def resolve_account(session, data):
    result = dict(data)
    result["property"] = canonical_property(result.get("property"))
    if not result.get("account_override"):
        route = None
        if result.get("property") and result.get("date"):
            route = session.get(Route, (result["property"], date.fromisoformat(result["date"]).year))
        result["account"] = route.account if route else None
    return result


def proposals(session, extraction: Extraction):
    ref = catalog(session)
    category_names = {c["name"] for c in ref["categories"]}
    tag_names = {c["name"] for c in ref["tags"]}
    ignored = []
    proposed = []
    for source_index, row in enumerate(extraction.transactions):
        if extraction.document_type == "utility" and row.amount_basis in {
            "statement_total",
            "component",
            "illustration",
        }:
            ignored.append(
                {"source": row.source, "reason": f"Utility {row.amount_basis} excluded", "page": row.page}
            )
            continue
        if row.crossed_out:
            ignored.append(
                {
                    "source": row.source or row.payee or "Crossed-out item",
                    "reason": "Crossed-out item excluded",
                    "page": row.page,
                }
            )
            continue
        if row.kind in ("payment", "fee", "interest"):
            ignored.append({"source": row.source, "reason": f"{row.kind.title()} excluded", "page": row.page})
            continue
        is_tax = extraction.document_type == "tax" or row.kind == "tax"
        if is_tax and not (row.paid is True and row.date and row.date_basis == "payment"):
            ignored.append(
                {"source": row.source, "reason": "Tax stub has no confirmed paid date", "page": row.page}
            )
            continue
        is_card = extraction.document_type == "credit_card"
        valid_date = row.date if row.date_basis in ("payment", "purchase", "refund") else None
        if not is_card and not is_tax and row.date_basis != "payment":
            valid_date = None
        data = ReviewFields(
            payee=row.payee,
            currency=row.currency.upper(),
            amount_minor=signed_minor(row.amount, row.kind == "refund"),
            date=valid_date,
            category=row.category if row.category in category_names else None,
            tag=row.tag if not is_card and row.tag in tag_names else None,
            property=None if is_card or row.property_address else row.property,
            memo=row.memo,
        ).model_dump(mode="json")
        data.update(
            {
                "source_index": source_index,
                "kind": row.kind,
                "document_type": extraction.document_type,
                "parcel": row.parcel,
                "utility_account": row.utility_account,
                "service_customer_id": row.service_customer_id,
                "billing_customer_id": row.billing_customer_id,
                "invoice_number": row.invoice_number,
                "invoice_date": row.invoice_date.isoformat() if row.invoice_date else None,
                "service_period": row.service_period,
                "amount_basis": row.amount_basis,
                "source": row.source,
                "page": row.page,
            }
        )
        # Business rules use document evidence before historical category suggestions.
        data = initial_assignments(data)
        # Only an unambiguous existing payee category is eligible as a fallback suggestion.
        if not data["category"] and row.payee:
            matches = {
                e["category"]
                for e in ref["payees"] + ref["history"]
                if merchant_identity(e["payee"]) == merchant_identity(row.payee)
                and e["category"] in category_names
                and not e.get("transfer")
                and not e.get("opening_balance")
            }
            if len(matches) == 1:
                data["category"] = matches.pop()
        warnings = list(extraction.warnings) + row.warnings
        if is_tax and merchant_identity(row.payee) == "Washoe County Treasurer":
            matched = washoe_property_from_parcel(row.parcel)
            if matched:
                if data.get("property") and data["property"] != matched:
                    warnings.append("The extracted property conflicts with the verified parcel mapping.")
                data["property"] = matched
                data["property_assignment"] = "verified_parcel"
        if row.property_address:
            address = row.property_address.model_dump(mode="json")
            data["property_address"] = address
            if not is_card and not is_tax:
                matched = property_from_address(address)
                if matched and (not data.get("assignment_rule") or data.get("property") in (None, matched)):
                    data["property"] = matched
                    data["property_assignment"] = "document_address"
                elif matched and data.get("property") != matched:
                    warnings.append(
                        "The document address and business rule identify different properties. Review the assignment."
                    )
                else:
                    warnings.append(
                        "The document address could not be matched to one verified property address."
                    )
        if data.get("assignment_warning"):
            warnings.append(data["assignment_warning"])
        data = initial_unit_assignment(data)
        proposed.append({"data": resolve_account(session, data), "warnings": warnings})
    return proposed, ignored


def insert_candidate(session, document_id, proposal, action="extracted", candidate_id=None):
    candidate = Candidate(
        id=candidate_id or str(uuid4()),
        document_id=document_id,
        revision=1,
        status="review",
        data=proposal["data"],
        warnings=proposal["warnings"],
    )
    session.add(candidate)
    session.flush()
    audit(session, candidate, action)
    invalidate_duplicate_approvals(session)
    return candidate


def create_candidates(session, document_id: str, extraction: Extraction):
    proposed, ignored = proposals(session, extraction)
    for proposal in proposed:
        insert_candidate(session, document_id, proposal)
    return ignored


def duplicates(session, candidate):
    d = candidate.data
    if not all(d.get(k) is not None for k in ("payee", "date", "amount_minor")):
        return []
    return [
        other.id
        for other in session.scalars(
            select(Candidate).where(Candidate.id != candidate.id, Candidate.status != "removed")
        )
        if merchant_identity(other.data.get("payee")) == merchant_identity(d.get("payee"))
        and scope_compatible(d, other.data)
        and all(other.data.get(k) == d.get(k) for k in ("date", "amount_minor"))
    ]


def historical_duplicates(session, candidate):
    return [
        {
            k: row.get(k)
            for k in (
                "id",
                "account",
                "payee",
                "date",
                "amount_minor",
                "category",
                "tag",
                "memo",
                "match_confidence",
                "match_reason",
            )
        }
        for row in historical_matches(catalog(session), candidate.data)
    ]


def reconcile_reference(session, previous, current):
    """Reconcile review state inside the reference import's database transaction."""
    for candidate in session.scalars(
        select(Candidate).where(Candidate.status.in_(["review", "approved", "removed", "existing"]))
    ):
        new_matches = Counter(match_key(row) for row in historical_matches(current, candidate.data))
        old_matches = Counter(match_key(row) for row in historical_matches(previous, candidate.data))
        if candidate.status == "existing":
            if candidate.data.get("existing_match_key") in new_matches:
                continue
            candidate.status = "review"
            candidate.data = {
                **candidate.data,
                "existing_match": None,
                "existing_match_key": None,
                "duplicate_acknowledged": False,
            }
            candidate.revision += 1
            audit(session, candidate, "existing_match_missing")
            continue
        changed_matches = new_matches and new_matches != old_matches
        renamed = canonical_property(candidate.data.get("property")) != candidate.data.get("property")
        if not renamed and not changed_matches:
            continue
        candidate.data = resolve_account(session, candidate.data) if renamed else dict(candidate.data)
        if changed_matches:
            candidate.data = {**candidate.data, "duplicate_acknowledged": False}
        if candidate.status != "removed":
            candidate.status = "review"
        candidate.revision += 1
        audit(
            session, candidate, "reference_matches_changed" if changed_matches else "property_alias_migrated"
        )


def duplicate_evidence(session, candidate):
    evidence = {
        "documents": sorted(duplicates(session, candidate)),
        "history": sorted(match_key(row) for row in historical_matches(catalog(session), candidate.data)),
    }
    return hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()


def invalidate_duplicate_approvals(session, exclude=()):
    for row in session.scalars(select(Candidate).where(Candidate.status == "approved")):
        if row.id in exclude:
            continue
        if (duplicates(session, row) or historical_duplicates(session, row)) and (
            not row.data.get("duplicate_acknowledged")
            or row.data.get("duplicate_evidence") != duplicate_evidence(session, row)
        ):
            row.status = "review"
            row.revision += 1
            row.data = {**row.data, "duplicate_acknowledged": False}
            audit(session, row, "document_matches_changed")


def entry_eligible(session, candidate):
    return candidate.status == "approved" and not issues(session, candidate)


def issues(session, candidate):
    d = candidate.data
    ref = catalog(session)
    result = []
    unit_issue = validate_unit(d)
    if unit_issue:
        result.append(unit_issue)
    if any(tag not in {r["name"] for r in ref["tags"]} for tag in quicken_tags(d)):
        result.append("Choose existing Quicken tags for this rental and expense")
    for key, label in (
        ("payee", "Payee"),
        ("date", "Payment / transaction date"),
        ("category", "Category"),
        ("account", "Destination account"),
    ):
        if not d.get(key):
            result.append(f"{label} is required")
    if not d.get("amount_minor"):
        result.append("A nonzero amount is required")
    if d.get("currency") != "USD":
        result.append("Only USD is supported")
    for key, target in (("category", "categories"), ("account", "accounts"), ("tag", "tags")):
        if d.get(key) and d[key] not in {r["name"] for r in ref[target]}:
            result.append(f"Choose an existing {key} from the QIF catalog")
    if d.get("document_type") == "credit_card" and not d.get("property"):
        result.append("Assign the card transaction to a property or business")
    if d.get("property") and not session.scalar(select(Route).where(Route.property == d["property"])):
        result.append("Choose a configured property or business")
    if (duplicates(session, candidate) or historical_duplicates(session, candidate)) and not d.get(
        "duplicate_acknowledged"
    ):
        result.append("Review the possible duplicate and acknowledge it if this is a separate transaction")
    elif d.get("duplicate_acknowledged") and d.get("duplicate_evidence") != duplicate_evidence(
        session, candidate
    ):
        result.append("Possible duplicate evidence changed. Review and confirm it again.")
    return result


def snapshot(candidate):
    return {"revision": candidate.revision, "status": candidate.status, "data": candidate.data}


def audit(session, candidate, action):
    session.add(
        Audit(
            id=str(uuid4()),
            candidate_id=candidate.id,
            created=int(time()),
            action=action,
            snapshot=snapshot(candidate),
        )
    )


def serialize(session, candidate):
    return {
        "id": candidate.id,
        "entry_eligible": entry_eligible(session, candidate),
        "document_id": candidate.document_id,
        **snapshot(candidate),
        "quicken_tags": quicken_tags(candidate.data),
        "warnings": candidate.warnings,
        "issues": [] if candidate.status == "existing" else issues(session, candidate),
        "duplicates": duplicates(session, candidate),
        "historical_duplicates": historical_duplicates(session, candidate),
    }


def apply_action(session, action):
    if len({row.id for row in action.rows}) != len(action.rows):
        raise ReviewError("A transaction can only appear once in a batch")
    candidates = []
    for change in action.rows:
        candidate = session.get(Candidate, change.id)
        if not candidate:
            raise ReviewError("Transaction no longer exists", 404)
        if candidate.revision != change.revision:
            raise ReviewError("This transaction changed in another tab. Reload before saving.", 409)
        if candidate.status not in ("review", "approved", "removed", "existing"):
            raise ReviewError("Entry has started; this transaction can no longer be edited", 409)
        if candidate.status in ("removed", "existing") and action.action not in ("restore", "remove"):
            raise ReviewError("Restore this transaction before editing or approving it")
        if action.action == "restore" and candidate.status not in ("removed", "existing"):
            raise ReviewError("Only removed transactions can be restored")
        if change.fields is not None:
            # Older clients omit unit. Preserve it unless the property changes.
            fields = change.fields.model_dump(mode="json")
            if "unit" not in change.fields.model_fields_set:
                fields["unit"] = candidate.data.get("unit", "unresolved")
            data = {**candidate.data, **fields}
            data["property"] = canonical_property(data.get("property"))
            if data.get("property") != canonical_property(candidate.data.get("property")):
                if "unit" not in change.fields.model_fields_set or data["unit"] not in [
                    "unresolved",
                    "whole_property",
                    *property_units(data.get("property")),
                ]:
                    data["unit"] = "unresolved"
                data["unit_evidence"] = None if data["unit"] == "unresolved" else "Assigned during review."
            elif data.get("unit") != candidate.data.get("unit", "unresolved"):
                data["unit_evidence"] = "Assigned during review."
            unit_issue = validate_unit(data)
            if unit_issue:
                raise ReviewError(unit_issue)
            if candidate.data.get("duplicate_acknowledged") and any(
                data.get(key) != candidate.data.get(key)
                for key in ("payee", "date", "amount_minor", "property", "unit", "account")
            ):
                data["duplicate_acknowledged"] = False
            candidate.data = resolve_account(session, data)
        if action.action == "remove":
            candidate.status = "removed"
        elif action.action in ("restore", "save"):
            candidate.status = "review"
            if action.action == "restore":
                candidate.data = {
                    k: v
                    for k, v in candidate.data.items()
                    if k not in ("existing_match", "existing_match_key", "duplicate_evidence")
                }
                candidate.data = {**candidate.data, "duplicate_acknowledged": False}
        elif action.action == "existing":
            match = next(
                (
                    row
                    for row in historical_matches(catalog(session), candidate.data)
                    if row["id"] == change.existing_id
                ),
                None,
            )
            if not match:
                raise ReviewError("The selected Quicken match changed. Reload before linking it.", 409)
            candidate.data = {
                **candidate.data,
                "existing_match": match,
                "existing_match_key": match_key(match),
                "duplicate_acknowledged": False,
            }
            candidate.status = "existing"
        candidates.append(candidate)
    session.flush()
    for change, candidate in zip(action.rows, candidates):
        if change.fields is not None and candidate.data.get("duplicate_acknowledged"):
            candidate.data = {**candidate.data, "duplicate_evidence": duplicate_evidence(session, candidate)}
    if action.action == "approve":
        invalid = [{"id": c.id, "issues": issues(session, c)} for c in candidates if issues(session, c)]
        if invalid:
            raise ReviewError("Cannot approve: " + "; ".join(invalid[0]["issues"]))
        for candidate in candidates:
            candidate.status = "approved"
    for candidate in candidates:
        candidate.revision += 1
        audit(session, candidate, action.action)
    session.flush()
    invalidate_duplicate_approvals(session, exclude={c.id for c in candidates})
    return [serialize(session, c) for c in candidates]
