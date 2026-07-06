"""Post the May + June 2026 Chase (…0108) statements into the REAL RobotX QBO.

Scope: inputs/robotx_2025/chase-2026-05.pdf + chase-2026-06.pdf only. The
earlier months (Jan–Apr 2026 + 2025) are already in prod; this fills the gap
after the booked Apr-30 balance ($403,349.48) up to the Jun-30 balance
($748,500.63).

Default = PREVIEW (writes NOTHING). Pass --commit to write.
Idempotent: sales dedup on (date, amount); everything else uses an
expected-vs-actual multiset on (date, amount, bank, direction), so re-running
is a no-op.

Classification comes straight from robotx_qbo.classify (owner-confirmed
overrides for the $642,005 Homecoin sale and the $15,000 Accc Inc accounting
fee; all still-unidentified items parked in Ask My Accountant).
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from tiktok_qbo.qbo.env import load_creds
from tiktok_qbo.qbo.client import QboClient, QboError
from robotx_qbo.ingest.chase_pdf import parse_chase
from robotx_qbo.classify import classify

COMMIT = "--commit" in sys.argv
FILES = ["inputs/robotx_2025/chase-2026-05.pdf", "inputs/robotx_2025/chase-2026-06.pdf"]

# classify() account name -> prod Chart-of-Accounts name
ACCT_MAP = {
    "Sales - Robots": "Sales of Product Income",
    "Owner's Draw - Qin Zhen": "Owner's Withdrawal",
    "Bank Service Charges": "Bank Service Charge",
    "Cost of Goods - Robots": "Cost of Goods Sold",
}


def d2(x) -> str:
    return f"{Decimal(str(x)):.2f}"


def log(m: str) -> None:
    print(("[WRITE] " if COMMIT else "[preview] ") + m)


class P:
    def __init__(self):
        self.c = QboClient(load_creds(Path(".env.robotx.prod")))
        self.errors: list[str] = []
        self.names = {a["Name"]: a["Id"] for a in self.all("Account")}

    def all(self, e):
        return self.c.query(f"SELECT * FROM {e}").get("QueryResponse", {}).get(e, [])

    def find(self, e, f, v):
        s = str(v).replace("'", "\\'")
        r = self.c.query(f"SELECT * FROM {e} WHERE {f} = '{s}'").get("QueryResponse", {}).get(e, [])
        return r[0] if r else None

    def aid(self, name):
        return self.names.get(ACCT_MAP.get(name, name))

    def post(self, path, body, label):
        if not COMMIT:
            return {"_": "preview"}
        try:
            return self.c.post(path, body)
        except QboError as ex:
            self.errors.append(f"{label}: {str(ex)[:160]}")
            print("   ERROR", label, "->", str(ex)[:160])
            return None

    def vendor(self, name):
        ex = self.find("Vendor", "DisplayName", name)
        if ex:
            return ex["Id"]
        r = self.post("vendor", {"DisplayName": name}, f"vendor {name}")
        return r["Vendor"]["Id"] if r and COMMIT else f"V-{name}"

    def customer(self, name):
        ex = self.find("Customer", "DisplayName", name)
        if ex:
            return ex["Id"]
        r = self.post("customer", {"DisplayName": name}, f"customer {name}")
        return r["Customer"]["Id"] if r and COMMIT else f"C-{name}"


def main():
    p = P()
    print(f"\n{'#'*60}\n{'COMMIT - WRITING TO REAL BOOKS' if COMMIT else 'PREVIEW (no writes)'}\n{'#'*60}\n")
    BANK = p.names["BOA-0108"]

    cls = []
    for f in FILES:
        cls += classify(parse_chase(f))
    stats = Counter()

    # 1. SALE — $642,005 Homecoin robot purchase -> invoice + payment
    print("-- sale (Homecoin) --")
    item = p.find("Item", "Name", "Robots")
    item_id = item["Id"] if item else None
    existing_inv = {(i["TxnDate"], d2(i["TotalAmt"])) for i in p.all("Invoice")}
    for c in cls:
        if c.category != "sale":
            continue
        t = c.txn
        ds, amt = f"{t.date:%Y-%m-%d}", d2(abs(t.amount))
        cust = p.customer(c.party)
        if (ds, amt) in existing_inv:
            stats["sale skip"] += 1
            continue
        log(f"invoice+payment {ds} ${amt} {c.party}")
        r = p.post("invoice", {"TxnDate": ds, "CustomerRef": {"value": cust},
            "Line": [{"Amount": amt, "DetailType": "SalesItemLineDetail",
                      "Description": t.description[:90],
                      "SalesItemLineDetail": {"ItemRef": {"value": item_id}}}]}, f"invoice {ds}")
        stats["invoice"] += 1
        if r and COMMIT:
            inv_id = r["Invoice"]["Id"]
            p.post("payment", {"TxnDate": ds, "TotalAmt": amt, "CustomerRef": {"value": cust},
                "DepositToAccountRef": {"value": BANK},
                "Line": [{"Amount": amt, "LinkedTxn": [{"TxnId": inv_id, "TxnType": "Invoice"}]}]},
                   f"payment {ds}")
            stats["payment"] += 1

    # 2. EVERYTHING ELSE -> checks (out) / deposits (in) via expected-vs-actual multiset
    print("-- checks / deposits (payroll, tax, owner, fees, parked unknowns) --")
    exp = defaultdict(list)   # (date, amt, bank, direction) -> [(classified, acct_name, vendor)]
    for c in cls:
        if c.category == "sale":
            continue
        t = c.txn
        direction = "in" if t.amount > 0 else "out"
        vendor = c.party if c.party else None
        exp[(f"{t.date:%Y-%m-%d}", d2(abs(t.amount)), BANK, direction)].append((c, c.account_name, vendor))

    act = Counter()
    for r in p.all("Purchase"):
        act[(r["TxnDate"], d2(r["TotalAmt"]), r.get("AccountRef", {}).get("value"), "out")] += 1
    for r in p.all("Deposit"):
        act[(r["TxnDate"], d2(r["TotalAmt"]), r.get("DepositToAccountRef", {}).get("value"), "in")] += 1

    for key, lst in exp.items():
        ds, amt, bank, direction = key
        missing = len(lst) - act.get(key, 0)
        if missing <= 0:
            stats[f"{direction} skip"] += len(lst)
            continue
        for (c, acct, vendor) in lst[len(lst) - missing:]:
            t = c.txn
            memo = f"RobotX | {c.category} | {t.description[:60]}"
            if direction == "in":
                log(f"deposit  {ds} ${amt} -> {acct}")
                p.post("deposit", {"DepositToAccountRef": {"value": bank}, "TxnDate": ds,
                    "PrivateNote": memo, "Line": [{"Amount": amt, "DetailType": "DepositLineDetail",
                    "DepositLineDetail": {"AccountRef": {"value": p.aid(acct)}}}]}, f"deposit {ds} {amt}")
                stats["deposit"] += 1
            else:
                log(f"check    {ds} ${amt} -> {acct}{'  ('+vendor+')' if vendor else ''}")
                body = {"PaymentType": "Check", "AccountRef": {"value": bank}, "TxnDate": ds,
                        "TotalAmt": amt, "PrivateNote": memo,
                        "Line": [{"Amount": amt, "DetailType": "AccountBasedExpenseLineDetail",
                                  "AccountBasedExpenseLineDetail": {"AccountRef": {"value": p.aid(acct)}}}]}
                if vendor:
                    vid = p.vendor(vendor)
                    if COMMIT:
                        body["EntityRef"] = {"value": vid}
                r = p.post("purchase", body, f"check {ds} {amt} {acct}")
                if r is None and "EntityRef" in body:   # retry without entity
                    body.pop("EntityRef")
                    p.post("purchase", body, f"check-retry {ds} {amt}")
                stats["check"] += 1

    # 3. REPORT
    print(f"\n{'='*60}\nSTATS: {dict(stats)}")
    if p.errors:
        print(f"ERRORS ({len(p.errors)}):")
        for e in p.errors[:15]:
            print("  ", e)
    if COMMIT:
        b = p.c.query("SELECT CurrentBalance FROM Account WHERE Name='BOA-0108'")["QueryResponse"]["Account"][0]
        print(f"\nChase (BOA-0108) balance now: ${Decimal(str(b['CurrentBalance'])):,.2f}  (target 748,500.63)")
    else:
        print("\nPREVIEW only. Re-run with --commit to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
