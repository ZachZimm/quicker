"""Initial review assignments. Manual review edits take precedence afterward."""

import re

from .profile import PREFERRED_CATEGORIES, canonical_property, merchant_identity

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
    result["property"] = canonical_property(data.get("property"))
    merchant = merchant_identity(data.get("payee"))
    result["merchant"] = merchant
    description = " ".join(data.get(key) or "" for key in ("payee", "source", "memo")).casefold()
    category = (data.get("category") or "").casefold()
    rule, business = None, False
    if (
        category in AUTO_INSURANCE_CATEGORIES or AUTO_INSURANCE_DESCRIPTION.search(description)
    ) and not re.search(r"\b(?:umbrella|homeowners|property insurance)\b", description):
        rule, business = "auto_insurance", True
    elif merchant == "Apple" and ("icloud" in description or abs(data.get("amount_minor") or 0) == 99):
        if not re.search(r"\b(?:game|music|app store|iphone|ipad|macbook)\b", description):
            rule, business = "icloud", True
    elif merchant == "HP All-In Plan" and (
        re.search(r"all[ -]?in", description)
        or (data.get("payee", "").casefold() == "hp" and abs(data.get("amount_minor") or 0) == 1406)
    ):
        rule, business = "hp_all_in", True
    elif merchant == "HP Instant Ink":
        rule, business = "printer_ink", True
    elif merchant == "Sam's Club":
        # Historical usage supports a default, but membership and merchandise are exceptions.
        if not re.search(
            r"\b(?:membership|renewal|grocer\w*|merchandise|supplies)\b", description + " " + category
        ):
            rule, business = "truck_fuel", True
            if not re.search(r"\b(?:gas|fuel|diesel|gasoline)\b", description):
                result["assignment_warning"] = "Confirm this Sam's Club purchase was truck fuel."
    elif merchant == "The Wash Shop":
        rule, business = "truck_wash", True
    elif merchant == "USPS":
        if re.search(r"p\.?\s*o\.?\s*box|lock ?box", description + " " + category):
            rule, business = "po_box", True
        elif "postage" in description:
            rule, business = "postage", True
    elif merchant == "Spectrum Mobile" or (
        merchant == "Spectrum" and re.search(r"\b(?:mobile|cell(?:ular)?|cell phones?)\b", description)
    ):
        rule, business = "mobile", True
    elif merchant in {
        "TMWA",
        "City Of Reno",
        "City Of Sparks",
        "Waste Management",
        "Washoe County Treasurer",
    }:
        rule = {
            "TMWA": "water",
            "City Of Reno": "sewer",
            "City Of Sparks": "sewer",
            "Waste Management": "garbage",
            "Washoe County Treasurer": "property_tax",
        }[merchant]
    if rule:
        result["category"] = PREFERRED_CATEGORIES[rule]
        result["assignment_rule"] = rule
        if business:
            result.update(property="R&K Properties", account=None, account_override=False)
        if rule == "auto_insurance":
            result.update(account="R&K Properties", account_override=True)
    return result
