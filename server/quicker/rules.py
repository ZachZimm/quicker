"""Initial review assignments. Manual review edits take precedence afterward."""

import re

AUTO_INSURANCE_CATEGORIES = {
    "auto & transport:auto insurance",
    "truck insurance",
    "car & truck (insurance)",
    "insurance (business):truck",
    "insurance(truck)",
    "kia insurance",
}
AUTO_INSURANCE_DESCRIPTION = re.compile(
    r"\b(?:auto(?:mobile|mo)?|car|truck|vehicle|motor)\s+insurance\b"
    r"|\binsurance\s+(?:auto(?:mobile)?|car|truck|vehicle)\b"
    r"|\bstate\s+farm\s+mutual\s+automo(?:bile)?\b",
    re.IGNORECASE,
)


def initial_assignments(data):
    result = dict(data)
    description = " ".join(data.get(key) or "" for key in ("payee", "source"))
    category = (data.get("category") or "").casefold()
    if category in AUTO_INSURANCE_CATEGORIES or AUTO_INSURANCE_DESCRIPTION.search(description):
        result.update(
            property="R&K Properties",
            account="R&K Properties",
            account_override=True,
            category="Insurance (Business):Truck",
        )
    return result
