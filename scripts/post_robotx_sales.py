"""Post RobotX's 4 Algi Investment sales as Invoices to the QBO sandbox.

Direct, minimal, idempotent (find-or-create). Reuses the verified statement
parsers + the generic tiktok_qbo.qbo client. Creds from .env.robotx.
"""
from __future__ import annotations
from decimal import Decimal
from pathlib import Path

from tiktok_qbo.qbo.env import load_creds
from tiktok_qbo.qbo.client import QboClient
from robotx_qbo.ingest.eastwest_pdf import parse_eastwest
from robotx_qbo.ingest.chase_pdf import parse_chase

TAG = "RobotXSale:"


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


def _ensure_item(client, name, income_acct_id):
    ex = _find(client, "Item", "Name", name)
    if ex:
        print(f"  [exists] item {name} (Id={ex['Id']})"); return ex["Id"]
    res = client.post("item", {"Name": name, "Type": "Service",
                               "IncomeAccountRef": {"value": income_acct_id}})
    iid = res["Item"]["Id"]; print(f"  [created] item {name} (Id={iid})"); return iid


def _ensure_customer(client, name):
    ex = _find(client, "Customer", "DisplayName", name)
    if ex:
        print(f"  [exists] customer {name} (Id={ex['Id']})"); return ex["Id"]
    res = client.post("customer", {"DisplayName": name})
    cid = res["Customer"]["Id"]; print(f"  [created] customer {name} (Id={cid})"); return cid


def _existing_invoice_keys(client, cust_id):
    """Return set of (TxnDate, TotalAmt) for the customer's invoices (dedup key).

    PrivateNote isn't queryable in QBO, so we dedup on date+amount instead.
    """
    rows = client.query(
        f"SELECT TxnDate, TotalAmt FROM Invoice WHERE CustomerRef = '{cust_id}'"
    ).get("QueryResponse", {}).get("Invoice", [])
    return {(r["TxnDate"], str(Decimal(str(r["TotalAmt"])))) for r in rows}


def main():
    creds = load_creds(Path(".env.robotx"))
    client = QboClient(creds)
    print(f"Connected: {creds.environment} realm={creds.realm_id}\n")

    # gather the 4 Algi sales from the statements
    txns = []
    for f in ["ewb-01.pdf", "ewb-02.pdf", "ewb-03.pdf", "ewb-04.pdf"]:
        txns += parse_eastwest(f"inputs/robotx/{f}")
    for f in ["chase-01.pdf", "chase-02.pdf", "chase-03.pdf", "chase-04.pdf"]:
        txns += parse_chase(f"inputs/robotx/{f}")
    sales = [t for t in txns if t.amount > 0 and "algi" in t.description.lower()]
    sales.sort(key=lambda t: t.date)
    print(f"Found {len(sales)} Algi sales:")
    for t in sales:
        print(f"  {t.date}  {t.amount:>12}  {t.description[:50]}")
    print()

    print("Bootstrapping account / item / customer:")
    income_id = _ensure_account(client, "Sales - Robots", "Income", "SalesOfProductIncome")
    item_id = _ensure_item(client, "Robots", income_id)
    cust_id = _ensure_customer(client, "Algi Investment Inc")
    print()

    print("Posting invoices:")
    existing = _existing_invoice_keys(client, cust_id)
    posted = []
    for t in sales:
        key = (f"{t.date:%Y-%m-%d}", str(abs(t.amount)))
        if key in existing:
            print(f"  [skip] {t.date} ${abs(t.amount)} already invoiced")
            continue
        body = {
            "TxnDate": f"{t.date:%Y-%m-%d}",
            "CustomerRef": {"value": cust_id},
            "PrivateNote": f"{TAG}{t.txn_id} | {t.description}",
            "Line": [{
                "Amount": f"{abs(t.amount)}",
                "DetailType": "SalesItemLineDetail",
                "Description": t.description,
                "SalesItemLineDetail": {"ItemRef": {"value": item_id}},
            }],
        }
        res = client.post("invoice", body)["Invoice"]
        posted.append(res)
        print(f"  [POSTED] {t.date} ${abs(t.amount):>12}  Invoice Id={res['Id']} DocNumber={res.get('DocNumber')}")
    print()

    # read back the customer's invoices to confirm
    rows = client.query(
        f"SELECT Id, DocNumber, TxnDate, TotalAmt FROM Invoice WHERE CustomerRef = '{cust_id}' ORDERBY TxnDate"
    ).get("QueryResponse", {}).get("Invoice", [])
    total = sum(Decimal(str(r["TotalAmt"])) for r in rows)
    print(f"Algi Investment Inc now has {len(rows)} invoice(s), total ${total:,.2f}:")
    for r in rows:
        print(f"  Invoice {r.get('DocNumber')}  {r['TxnDate']}  ${Decimal(str(r['TotalAmt'])):,.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
