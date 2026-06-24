"""Post 2025 RobotX bank activity (May–Dec 2025) into QBO and re-open the banks
from their true 2025 start instead of the 12/31/2025 lump opening.

What it does (idempotent; safe to re-run):
  1. Ensure the few new accounts exist (Outside Services).
  2. OPENING SURGERY: delete the old 12/31/2025 lump-opening JE
     (DocNumber OPEN-BAL / RX-OPEN-EWB) and post a Chase opening JE of
     $47,500.00 @ 2025-11-28 (East West truly opened at $0 in May 2025, so it
     needs no opening — its 2025 activity builds the balance).
  3. Post every 2025 transaction per robotx_qbo.classify:
       sale     -> Invoice + received Payment (to the receiving bank)
       purchase -> Bill + Bill Payment (from the bank)
       else     -> Purchase/Check (outflow) or Deposit (inflow) to its account
     Unknowns land in "Ask My Accountant" so the books balance.

Every entity is tagged in PrivateNote with "RX2025:<txn_id>"; anything already
tagged is skipped, so re-running only posts the shortfall.

Default = PREVIEW (writes NOTHING). Pass --commit to write.
--prod uses .env.robotx.prod (production); default is .env.robotx (sandbox).
"""
from __future__ import annotations

import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

from tiktok_qbo.qbo.env import load_creds
from tiktok_qbo.qbo.client import QboClient, QboError
from robotx_qbo.ingest.load import load_2025
from robotx_qbo.classify import classify

COMMIT = "--commit" in sys.argv
PROD = "--prod" in sys.argv
ENVFILE = ".env.robotx.prod" if PROD else ".env.robotx"

SUPPLIER_FULL = {
    "Newton": "Newton Robotics Inc", "Keenon": "KEENON Robotics Inc",
    "Pudu": "Pudu Robotics", "Dobot": "Shenzhen Dobot", "Booster": "Booster Robotics",
    "Intbot": "Intbot Inc", "OpenLive": "OpenLive Technology",
    "YuShu": "YuShu Technology", "Thunder": "Thunder Inter Robotics Group Inc",
}

if PROD:
    # Production already holds the 2026 books AND the 2025 Chase activity (entered
    # separately, deposits booked to Common Stock etc.). Per instruction: leave all
    # existing entries untouched, add only what's missing (the East West 2025 detail).
    BANK_NAME = {"chase": "BOA-0108", "eastwest": "Standard Business Checking (6972) - 1"}
    ACCT_ALIAS = {"Sales - Robots": "Sales of Product Income",
                  "Cost of Goods - Robots": "Cost of Goods Sold",
                  "Bank Service Charges": "Bank Service Charge",
                  "Owner's Draw - Qin Zhen": "Owner's Withdrawal"}
    NEW_ACCOUNTS = [("Travel", "Expense"), ("Outside Services", "Expense"),
                    ("Professional Fees", "Expense")]
    POST_CHASE_OPEN = False   # Chase already opened in prod via existing entries
    SKIP_EXISTING = True      # skip any txn already present (date+amount+bank+dir)
else:
    BANK_NAME = {"chase": "Chase Checking - 0108", "eastwest": "East West Checking - 6972"}
    ACCT_ALIAS = {"Professional Fees": "Legal & Professional Fees"}
    NEW_ACCOUNTS = [("Outside Services", "Expense")]  # Travel / Legal&Prof already exist
    POST_CHASE_OPEN = True
    SKIP_EXISTING = False

OLD_OPEN_DOCS = {"OPEN-BAL", "RX-OPEN-EWB"}
CHASE_OPEN_DOC = "RX-OPEN-CHASE-2025"
CHASE_OPEN_AMT = Decimal("47500.00")
CHASE_OPEN_DATE = "2025-11-28"
TAG = "RX2025"
SALES_ITEM = "Robots"


def d2(x) -> str:
    return f"{Decimal(str(x)):.2f}"


def log(m: str) -> None:
    print(("[WRITE] " if COMMIT else "[preview] ") + m)


