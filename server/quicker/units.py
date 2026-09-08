"""Rental assignment and Quicken tags, independent of payment-year account routing."""

import re

from .profile import (
    PROPERTIES,
    VERIFIED_UNIT_MAPPINGS,
    merchant_identity,
    normalize_street,
    washoe_property_from_parcel,
)


def property_units(property_name):
    return next((p["units"] for p in PROPERTIES if p["name"] == property_name), [])


def is_unit_tag(tag):
    return any(tag in p["units"] for p in PROPERTIES)


def quicken_tags(data):
    """Keep the expense tag and the selected rental tag without duplicates."""
    tags = [data.get("tag")]
    if data.get("unit") in property_units(data.get("property")):
        tags.append(data["unit"])
    return list(dict.fromkeys(t for t in tags if t))


def account_identity(value):
    # Separators are cosmetic; missing digits, leading zeros and masked IDs are not.
    return re.sub(r"[\s-]", "", value or "").casefold()


def address_matches(actual, known):
    if not actual or actual.get("role") not in {"service", "job"}:
        return False
    # Unlike property matching, the full street including the unit must match.
    return (
        normalize_street(actual.get("street") or "") == normalize_street(known["street"])
        and (actual.get("city") or "").strip().casefold() == known["city"].casefold()
        and (actual.get("state") or "").strip().casefold().replace("nevada", "nv")
        == known["state"].casefold().replace("nevada", "nv")
    )


def initial_unit_assignment(data):
    result = {**data, "unit": "unresolved", "unit_evidence": None}
    # Model suggestions are not verified unit evidence. Legacy/manual tags are handled separately.
    if is_unit_tag(result.get("tag")):
        result["tag"] = None
    prop = data.get("property")
    if (
        prop
        and data.get("document_type") == "tax"
        and merchant_identity(data.get("payee")) == "Washoe County Treasurer"
        and washoe_property_from_parcel(data.get("parcel")) == prop
    ):
        return {**result, "unit": "whole_property", "unit_evidence": "Verified parcel covers the property."}
    if data.get("document_type") not in {"invoice", "utility"}:
        return result
    matches = []
    for mapping in VERIFIED_UNIT_MAPPINGS:
        if (
            (prop and mapping["property"] != prop)
            or mapping["unit"] not in ["unresolved", *property_units(mapping["property"])]
            or not mapping.get("evidence")
            or merchant_identity(mapping["merchant"]) != merchant_identity(data.get("payee"))
        ):
            continue
        checks = []
        if mapping.get("utility_account"):
            checks.append(
                account_identity(data.get("utility_account")) == account_identity(mapping["utility_account"])
            )
        if mapping.get("service_customer_id"):
            checks.append(
                account_identity(data.get("service_customer_id"))
                == account_identity(mapping["service_customer_id"])
            )
        if mapping.get("address"):
            checks.append(address_matches(data.get("property_address"), mapping["address"]))
        if checks and all(checks):
            matches.append(mapping)
    if len({(m["property"], m["unit"]) for m in matches}) == 1:
        result.update(unit=matches[0]["unit"], unit_evidence=matches[0]["evidence"])
        if not prop:
            result.update(property=matches[0]["property"], property_assignment="verified_unit_identifier")
    elif matches:
        result["unit_evidence"] = "Verified identifiers conflict. Unit remains unresolved."
    return result


def validate_unit(data):
    unit = data.get("unit", "unresolved")
    if unit != "unresolved" and not data.get("property"):
        return "Choose a property before assigning a unit"
    if unit not in ["unresolved", "whole_property", *property_units(data.get("property"))]:
        return "Choose a unit belonging to the selected property"
    if is_unit_tag(data.get("tag")):
        return "Choose the rental in Unit; use the separate tag for expense details"
    return None
