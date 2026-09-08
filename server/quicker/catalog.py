"""Quicken reference import. Never writes back to Quicken."""

import hashlib
import re
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from .db import Route, Setting
from .profile import canonical_property, merchant_identity

TRANSACTION_SECTIONS = {
    "!Type:Bank",
    "!Type:Cash",
    "!Type:CCard",
    "!Type:Oth A",
    "!Type:Oth L",
    "!Type:Invst",
}


def catalog(session):
    row = session.get(Setting, "catalog")
    return row.value if row else {"accounts": [], "categories": [], "tags": [], "payees": [], "history": []}


def field(record, prefix):
    return next((line[1:] for line in record if line.startswith(prefix)), "")


def qif_date(value):
    parts = re.findall(r"\d+", value)
    if len(parts) != 3:
        return None
    month, day, year = map(int, parts)
    if year < 100:
        year += 2000 if "'" in value or year < 70 else 1900
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def qif_minor(value):
    try:
        amount = Decimal(value.replace(",", "")) * 100
        if not amount.is_finite() or amount != amount.to_integral_value():
            return None
        return int(amount)
    except (InvalidOperation, ValueError):
        return None


def coverage(ref):
    accounts = []
    for account in ref["accounts"]:
        rows = [r for r in ref["history"] if r["account"] == account["name"]]
        dates = [r["date"] for r in rows if r.get("date")]
        accounts.append(
            {
                "account": account["name"],
                "count": len(rows),
                "first_date": min(dates) if dates else None,
                "last_date": max(dates) if dates else None,
            }
        )
    return {
        "total": len(ref["history"]),
        "accounts": accounts,
        "blank_payees": sum(not r["payee"] for r in ref["history"]),
        "invalid_dates": sum(not r.get("date") for r in ref["history"]),
        "invalid_amounts": sum(r.get("amount_minor") is None for r in ref["history"]),
    }


def parse_qif(content: str):
    result = {"accounts": {}, "categories": {}, "tags": {}, "payees": [], "history": []}
    section, account, record = "", "", []
    occurrences = Counter()
    for line in content.lstrip("\ufeff").splitlines():
        if line.startswith("!"):
            if record:
                raise ValueError("Unterminated QIF record before section header")
            section = line.strip()
            continue
        if line != "^":
            if line or record:
                record.append(line)
            continue
        name = field(record, "N")
        if section == "!Account" and name:
            account = name
            result["accounts"][name] = {"name": name, "type": field(record, "T")}
        elif section in ("!Type:Cat", "!Type:Tag") and name:
            target = "categories" if section == "!Type:Cat" else "tags"
            result[target][name] = {"name": name, "type": "income" if "I" in record else "expense"}
        elif section == "!Type:Memorized" or section in TRANSACTION_SECTIONS:
            category, _, tag = field(record, "L").partition("/")
            entry = {
                "payee": field(record, "P"),
                "merchant": merchant_identity(field(record, "P")),
                "category": category,
                "tag": tag,
                "memo": field(record, "M"),
                "raw": record.copy(),
            }
            if section == "!Type:Memorized":
                # Memorized payees have no owning register. Never inherit the last account header.
                result["payees"].append(entry)
            else:
                if not account:
                    raise ValueError("Transactions have no account header; export with Account List included")
                raw_date, raw_amount = field(record, "D"), field(record, "T") or field(record, "U")
                # Stable when other transactions/accounts are inserted or reordered.
                identity = (
                    account + "\n" + "\n".join(sorted(line for line in record if not line.startswith("C")))
                )
                occurrences[identity] += 1
                identity += f"\noccurrence:{occurrences[identity]}"
                entry.update(
                    id=hashlib.sha256(identity.encode()).hexdigest(),
                    account=account,
                    date=qif_date(raw_date),
                    amount_minor=qif_minor(raw_amount),
                    raw_date=raw_date,
                    raw_amount=raw_amount,
                    number=field(record, "N"),
                    cleared=field(record, "C"),
                    section=section,
                    transfer=category.startswith("["),
                    opening_balance=field(record, "P").casefold() in {"opening balance", "starting balance"},
                )
                result["history"].append(entry)
        record = []
    if record:
        raise ValueError("Unterminated final QIF record; export the file again")
    if not result["accounts"] and not result["categories"]:
        raise ValueError(
            "No account or category lists found. Include Account List and Category List in the QIF export."
        )
    for key in ("accounts", "categories", "tags"):
        result[key] = list(result[key].values())
    result["coverage"] = coverage(result)
    result["digest"] = hashlib.sha256(content.encode()).hexdigest()
    return result


def inferred_routes(ref):
    for account in ref["accounts"]:
        name = account["name"]
        match = re.match(r"^(20\d{2})\s*[- ]\s*(.+)$", name)
        tail = re.match(r"^(.+?)\s+(20\d{2})$", name)
        if match:
            year, prop = int(match[1]), match[2]
        elif tail:
            prop, year = tail[1], int(tail[2])
        else:
            continue
        yield {"property": canonical_property(prop), "year": year, "account": name}


def import_catalog(session, content):
    ref = parse_qif(content)
    previous = catalog(session)
    # Canonicalize existing routes without replacing a deliberate destination override.
    for route in list(session.scalars(select(Route))):
        prop = canonical_property(route.property)
        if prop != route.property:
            target = session.get(Route, (prop, route.year))
            if target and target.account != route.account:
                raise ValueError(
                    f"Conflicting account mappings for {prop} in {route.year}; resolve them before import"
                )
            if not target:
                session.add(Route(property=prop, year=route.year, account=route.account))
            session.delete(route)
            session.flush()
    counts = Counter((r["property"], r["year"]) for r in inferred_routes(ref))
    for route in inferred_routes(ref):
        if session.get(Route, (route["property"], route["year"])) is not None:
            continue
        if counts[(route["property"], route["year"])] > 1:
            raise ValueError(
                f"Multiple accounts for {route['property']} in {route['year']}; an explicit mapping is needed"
            )
        session.add(Route(**route))
    current = session.get(Setting, "catalog")
    if current:
        current.value = ref
    else:
        session.add(Setting(key="catalog", value=ref))
    session.flush()
    if previous.get("digest") != ref["digest"]:
        from .review import reconcile_reference

        reconcile_reference(session, previous, ref)
    session.flush()
    return ref


def route_list(session):
    return [
        {"property": r.property, "year": r.year, "account": r.account}
        for r in session.scalars(select(Route).order_by(Route.property, Route.year))
    ]
