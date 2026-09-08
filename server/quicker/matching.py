"""Evidence-based duplicate suggestions; dates are never inferred from a match."""

import hashlib
import json
import re
from datetime import date

from .profile import PROPERTIES, canonical_property, merchant_identity


def history_property(account):
    name = re.sub(r"^20\d{2}\s*[- ]\s*", "", account or "")
    name = re.sub(r"\s+20\d{2}$", "", name)
    name = canonical_property(name)
    return name if name in {p["name"] for p in PROPERTIES} else None


def period_bounds(value):
    tokens = re.findall(r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/(?:\d{4}|\d{2})", value or "")
    if len(tokens) != 2:
        return None
    try:
        dates = []
        for token in tokens:
            if "/" in token:
                month, day, year = map(int, token.split("/"))
                dates.append(date(year + 2000 if year < 100 else year, month, day))
            else:
                dates.append(date.fromisoformat(token))
        return dates if 0 <= (dates[1] - dates[0]).days <= 366 else None
    except ValueError:
        return None


def known_unit(tags):
    values = set((tags or "").split(":"))
    matches = {u for p in PROPERTIES for u in p["units"] if u in values}
    return next(iter(matches)) if len(matches) == 1 else None


def scope_compatible(data, other):
    prop, other_prop = canonical_property(data.get("property")), other.get("property")
    if prop and other_prop and prop != other_prop:
        return False
    unit, other_unit = data.get("unit"), other.get("unit")
    known = {u for p in PROPERTIES for u in p["units"]}
    if unit in known and other_unit in known and unit != other_unit:
        return False
    for key in ("parcel", "service_customer_id"):
        if data.get(key) and other.get(key) and data[key] != other[key]:
            return False
    return True


def historical_matches(reference, data):
    if not data.get("payee") or data.get("amount_minor") is None:
        return []
    merchant = merchant_identity(data["payee"])
    period = period_bounds(data.get("service_period"))
    matches = []
    for row in reference["history"]:
        if (
            not row.get("payee")
            or row.get("opening_balance")
            or row.get("transfer")
            or merchant_identity(row["payee"]) != merchant
            or row.get("amount_minor") != data["amount_minor"]
            or not row.get("date")
        ):
            continue
        prop = history_property(row["account"])
        if not scope_compatible(data, {"property": prop, "unit": known_unit(row.get("tag"))}):
            continue
        confidence, reason = None, None
        if data.get("date"):
            days = abs((date.fromisoformat(data["date"]) - date.fromisoformat(row["date"])).days)
            if days == 0:
                confidence, reason = "same_day", "Same payee, amount and transaction date."
            elif days <= 3 and prop and prop == canonical_property(data.get("property")):
                confidence, reason = (
                    "near_date",
                    "Same property, payee and amount; dates are within three days.",
                )
        elif period and prop and prop == canonical_property(data.get("property")):
            if period[0] <= date.fromisoformat(row["date"]) <= period[1]:
                confidence, reason = (
                    "service_period",
                    "Same property, payee and amount within the printed service period.",
                )
        if confidence:
            matches.append({**row, "match_confidence": confidence, "match_reason": reason})
    return matches


def match_key(row):
    fields = {k: row.get(k) for k in ("account", "date", "amount_minor", "category", "tag", "memo", "number")}
    fields["merchant"] = merchant_identity(row.get("payee"))
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()
