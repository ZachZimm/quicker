"""Strict single-transaction QIF delivery format shared with server verification."""

import base64
from datetime import date
from uuid import UUID


def decode_qif(content):
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("cp1252")


def marker(item_id):
    return "[Q:" + base64.urlsafe_b64encode(UUID(item_id).bytes).decode().rstrip("=") + "]"


def delivery_memo(item):
    return (item["data"].get("memo", "").strip() + " " + marker(item["id"])).strip()


def render_entry(item):
    d = item["data"]
    kind = item["account_type"]
    if kind not in ("Bank", "Cash", "CCard"):
        raise ValueError("This account type is not supported for automatic entry")
    for key in ("account", "payee", "category"):
        if not d.get(key):
            raise ValueError(f"Missing {key}")
    tags = item.get("tags", [])
    fields = [str(d.get(k) or "") for k in ("account", "payee", "category", "memo")] + tags
    if any(any(ord(c) < 32 for c in v) for v in fields):
        raise ValueError("Entry fields must be single-line text without control characters")
    if d["category"].startswith("[") or "/" in d["category"] or any(":" in t or "/" in t for t in tags):
        raise ValueError("Transfers and ambiguous category/tag separators are not supported")
    if len(d["payee"]) > 63 or len(delivery_memo(item)) > 63:
        raise ValueError(
            "Shorten the payee to 63 characters and memo to 36 characters for Quicken entry; the tracking reference uses 27 memo characters"
        )
    day = date.fromisoformat(d["date"])
    if not 1901 <= day.year <= 2099:
        raise ValueError("Quicken entry dates must be between 1901 and 2099")
    amount = d["amount_minor"]
    if isinstance(amount, bool) or not isinstance(amount, int) or not amount:
        raise ValueError("Entry needs a nonzero amount in cents")
    memo = delivery_memo(item)
    category = d["category"] + ("/" + ":".join(tags) if tags else "")
    total = f"{'-' if amount < 0 else ''}{abs(amount) // 100}.{abs(amount) % 100:02d}"
    lines = [
        "!Option:AutoSwitch",
        "!Account",
        "N" + d["account"],
        "T" + kind,
        "^",
        "!Type:" + kind,
        f"D{day.month}/{day.day}/{day.year}",
        "T" + total,
        "P" + d["payee"],
        "L" + category,
        "M" + memo,
        "^",
    ]
    # Quicken Classic's QIF importer uses the Windows ANSI code page.
    return ("\r\n".join(lines) + "\r\n").encode("cp1252", errors="strict")


def matches(item, row):
    d = item["data"]
    memo = delivery_memo(item)
    return (
        all(row.get(k) == d.get(k) for k in ("account", "payee", "category", "date", "amount_minor"))
        and set(filter(None, row.get("tag", "").split(":"))) == set(item.get("tags", []))
        and row.get("memo", "") == memo
    )
