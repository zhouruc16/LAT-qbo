"""Merge Jan-Apr 2026 bank activity into the REAL RobotX QBO company.

Default = PREVIEW (writes NOTHING). Pass --commit to write.
See conversation/plan for the agreed approach. Idempotent: posts only the
shortfall vs what already exists, so it is safe to re-run.
"""
from __future__ import annotations
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from tiktok_qbo.qbo.env import load_creds
from tiktok_qbo.qbo.client import QboClient, QboError
from robotx_qbo.ingest.eastwest_pdf import parse_eastwest
from robotx_qbo.ingest.chase_pdf import parse_chase

COMMIT = "--commit" in sys.argv
SUPPLIERS = {"yushu": "YuShu Technology", "dobot": "Shenzhen Dobot",
             "booster": "Booster Robotics", "intbot": "Intbot Inc",
             "pudu": "Pudu Robotics", "openlive": "OpenLive Technology",
             "thunder": "Thunder Inter Robotics Group Inc"}
ACCT_MAP = {
    "Sales - Robots": "Sales of Product Income", "Cost of Goods - Robots": "Cost of Goods Sold",
    "Bank Service Charges": "Bank Service Charge", "Owner's Draw - Qin Zhen": "Owner's Withdrawal",
}
CREATE = [("Wages & Salaries", "Expense"), ("Payroll Taxes", "Expense"),
          ("Taxes & Licenses", "Expense"), ("Rent", "Expense"),
          ("Freight & Shipping", "Expense"), ("Meals", "Expense"),
          ("Office Supplies", "Expense"), ("Auto/Fuel", "Expense"),
          ("Vendor Deposits", "Other Current Asset"), ("Ask My Accountant", "Other Current Asset")]


def d2(x): return f"{Decimal(str(x)):.2f}"
def log(m): print(("[WRITE] " if COMMIT else "[preview] ") + m)


class P:
    def __init__(self):
        self.c = QboClient(load_creds(Path(".env.robotx.prod")))
        self.errors = []
        self._reload()

    def _reload(self):
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


def classify_rest(t):
    """For txns that are not sale/purchase/transfer/cosco/phohanoi: return
    (account_name, direction 'in'|'out', vendor_or_None)."""
    d, dl, du = t.description, t.description.lower(), t.description.upper()
    if t.kind == "check" and t.check_no == "0":
        return "Ask My Accountant", "out", None
    if t.kind == "check" and (t.payee or "") == "UPS":
        return "Freight & Shipping", "out", "UPS"
    if t.kind == "check" and (t.payee or "") == "Hilisong CA LLC":
        return "Rent", "out", "Hilisong CA LLC"
    if t.kind == "check":
        return "Wages & Salaries", "out", t.payee
    if t.kind == "cc":
        return "Owner's Draw - Qin Zhen", "out", "Qin Zhen"
    if any(k in du for k in ("IRS", "EDD", "EMPLOYMENT DEVEL")):
        return "Payroll Taxes", "out", ("IRS" if "IRS" in du else "EDD")
    if "CDTFA" in du or "CA DEPT TAX" in du:
        return "Taxes & Licenses", "out", "CDTFA"
    if "seller" in dl and "permit" in dl:
        return "Taxes & Licenses", "out", "CDTFA"
    if t.kind == "fee" or "service charge" in dl:
        return "Bank Service Charges", "out", None
    for n, a in [("OUTBACK", "Meals"), ("FEDEX", "Office Supplies"),
                 ("ARCO", "Auto/Fuel"), ("AMAZON", "Office Supplies")]:
        if n in du:
            return a, "out", None
    return "Ask My Accountant", ("in" if t.amount > 0 else "out"), None