class P:
    def __init__(self):
        self.c = QboClient(load_creds(Path(ENVFILE)))
        self.errors: list[str] = []
        self._reload()

    def _reload(self):
        self.names = {a["Name"]: a["Id"] for a in self.all("Account")}

    def all(self, e):
        # Paginate — QBO caps a query at 100 rows by default, which silently
        # broke idempotency once the company held >100 of an entity.
        out, pos = [], 1
        while True:
            page = self.c.query(f"SELECT * FROM {e} STARTPOSITION {pos} MAXRESULTS 1000") \
                .get("QueryResponse", {}).get(e, [])
            out += page
            if len(page) < 1000:
                return out
            pos += 1000

    def find(self, e, f, v):
        s = str(v).replace("'", "\\'")
        r = self.c.query(f"SELECT * FROM {e} WHERE {f} = '{s}'").get("QueryResponse", {}).get(e, [])
        return r[0] if r else None

    def aid(self, name):
        return self.names.get(ACCT_ALIAS.get(name, name))

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

    def done_tags(self):
        """txn_ids already posted (PrivateNote carries RX2025:<id>)."""
        seen = set()
        for e in ["Invoice", "Bill", "Purchase", "Deposit", "Payment", "BillPayment"]:
            for r in self.all(e):
                note = r.get("PrivateNote", "") or ""
                if f"{TAG}:" in note:
                    seen.add(note.split(f"{TAG}:", 1)[1].split()[0])
        return seen


