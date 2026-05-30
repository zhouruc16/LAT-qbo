"""Post RobotX's 8 robot purchases as Bills to the QBO sandbox.

7 supplier wires + the Thunder Robotics check (#200). YuShu $70k -> Vendor
Deposits (asset, flagged PENDING); OpenLive $465,450 -> COGS but flagged
PENDING. Direct, idempotent (dedup by vendor+date+amount). Creds .env.robotx.
"""
from __future__ import annotations
from decimal import Decimal
from pathlib import Path

from tiktok_qbo.qbo.env import load_creds
from tiktok_qbo.qbo.client import QboClient
from robotx_qbo.ingest.eastwest_pdf import parse_eastwest
from robotx_qbo.ingest.chase_pdf import parse_chase

SUPPLIERS = {
    "YuShu": "YuShu Technology",
    "Dobot": "Shenzhen Dobot",
    "Booster": "Booster Robotics",
    "Intbot": "Intbot Inc",
    "Pudu": "Pudu Robotics",
    "OpenLive": "OpenLive Technology",
    "Thunder": "Thunder Inter Robotics Group Inc",
}


def _find(client, entity, field, name):
    safe = name.replace("'", "\\'")
    rows = client.query(f"SELECT * FROM {entity} WHERE {field} = '{safe}'") \
                 .get("QueryResponse", {}).get(entity, [])
    return rows[0] if rows else None


def _ensure_account(client, name, atype, sub):
    ex = _find(client, "Account", "Name", name)
    if ex:
        print(f"  [exists] account {name} (Id={ex['Id']})"); return ex["Id"]
    res = client.post("account", {"Name": name, "AccountType": atype, "AccountSubType": sub})
    aid = res["Account"]["Id"]; print(f"  [created] account {name} (Id={aid})"); return aid


def _ensure_vendor(client, name):
    ex = _find(client, "Vendor", "DisplayName", name)
    if ex:
        return ex["Id"]
    return client.post("vendor", {"DisplayName": name})["Vendor"]["Id"]


def _supplier_of(desc):
    dl = desc.lower()
    for key, full in SUPPLIERS.items():
        if key.lower() in dl:
            return key, full
    return None, None


def main():
    creds = load_creds(Path(".env.robotx"))
    client = QboClient(creds)
    print(f"Connected: {creds.environment} realm={creds.realm_id}\n")

    txns = []
    for f in ["ewb-01.pdf", "ewb-02.pdf", "ewb-03.pdf", "ewb-04.pdf"]:
        txns += parse_eastwest(f"inputs/robotx/{f}")
    for f in ["chase-01.pdf", "chase-02.pdf", "chase-03.pdf", "chase-04.pdf"]:
        txns += parse_chase(f"inputs/robotx/{f}")

    # purchases: outgoing wires to suppliers + the Thunder check
    purchases = []
    for t in txns:
        if t.amount < 0 and _supplier_of(t.description)[0]:
            purchases.append(t)
        elif t.kind == "check" and (t.payee or "").startswith("Thunder"):
            purchases.append(t)
    purchases.sort(key=lambda t: t.date)

    print(f"Found {len(purchases)} purchases:")
    for t in purchases:
        src = t.payee if t.kind == "check" else t.description[:45]
        print(f"  {t.date}  {t.amount:>12}  {src}")
    print()

    print("Bootstrapping accounts:")
    cogs_id = _ensure_account(client, "Cost of Goods - Robots", "Cost of Goods Sold", "SuppliesMaterialsCogs")
    dep_id = _ensure_account(client, "Vendor Deposits", "Other Current Asset", "OtherCurrentAssets")
    print()

    print("Posting bills:")
    posted_total = Decimal(0)
    for t in purchases:
        if t.kind == "check":
            vendor_name = t.payee
        else:
            _, vendor_name = _supplier_of(t.description)
        vid = _ensure_vendor(client, vendor_name)

        flagged = ""
        acct_id = cogs_id
        blob = (t.description + (t.payee or "")).lower()
        if "yushu" in blob:
            acct_id = dep_id; flagged = " [PENDING: performance bond?]"
        if "openlive" in blob and abs(t.amount) == Decimal("465450.00"):
            flagged = " [PENDING: import settlement?]"

        amt = abs(t.amount)
        # dedup by vendor+date+amount
        existing = client.query(
            f"SELECT TxnDate, TotalAmt FROM Bill WHERE VendorRef = '{vid}'"
        ).get("QueryResponse", {}).get("Bill", [])
        keys = {(r["TxnDate"], str(Decimal(str(r["TotalAmt"])))) for r in existing}
        if (f"{t.date:%Y-%m-%d}", str(amt)) in keys:
            print(f"  [skip] {t.date} {vendor_name} ${amt} already billed")
            continue

        body = {
            "VendorRef": {"value": vid},
            "TxnDate": f"{t.date:%Y-%m-%d}",
            "PrivateNote": f"RobotXPurchase:{t.txn_id}{flagged}",
            "Line": [{
                "Amount": f"{amt}",
                "DetailType": "AccountBasedExpenseLineDetail",
                "Description": t.description[:100],
                "AccountBasedExpenseLineDetail": {"AccountRef": {"value": acct_id}},
            }],
        }
        res = client.post("bill", body)["Bill"]
        posted_total += amt
        print(f"  [POSTED] {t.date} {vendor_name:<32} ${amt:>12,.2f}  Bill Id={res['Id']}{flagged}")
    print(f"\nPosted bills total: ${posted_total:,.2f}")

    # summary of A/P
    rows = client.query("SELECT Balance FROM Vendor WHERE Active = true") \
                 .get("QueryResponse", {}).get("Vendor", [])
    ap = sum(Decimal(str(r.get("Balance", 0))) for r in rows)
    print(f"Total vendor balances (A/P): ${ap:,.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