def main():
    p = P()
    print(f"\n{'#'*60}\n{'COMMIT - WRITING TO REAL BOOKS' if COMMIT else 'PREVIEW'}\n{'#'*60}\n")
    BOA = p.names["BOA-0108"]
    EWB = p.names["Standard Business Checking (6972) - 1"]
    DUP = p.names.get("Standard Business Checking (6972) - 2 - 1")
    banks = {"chase": BOA, "eastwest": EWB}

    # 1. CLEANUP
    print("-- cleanup --")
    for dp in p.all("Deposit"):
        if d2(dp["TotalAmt"]) == "45526.54" and dp["TxnDate"] == "2026-02-23":
            log(f"delete placeholder Deposit {dp['Id']}")
            p.post("deposit?operation=delete", {"Id": dp["Id"], "SyncToken": dp["SyncToken"]},
                   f"del deposit {dp['Id']}")
    if DUP:
        log(f"deactivate duplicate acct {DUP}")
        a = p.find("Account", "Id", DUP)
        if a:
            p.post("account", {"Id": DUP, "SyncToken": a["SyncToken"], "Name": a["Name"],
                               "Active": False, "sparse": True}, "deactivate dup")

    # 2. CREATE ACCOUNTS + ITEM
    print("-- create accounts --")
    for name, atype in CREATE:
        if name in p.names:
            print(f"  [exists] {name}"); continue
        log(f"create {name}")
        r = p.post("account", {"Name": name, "AccountType": atype}, f"acct {name}")
        if r and COMMIT:
            p.names[name] = r["Account"]["Id"]
    if not p.find("Item", "Name", "Robots"):
        log("create item Robots")
        p.post("item", {"Name": "Robots", "Type": "Service",
                        "IncomeAccountRef": {"value": p.names["Sales of Product Income"]}},
               "item Robots")
    if COMMIT:
        p._reload()
    item = p.find("Item", "Name", "Robots")
    item_id = item["Id"] if item else None
    AMA = p.aid("Ask My Accountant")

    # 3. OPENING JE (East West)
    print("-- opening balance --")
    if any(j.get("DocNumber") == "RX-OPEN-EWB" for j in p.all("JournalEntry")):
        print("  [exists] EWB opening JE")
    else:
        log("post EWB opening JE $338,168.19 @ 2025-12-31")
        obe = p.names["Opening Balance Equity"]
        p.post("journalentry", {"DocNumber": "RX-OPEN-EWB", "TxnDate": "2025-12-31",
            "PrivateNote": "RobotX East West opening balance",
            "Line": [{"Amount": "338168.19", "DetailType": "JournalEntryLineDetail",
                      "JournalEntryLineDetail": {"PostingType": "Debit", "AccountRef": {"value": EWB}}},
                     {"Amount": "338168.19", "DetailType": "JournalEntryLineDetail",
                      "JournalEntryLineDetail": {"PostingType": "Credit", "AccountRef": {"value": obe}}}]},
               "opening JE")

    # load data
    txns = []
    for f in ["ewb-01.pdf", "ewb-02.pdf", "ewb-03.pdf", "ewb-04.pdf"]:
        txns += parse_eastwest(f"inputs/robotx/{f}")
    for f in ["chase-01.pdf", "chase-02.pdf", "chase-03.pdf", "chase-04.pdf"]:
        txns += parse_chase(f"inputs/robotx/{f}")
    stats = Counter()

    # 4a. SALES (Algi) -> invoice + payment
    print("-- sales --")
    algi = p.customer("Algi Investment Inc")
    existing_inv = {(i["TxnDate"], d2(i["TotalAmt"])) for i in p.all("Invoice")}
    for t in txns:
        if not (t.amount > 0 and "algi" in t.description.lower()):
            continue
        ds, amt = f"{t.date:%Y-%m-%d}", abs(t.amount)
        if (ds, d2(amt)) in existing_inv:
            stats["sale skip"] += 1; continue
        r = p.post("invoice", {"TxnDate": ds, "CustomerRef": {"value": algi},
            "Line": [{"Amount": d2(amt), "DetailType": "SalesItemLineDetail",
                      "Description": t.description[:90],
                      "SalesItemLineDetail": {"ItemRef": {"value": item_id}}}]}, f"invoice {ds}")
        stats["invoice"] += 1
        if r and COMMIT:
            inv_id = r["Invoice"]["Id"]
            p.post("payment", {"TxnDate": ds, "TotalAmt": d2(amt), "CustomerRef": {"value": algi},
                "DepositToAccountRef": {"value": banks[t.account]},
                "Line": [{"Amount": d2(amt), "LinkedTxn": [{"TxnId": inv_id, "TxnType": "Invoice"}]}]},
                   f"payment {ds}")
            stats["payment"] += 1

    # 4b. PURCHASES -> bill + bill payment
    print("-- purchases --")
    existing_bill = {(b.get("VendorRef", {}).get("value"), b["TxnDate"], d2(b["TotalAmt"]))
                     for b in p.all("Bill")}
    for t in txns:
        dl = t.description.lower()
        is_wire = t.amount < 0 and any(s in dl for s in SUPPLIERS)
        is_thun = t.kind == "check" and (t.payee or "").startswith("Thunder")
        if not (is_wire or is_thun):
            continue
        ds, amt = f"{t.date:%Y-%m-%d}", abs(t.amount)
        vname = next((full for key, full in SUPPLIERS.items() if key in dl), None) or "Thunder Inter Robotics Group Inc"
        vid = p.vendor(vname)
        acct = "Vendor Deposits" if "yushu" in dl else "Cost of Goods - Robots"
        if (vid, ds, d2(amt)) in existing_bill:
            stats["bill skip"] += 1; continue
        r = p.post("bill", {"TxnDate": ds, "VendorRef": {"value": vid},
            "Line": [{"Amount": d2(amt), "DetailType": "AccountBasedExpenseLineDetail",
                      "Description": t.description[:90],
                      "AccountBasedExpenseLineDetail": {"AccountRef": {"value": p.aid(acct)}}}]}, f"bill {vname}")
        stats["bill"] += 1
        if r and COMMIT:
            bid = r["Bill"]["Id"]
            p.post("billpayment", {"TxnDate": ds, "TotalAmt": d2(amt), "VendorRef": {"value": vid},
                "PayType": "Check", "CheckPayment": {"BankAccountRef": {"value": banks[t.account]}},
                "Line": [{"Amount": d2(amt), "LinkedTxn": [{"TxnId": bid, "TxnType": "Bill"}]}]},
                   f"billpay {vname}")
            stats["billpayment"] += 1

    # 4c. TRANSFERS (dedup legs -> 3); 15k->7880 handled as check below
    print("-- transfers --")
    existing_tr = {(x["TxnDate"], d2(x["Amount"])) for x in p.all("Transfer")}
    done_tr = set(existing_tr)
    for t in txns:
        dl = t.description.lower()
        own = ("online transfer" in dl) or ("robotx inc" in dl and ("wire" in dl or "fedwire" in dl))
        if not own or "7880" in t.description:
            continue
        ds, amt = f"{t.date:%Y-%m-%d}", abs(t.amount)
        if (ds, d2(amt)) in done_tr:
            continue
        done_tr.add((ds, d2(amt)))
        frm, to = (banks[t.account], banks["chase" if t.account == "eastwest" else "eastwest"]) \
            if t.amount < 0 else (banks["chase" if t.account == "eastwest" else "eastwest"], banks[t.account])
        p.post("transfer", {"FromAccountRef": {"value": frm}, "ToAccountRef": {"value": to},
                            "Amount": d2(amt), "TxnDate": ds}, f"transfer {ds} {amt}")
        stats["transfer"] += 1

    # 4d. Pho Ha Noi $4,294.50 -> payment vs PHN001
    print("-- pho ha noi payment --")
    phn = p.find("Customer", "DisplayName", "Pho Ha Noi")
    inv = None
    for i in p.all("Invoice"):
        if i.get("CustomerRef", {}).get("name") == "Pho Ha Noi" and d2(i["TotalAmt"]) == "4294.50":
            inv = i; break
    pho_t = next((t for t in txns if t.amount > 0 and abs(t.amount) == Decimal("4294.50")), None)
    if phn and inv and pho_t:
        ds = f"{pho_t.date:%Y-%m-%d}"
        already = any(d2(x["TotalAmt"]) == "4294.50" and x.get("CustomerRef", {}).get("value") == phn["Id"]
                      for x in p.all("Payment"))
        if already:
            stats["phohanoi skip"] += 1
        else:
            log(f"payment $4,294.50 vs PHN001 @ {ds}")
            p.post("payment", {"TxnDate": ds, "TotalAmt": "4294.50", "CustomerRef": {"value": phn["Id"]},
                "DepositToAccountRef": {"value": EWB},
                "Line": [{"Amount": "4294.50", "LinkedTxn": [{"TxnId": inv["Id"], "TxnType": "Invoice"}]}]},
                   "phohanoi payment")
            stats["payment"] += 1

    # 4e. EVERYTHING ELSE -> checks/deposits via expected-vs-actual multiset
    print("-- checks / deposits (payroll, tax, fee, owner, POS, unknowns) --")
    exp = defaultdict(list)   # (date, amt, bank, direction) -> [(txn, acct, vendor)]
    for t in txns:
        dl = t.description.lower()
        if t.amount > 0 and "cosco" in dl:
            stats["skip COSCO"] += 1; continue
        if t.amount > 0 and abs(t.amount) == Decimal("4294.50"):
            continue  # pho ha noi handled
        if t.amount > 0 and "algi" in dl:
            continue
        if (t.amount < 0 and any(s in dl for s in SUPPLIERS)) or (t.kind == "check" and (t.payee or "").startswith("Thunder")):
            continue
        own = ("online transfer" in dl) or ("robotx inc" in dl and ("wire" in dl or "fedwire" in dl))
        if own and "7880" not in t.description:
            continue
        acct, direction, vendor = classify_rest(t)
        if own and "7880" in t.description:
            acct, direction, vendor = "Ask My Accountant", "out", None
        bank = banks[t.account]
        exp[(f"{t.date:%Y-%m-%d}", d2(abs(t.amount)), bank, direction)].append((t, acct, vendor))

    act = Counter()
    for r in p.all("Purchase"):
        act[(r["TxnDate"], d2(r["TotalAmt"]), r.get("AccountRef", {}).get("value"), "out")] += 1
    for r in p.all("Deposit"):
        act[(r["TxnDate"], d2(r["TotalAmt"]), r.get("DepositToAccountRef", {}).get("value"), "in")] += 1

    for key, lst in exp.items():
        ds, amt, bank, direction = key
        missing = len(lst) - act.get(key, 0)
        for (t, acct, vendor) in (lst[len(lst) - missing:] if missing > 0 else []):
            memo = f"RobotX | {t.payee or ''} | {t.description[:50]}"
            if direction == "in":
                p.post("deposit", {"DepositToAccountRef": {"value": bank}, "TxnDate": ds,
                    "PrivateNote": memo, "Line": [{"Amount": amt, "DetailType": "DepositLineDetail",
                    "DepositLineDetail": {"AccountRef": {"value": p.aid(acct)}}}]}, f"deposit {ds} {amt}")
                stats["deposit"] += 1
            else:
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

    # 5. REPORT
    print(f"\n{'='*60}\nSTATS: {dict(stats)}")
    if p.errors:
        print(f"ERRORS ({len(p.errors)}):")
        for e in p.errors[:15]:
            print("  ", e)
    if COMMIT:
        print("\nBANK BALANCES (target Chase 403,349.48 / East West 5,526.52):")
        for n in ["BOA-0108", "Standard Business Checking (6972) - 1"]:
            b = p.c.query(f"SELECT CurrentBalance FROM Account WHERE Name='{n}'")["QueryResponse"]["Account"][0]
            print(f"  {n}: ${Decimal(str(b['CurrentBalance'])):,.2f}")
    else:
        print("\nPREVIEW only. Re-run with --commit to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