def main():
    p = P()
    print(f"\n{'#'*64}\n{'COMMIT — WRITING TO ' + ENVFILE if COMMIT else 'PREVIEW (' + ENVFILE + ')'}\n{'#'*64}\n")
    chase = p.names[BANK_NAME["chase"]]
    ewb = p.names[BANK_NAME["eastwest"]]
    banks = {"chase": chase, "eastwest": ewb}
    stats = Counter()

    # 1. NEW ACCOUNTS
    print("-- accounts --")
    for name, atype in NEW_ACCOUNTS:
        if name in p.names:
            print(f"  [exists] {name}"); continue
        log(f"create account {name}")
        r = p.post("account", {"Name": name, "AccountType": atype}, f"acct {name}")
        if r and COMMIT:
            p.names[name] = r["Account"]["Id"]

    # 2. OPENING SURGERY
    print("-- opening-balance surgery --")
    for je in p.all("JournalEntry"):
        if je.get("DocNumber") in OLD_OPEN_DOCS:
            log(f"delete old lump-opening JE {je.get('DocNumber')} (Id {je['Id']})")
            p.post("journalentry?operation=delete",
                   {"Id": je["Id"], "SyncToken": je["SyncToken"]}, "del open JE")
    if not POST_CHASE_OPEN:
        print("  [skip] Chase opening JE — Chase already opened in this company")
    elif any(j.get("DocNumber") == CHASE_OPEN_DOC for j in p.all("JournalEntry")):
        print(f"  [exists] {CHASE_OPEN_DOC}")
    else:
        obe = p.names["Opening Balance Equity"]
        log(f"post Chase opening JE ${CHASE_OPEN_AMT:,.2f} @ {CHASE_OPEN_DATE}")
        p.post("journalentry", {
            "DocNumber": CHASE_OPEN_DOC, "TxnDate": CHASE_OPEN_DATE,
            "PrivateNote": "RobotX Chase opening balance (pre-2025-history)",
            "Line": [
                {"Amount": d2(CHASE_OPEN_AMT), "DetailType": "JournalEntryLineDetail",
                 "JournalEntryLineDetail": {"PostingType": "Debit", "AccountRef": {"value": chase}}},
                {"Amount": d2(CHASE_OPEN_AMT), "DetailType": "JournalEntryLineDetail",
                 "JournalEntryLineDetail": {"PostingType": "Credit", "AccountRef": {"value": obe}}},
            ]}, "chase open JE")

    # 3. POST 2025 TRANSACTIONS
    cls = classify(load_2025())
    done = p.done_tags() if (COMMIT or SKIP_EXISTING) else set()
    # Already-present entries (by bank+date+amount+direction) so we never add a
    # transaction the company already has (e.g. the 2025 Chase entries in prod).
    present: set[tuple] = set()
    if SKIP_EXISTING:
        for r in p.all("Deposit"):
            present.add((r.get("DepositToAccountRef", {}).get("value"), r["TxnDate"], d2(r["TotalAmt"]), "in"))
        for r in p.all("Purchase"):
            present.add((r.get("AccountRef", {}).get("value"), r["TxnDate"], d2(r["TotalAmt"]), "out"))
    item = p.find("Item", "Name", SALES_ITEM)
    item_id = item["Id"] if item else None

    print("-- transactions --")
    for c in cls:
        t = c.txn
        if t.txn_id in done:
            stats["skip-tag"] += 1; continue
        ds, amt = f"{t.date:%Y-%m-%d}", abs(t.amount)
        if SKIP_EXISTING and (banks[t.account], ds, d2(amt), "in" if t.amount > 0 else "out") in present:
            stats["skip-exists"] += 1; continue
        note = f"{TAG}:{t.txn_id} | {c.account_name} | {t.description[:48]}"
        bank = banks[t.account]

        if c.qbo_action == "invoice":
            cust = p.customer(c.party or "Customer")
            r = p.post("invoice", {"TxnDate": ds, "CustomerRef": {"value": cust},
                "PrivateNote": note,
                "Line": [{"Amount": d2(amt), "DetailType": "SalesItemLineDetail",
                          "Description": t.description[:90],
                          "SalesItemLineDetail": {"ItemRef": {"value": item_id}}}]}, f"invoice {ds} {amt}")
            stats["invoice"] += 1
            if r and COMMIT:
                p.post("payment", {"TxnDate": ds, "TotalAmt": d2(amt), "CustomerRef": {"value": cust},
                    "PrivateNote": note, "DepositToAccountRef": {"value": bank},
                    "Line": [{"Amount": d2(amt), "LinkedTxn": [{"TxnId": r["Invoice"]["Id"], "TxnType": "Invoice"}]}]},
                    f"payment {ds}")
                stats["payment"] += 1

        elif c.qbo_action == "bill":
            vname = SUPPLIER_FULL.get(c.party, c.party or "Vendor")
            vid = p.vendor(vname)
            r = p.post("bill", {"TxnDate": ds, "VendorRef": {"value": vid}, "PrivateNote": note,
                "Line": [{"Amount": d2(amt), "DetailType": "AccountBasedExpenseLineDetail",
                          "Description": t.description[:90],
                          "AccountBasedExpenseLineDetail": {"AccountRef": {"value": p.aid(c.account_name)}}}]},
                f"bill {vname} {amt}")
            stats["bill"] += 1
            if r and COMMIT:
                p.post("billpayment", {"TxnDate": ds, "TotalAmt": d2(amt), "VendorRef": {"value": vid},
                    "PrivateNote": note, "PayType": "Check",
                    "CheckPayment": {"BankAccountRef": {"value": bank}},
                    "Line": [{"Amount": d2(amt), "LinkedTxn": [{"TxnId": r["Bill"]["Id"], "TxnType": "Bill"}]}]},
                    f"billpay {vname}")
                stats["billpayment"] += 1

        elif t.amount > 0:  # inflow that isn't a sale -> deposit to its account
            p.post("deposit", {"DepositToAccountRef": {"value": bank}, "TxnDate": ds,
                "PrivateNote": note, "Line": [{"Amount": d2(amt), "DetailType": "DepositLineDetail",
                "DepositLineDetail": {"AccountRef": {"value": p.aid(c.account_name)}}}]}, f"deposit {ds} {amt}")
            stats["deposit"] += 1

        else:  # outflow -> check / purchase to its account
            body = {"PaymentType": "Check", "AccountRef": {"value": bank}, "TxnDate": ds,
                    "TotalAmt": d2(amt), "PrivateNote": note,
                    "Line": [{"Amount": d2(amt), "DetailType": "AccountBasedExpenseLineDetail",
                              "AccountBasedExpenseLineDetail": {"AccountRef": {"value": p.aid(c.account_name)}}}]}
            if c.party:
                vid = p.vendor(c.party)
                if COMMIT:
                    body["EntityRef"] = {"value": vid}
            r = p.post("purchase", body, f"check {ds} {amt} {c.account_name}")
            if r is None and "EntityRef" in body:
                body.pop("EntityRef")
                p.post("purchase", body, f"check-retry {ds} {amt}")
            stats["check"] += 1

    # 4. REPORT
    print(f"\n{'='*64}\nSTATS: {dict(stats)}")
    if p.errors:
        print(f"ERRORS ({len(p.errors)}):")
        for e in p.errors[:20]:
            print("  ", e)
    if COMMIT:
        print("\nBANK BALANCES (target after 2025+2026: Chase 403,349.48 / EWB 5,526.52):")
        for n in (BANK_NAME["chase"], BANK_NAME["eastwest"]):
            row = p.c.query(f"SELECT CurrentBalance FROM Account WHERE Name='{n}'")["QueryResponse"]["Account"][0]
            print(f"  {n}: ${Decimal(str(row['CurrentBalance'])):,.2f}")
    else:
        print("\nPREVIEW only. Re-run with --commit to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
