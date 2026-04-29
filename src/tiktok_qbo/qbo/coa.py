"""Bootstrap chart of accounts + Customer record in QBO.

Idempotent: looks up by Name first; only creates if missing.
"""
from __future__ import annotations

from dataclasses import dataclass

from tiktok_qbo.qbo.client import QboClient


# Account spec: (Name, AccountType, AccountSubType, classification)
# AccountType / AccountSubType are QBO-defined enumerations.
ACCOUNT_SPECS: list[tuple[str, str, str]] = [
    ("TikTok Clearing - LELNU",      "Other Current Asset", "OtherCurrentAssets"),
    ("TikTok Reserve - LELNU",       "Other Current Asset", "OtherCurrentAssets"),
    ("Sales - TikTok LELNU",         "Income",              "SalesOfProductIncome"),
    ("Shipping Income - TikTok",     "Income",              "ServiceFeeIncome"),
    ("TikTok Adjustments",           "Other Income",        "OtherMiscellaneousIncome"),
    ("Marketplace Fees - TikTok",    "Expense",             "AdvertisingPromotional"),
    ("Shipping Expense - TikTok",    "Expense",             "ShippingFreightDelivery"),
]

CUSTOMER_NAME = "TikTok Shop LELNU"


@dataclass(frozen=True)
class CoaRefs:
    """QBO IDs for the accounts and customer used by the pipeline."""
    bank_checking_id: str  # BofA Checking - resolved by name lookup
    clearing_id: str
    reserve_id: str
    sales_id: str
    shipping_income_id: str
    adjustments_id: str
    fees_id: str
    shipping_expense_id: str
    customer_id: str


def _find_account(client: QboClient, name: str) -> dict | None:
    safe = name.replace("'", "\\'")
    res = client.query(f"SELECT * FROM Account WHERE Name = '{safe}'")
    rows = res.get("QueryResponse", {}).get("Account", [])
    return rows[0] if rows else None


def _find_customer(client: QboClient, name: str) -> dict | None:
    safe = name.replace("'", "\\'")
    res = client.query(f"SELECT * FROM Customer WHERE DisplayName = '{safe}'")
    rows = res.get("QueryResponse", {}).get("Customer", [])
    return rows[0] if rows else None


def _create_account(client: QboClient, name: str, acct_type: str, sub_type: str) -> dict:
    body = {
        "Name": name,
        "AccountType": acct_type,
        "AccountSubType": sub_type,
    }
    res = client.post("account", body)
    return res.get("Account", {})


def _create_customer(client: QboClient, name: str) -> dict:
    body = {"DisplayName": name, "CompanyName": name}
    res = client.post("customer", body)
    return res.get("Customer", {})


def _find_bank_checking(client: QboClient) -> dict:
    """Find the BofA Checking bank account by AccountType=Bank."""
    res = client.query("SELECT * FROM Account WHERE AccountType = 'Bank'")
    rows = res.get("QueryResponse", {}).get("Account", [])
    if not rows:
        raise RuntimeError(
            "No Bank account found in QBO. Add a checking account first "
            "(Settings -> Chart of accounts -> New -> Bank)."
        )
    # Prefer one with 'BofA' or '9247' in name; else first.
    for r in rows:
        name = r.get("Name", "")
        if "BofA" in name or "9247" in name or "Bank of America" in name:
            return r
    return rows[0]


def bootstrap_coa(client: QboClient) -> CoaRefs:
    """Look up or create all required accounts and the customer record.

    Returns the CoaRefs with QBO IDs.
    """
    name_to_id: dict[str, str] = {}
    for name, atype, sub in ACCOUNT_SPECS:
        existing = _find_account(client, name)
        if existing:
            name_to_id[name] = existing["Id"]
            print(f"  [exists] {name} (Id={existing['Id']})")
        else:
            created = _create_account(client, name, atype, sub)
            name_to_id[name] = created["Id"]
            print(f"  [created] {name} (Id={created['Id']})")

    customer = _find_customer(client, CUSTOMER_NAME)
    if customer:
        cust_id = customer["Id"]
        print(f"  [exists] Customer {CUSTOMER_NAME} (Id={cust_id})")
    else:
        created = _create_customer(client, CUSTOMER_NAME)
        cust_id = created["Id"]
        print(f"  [created] Customer {CUSTOMER_NAME} (Id={cust_id})")

    bank = _find_bank_checking(client)
    print(f"  [bank]    {bank['Name']} (Id={bank['Id']})")

    return CoaRefs(
        bank_checking_id=bank["Id"],
        clearing_id=name_to_id["TikTok Clearing - LELNU"],
        reserve_id=name_to_id["TikTok Reserve - LELNU"],
        sales_id=name_to_id["Sales - TikTok LELNU"],
        shipping_income_id=name_to_id["Shipping Income - TikTok"],
        adjustments_id=name_to_id["TikTok Adjustments"],
        fees_id=name_to_id["Marketplace Fees - TikTok"],
        shipping_expense_id=name_to_id["Shipping Expense - TikTok"],
        customer_id=cust_id,
    )
