from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from time import time
from uuid import uuid4

from sqlalchemy import select

from .catalog import catalog
from .contracts import Extraction, ReviewFields
from .db import Audit, Candidate, Route
from .profile import canonical_property, merchant_identity, property_from_address
from .rules import initial_assignments


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


def create_candidates(session, document_id: str, extraction: Extraction):
    ref = catalog(session)
    category_names = {c["name"] for c in ref["categories"]}
    tag_names = {c["name"] for c in ref["tags"]}
    ignored = []
    for source_index, row in enumerate(extraction.transactions):
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
        candidate = Candidate(
            id=str(uuid4()),
            document_id=document_id,
            revision=1,
            status="review",
            data=resolve_account(session, data),
            warnings=warnings,
        )
        session.add(candidate)
        session.flush()
        audit(session, candidate, "extracted")
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
        and all(other.data.get(k) == d.get(k) for k in ("date", "amount_minor"))
    ]


def historical_matches(reference, data):
    if not data.get("payee") or not data.get("date") or data.get("amount_minor") is None:
        return []
    merchant = merchant_identity(data["payee"])
    return [
        row
        for row in reference["history"]
        if row.get("payee")
        and not row.get("opening_balance")
        and not row.get("transfer")
        and merchant_identity(row["payee"]) == merchant
        and row.get("amount_minor") == data["amount_minor"]
        and row.get("date") == data["date"]
    ]


def historical_duplicates(session, candidate):
    return [
        {k: row.get(k) for k in ("id", "account", "payee", "date", "amount_minor", "category", "tag", "memo")}
        for row in historical_matches(catalog(session), candidate.data)
    ]


def reconcile_reference(session, previous, current):
    """Reconcile review state inside the reference import's database transaction."""
    for candidate in session.scalars(
        select(Candidate).where(Candidate.status.in_(["review", "approved", "removed"]))
    ):
        new_matches = Counter(row["account"] for row in historical_matches(current, candidate.data))
        old_matches = Counter(row["account"] for row in historical_matches(previous, candidate.data))
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


def issues(session, candidate):
    d = candidate.data
    ref = catalog(session)
    result = []
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
        "document_id": candidate.document_id,
        **snapshot(candidate),
        "warnings": candidate.warnings,
        "issues": issues(session, candidate),
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
        if candidate.status not in ("review", "approved", "removed"):
            raise ReviewError("Entry has started; this transaction can no longer be edited", 409)
        if candidate.status == "removed" and action.action not in ("restore", "remove"):
            raise ReviewError("Restore this transaction before editing or approving it")
        if action.action == "restore" and candidate.status != "removed":
            raise ReviewError("Only removed transactions can be restored")
        if change.fields is not None:
            data = {**candidate.data, **change.fields.model_dump(mode="json")}
            if candidate.data.get("duplicate_acknowledged") and any(
                data.get(key) != candidate.data.get(key) for key in ("payee", "date", "amount_minor")
            ):
                data["duplicate_acknowledged"] = False
            candidate.data = resolve_account(session, data)
        if action.action == "remove":
            candidate.status = "removed"
        elif action.action in ("restore", "save"):
            candidate.status = "review"
        candidates.append(candidate)
    session.flush()
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
    return [serialize(session, c) for c in candidates]
