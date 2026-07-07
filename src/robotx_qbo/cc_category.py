"""Merchant -> expense account categorisation for the Chase …7856 card.

categorize(description) -> account_name

Ordered keyword rules; first match wins. More specific keywords come first
(e.g. "amazon web services" before "amazon"). Anything unmatched lands in
"Uncategorized Expense" so it can be refined later. Fee rows are forced to
"Bank Service Charge" by the caller regardless of this map.
"""
from __future__ import annotations

# (keyword substring, account). Case-insensitive, first match wins.
_RULES: list[tuple[str, str]] = [
    # --- software / SaaS / cloud (Dues & Subscriptions) ---
    ("amazon web services", "Dues & Subscriptions"),
    ("aws.amazon", "Dues & Subscriptions"),
    ("google *play", "Dues & Subscriptions"),
    ("intuit", "Dues & Subscriptions"),
    ("openai", "Dues & Subscriptions"),
    ("anthropic", "Dues & Subscriptions"),
    ("claude", "Dues & Subscriptions"),
    ("x corp", "Dues & Subscriptions"),
    ("supabase", "Dues & Subscriptions"),
    ("netlify", "Dues & Subscriptions"),
    ("shopify", "Dues & Subscriptions"),
    ("godaddy", "Dues & Subscriptions"),
    ("hasdata", "Dues & Subscriptions"),
    ("propertyradar", "Dues & Subscriptions"),
    ("docusign", "Dues & Subscriptions"),
    ("cloudflare", "Dues & Subscriptions"),
    ("envato", "Dues & Subscriptions"),
    ("bluehost", "Dues & Subscriptions"),
    ("pdffiller", "Dues & Subscriptions"),
    ("gptdao", "Dues & Subscriptions"),
    ("6sign", "Dues & Subscriptions"),
    ("zachtechnol", "Dues & Subscriptions"),
    ("fiverr", "Dues & Subscriptions"),

    # --- advertising / marketing ---
    ("google *ads", "Advertising & Marketing"),
    ("customink", "Advertising & Marketing"),
    ("360onlineprint", "Advertising & Marketing"),

    # --- travel (airlines, hotels, booking) ---
    ("american air", "Travel"),
    ("southwes", "Travel"),
    ("china easte", "Travel"),
    ("expedia", "Travel"),
    ("super.com", "Travel"),
    ("super+", "Travel"),
    ("hilton", "Travel"),
    ("holiday inn", "Travel"),
    ("vio.com", "Travel"),
    ("panasonic avionics", "Travel"),
    ("dallmayr", "Travel"),

    # --- auto / fuel ---
    ("petro", "Auto/Fuel"),
    ("shell oil", "Auto/Fuel"),
    ("7-eleven", "Auto/Fuel"),
    ("arco", "Auto/Fuel"),
    ("chevron", "Auto/Fuel"),
    ("tire", "Auto/Fuel"),
    ("air/vac", "Auto/Fuel"),

    # --- meals ---
    ("haidilao", "Meals"),
    ("meizhou", "Meals"),
    ("dongpo", "Meals"),
    ("mcdonald", "Meals"),
    ("seapot", "Meals"),
    ("hot pot", "Meals"),
    ("buffet", "Meals"),
    ("trough", "Meals"),

    # --- shipping ---
    ("ups*", "Freight & Shipping"),

    # --- office supplies / hardware ---
    ("amazon", "Office Supplies"),
    ("amzn", "Office Supplies"),
    ("b2b prime", "Office Supplies"),
    ("micro center", "Office Supplies"),
    ("newegg", "Office Supplies"),
    ("customink llc", "Office Supplies"),

    # --- state filing fees / licenses ---
    ("secretary of state", "Taxes & Licenses"),
    ("corporate filings", "Taxes & Licenses"),

    # --- insurance ---
    ("thimble", "Insurance"),

    # --- robot supplier (YuShu / Unitree) ---
    ("yushu", "Cost of Goods Sold"),

    # --- card fees / reversals ---
    ("late fee", "Bank Service Charge"),
    ("foreign transaction fee", "Bank Service Charge"),
    ("cash advance fee", "Bank Service Charge"),
]

FALLBACK = "Uncategorized Expense"

# New accounts this map introduces (name, QBO AccountType).
NEW_ACCOUNTS = [
    ("Advertising & Marketing", "Expense"),
    ("Dues & Subscriptions", "Expense"),
    ("Insurance", "Expense"),
]


def categorize(description: str) -> str:
    dl = description.lower()
    for kw, acct in _RULES:
        if kw in dl:
            return acct
    return FALLBACK
