"""Quicken reference import. Never writes back to Quicken."""

import re

from sqlalchemy import select

from .db import Route, Setting


def catalog(session):
    row = session.get(Setting, "catalog")
    return row.value if row else {"accounts": [], "categories": [], "tags": [], "payees": [], "history": []}


def field(record, prefix):
    return next((line[1:] for line in record if line.startswith(prefix)), "")


def parse_qif(content: str):
    result = {"accounts": {}, "categories": {}, "tags": {}, "payees": [], "history": []}
    section = ""
    record = []
    account = ""
    for line in content.splitlines():
        if line.startswith("!"):
            section = line.strip()
        elif line == "^":
            name = field(record, "N")
            if section == "!Account" and name:
                account = name
                result["accounts"][name] = {"name": name, "type": field(record, "T")}
            elif section in ("!Type:Cat", "!Type:Tag") and name:
                target = "categories" if section == "!Type:Cat" else "tags"
                result[target][name] = {"name": name, "type": "income" if "I" in record else "expense"}
            elif section.startswith("!Type:") and field(record, "P"):
                assignment = field(record, "L")
                category, _, tag = assignment.partition("/")
                entry = {
                    "payee": field(record, "P"),
                    "category": category,
                    "tag": tag,
                    "memo": field(record, "M"),
                    "account": account,
                }
                result["payees" if section == "!Type:Memorized" else "history"].append(entry)
            record = []
        else:
            record.append(line)
    if not result["accounts"] and not result["categories"]:
        raise ValueError(
            "No account or category lists found. Include Account List and Category List in the QIF export."
        )
    for key in ("accounts", "categories", "tags"):
        result[key] = list(result[key].values())
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
        prop = prop.replace("R&KProperties", "R&K Properties")
        yield {"property": prop, "year": year, "account": name}


def import_catalog(session, content):
    ref = parse_qif(content)
    current = session.get(Setting, "catalog")
    if current:
        current.value = ref
    else:
        session.add(Setting(key="catalog", value=ref))
    for route in inferred_routes(ref):
        if session.get(Route, (route["property"], route["year"])) is None:
            session.add(Route(**route))
    session.flush()
    return ref


def route_list(session):
    return [
        {"property": r.property, "year": r.year, "account": r.account}
        for r in session.scalars(select(Route).order_by(Route.property, Route.year))
    ]
