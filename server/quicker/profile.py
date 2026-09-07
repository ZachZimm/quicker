"""The property's bookkeeping conventions, separate from QIF and model protocols."""

import re

# Verified from the property-location fields on the 2026 Washoe tax notices.
# Notice images are reference evidence only, never imported source documents.
WASHOE_PARCELS = {
    "03127109": {"property": "1810 G Street", "address": "1810 G ST", "notice": "IMG_3320.HEIC"},
    "02824308": {"property": "Grose Lane", "address": "2509 GROSE LN", "notice": "IMG_3322.HEIC"},
    "02734118": {"property": "Holman Way", "address": "983 HOLMAN WAY", "notice": "IMG_3324.HEIC"},
    "02734116": {"property": "Holman Circle", "address": "1007 HOLMAN CIR", "notice": "IMG_3326.HEIC"},
    "00714311": {"property": "Bell St.", "address": "1008 BELL ST", "notice": "IMG_3328.HEIC"},
    "00607622": {"property": "Mallard Place", "address": "840 MALLARD PL", "notice": "IMG_3330.HEIC"},
    "02122411": {"property": "Viento Way", "address": "4325 VIENTO WAY", "notice": "IMG_3332.HEIC"},
    "00616208": {"property": "West 6th Street", "address": "1375 W 6TH ST", "notice": "IMG_3334.HEIC"},
}

# Add mappings only after bills or other records establish the rental identity.
# Each entry needs property, unit, merchant, evidence and at least one of:
# utility_account or address {street, city, state}. See README for matching rules.
VERIFIED_UNIT_MAPPINGS = []


def washoe_property_from_parcel(parcel):
    # Accept printed separators, but never guess missing digits or leading zeros.
    normalized = re.sub(r"[\s-]", "", parcel or "")
    return WASHOE_PARCELS.get(normalized, {}).get("property")

PREFERRED_CATEGORIES = {
    "auto_insurance": "Insurance (Business):Truck",
    "icloud": "ICloud",
    "hp_all_in": "All In Plan",
    "printer_ink": "Printer Plan",
    "truck_fuel": "Truck Gas",
    "truck_wash": "Truck Wash",
    "po_box": "P.O. Box-6 Months",
    "postage": "Postage and Delivery (Business)",
    "mobile": "Cell Phones",
    "water": "Water",
    "sewer": "Sewer",
    "garbage": "Garbage",
    "property_tax": "Property Tax",
}

PROPERTIES = [
    {
        "id": "bell",
        "name": "Bell St.",
        "aliases": ["Bell;One Half", "Bell;one half"],
        "units": ["1008 Bell", "2 Bell"],
        "addresses": [{"street": "1008 Bell St", "city": "Reno", "state": "NV"}],
    },
    {"id": "g_street", "name": "1810 G Street", "aliases": ["G Street"], "units": ["G Street"]},
    {"id": "grose", "name": "Grose Lane", "aliases": ["Grose"], "units": []},
    {"id": "holman_circle", "name": "Holman Circle", "aliases": [], "units": ["Holman 07", "Holman 09"]},
    {"id": "holman_way", "name": "Holman Way", "aliases": [], "units": ["Holman 83", "Holman 85"]},
    {"id": "mallard", "name": "Mallard Place", "aliases": ["Mallard"], "units": []},
    {"id": "viento", "name": "Viento Way", "aliases": ["Viento"], "units": []},
    {
        "id": "west_sixth",
        "name": "West 6th Street",
        "aliases": ["West Sixth Street"],
        "units": ["1375-75", "1375-77"],
    },
    {"id": "rk", "name": "R&K Properties", "aliases": ["R&KProperties"], "units": []},
]


def canonical_property(name):
    if not name:
        return name
    for prop in PROPERTIES:
        if name.strip().casefold() in {v.casefold() for v in [prop["name"], *prop["aliases"]]}:
            return prop["name"]
    return name.strip()


def merchant_identity(name):
    """Return a match identity; never replace the original printed payee."""
    text = re.sub(r"\s+", " ", (name or "").strip()).casefold()
    checks = [
        (r"^(?:apple(?:\.com/bill)?|icloud)(?:\b|$)", "Apple"),
        (r"^hp\s*\*?\s*all[ -]?in(?:\b|$)", "HP All-In Plan"),
        (r"^hp instant ink(?:\b|$)", "HP Instant Ink"),
        (r"^hp$", "HP All-In Plan"),
        (r"^sam['’]?s\s*club(?:\b|$)", "Sam's Club"),
        (r"^(?:the )?wash shop(?:\b|$)", "The Wash Shop"),
        (r"^spectrum[ -]+mobile(?:\b|$)", "Spectrum Mobile"),
        (r"^spectrum(?:\b|$)", "Spectrum"),
        (r"^city [o0]f reno(?:\b|$)", "City Of Reno"),
        (r"^city [o0]f sparks(?:\b|$)", "City Of Sparks"),
        (r"^(?:the )?home depot(?:\b|$)", "Home Depot"),
        (r"^state farm mutual automo(?:bile)?(?:\b|$)", "State Farm Auto"),
        (r"^state farm insurance(?:\b|$)", "State Farm Insurance"),
        (r"^tmwa(?:\b|$)", "TMWA"),
        (r"^waste management(?:\b|$)", "Waste Management"),
        (r"^washoe county treasurer(?:\b|$)", "Washoe County Treasurer"),
        (r"^usps(?:\b|$)", "USPS"),
    ]
    for pattern, identity in checks:
        if re.search(pattern, text):
            return identity
    return text


def property_directory(routes):
    result = []
    for name in sorted({r["property"] for r in routes}):
        profile = next((p for p in PROPERTIES if p["name"] == name), None)
        result.append(
            {
                **(profile or {"id": name, "name": name, "aliases": [], "units": []}),
                "accounts": [r for r in routes if r["property"] == name],
            }
        )
    return result


def normalize_street(value):
    words = re.sub(r"[^a-z0-9# -]", " ", value.casefold()).split()
    aliases = {
        "street": "st",
        "avenue": "ave",
        "road": "rd",
        "lane": "ln",
        "drive": "dr",
        "circle": "cir",
        "place": "pl",
        "court": "ct",
        "west": "w",
        "east": "e",
        "north": "n",
        "south": "s",
    }
    return " ".join(aliases.get(word, word) for word in words)


def property_from_address(address):
    """Match a model-read street to verified addresses; never fuzzy-match house numbers."""
    if not address or address.get("role") not in {"service", "job", "utility_customer"}:
        return None
    street = normalize_street(address.get("street") or "")
    # A unit identifies part of this property, not a different destination account.
    street = re.sub(r" (?:apt|apartment|unit|suite|#) ?[a-z0-9-]+$", "", street)
    city = (address.get("city") or "").strip().casefold()
    state = (address.get("state") or "").strip().casefold()
    if state == "nevada":
        state = "nv"
    matches = {
        prop["name"]
        for prop in PROPERTIES
        for known in prop.get("addresses", [])
        if street == normalize_street(known["street"])
        and (not city or city == known["city"].casefold())
        and (not state or state == known["state"].casefold())
    }
    return next(iter(matches)) if len(matches) == 1 else None
