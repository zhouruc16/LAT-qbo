"""Post the Chase …7856 credit-card statements (Nov 2025 – Jun 2026) into the
REAL RobotX QBO, and correct the mis-booked card payments.

What it does (default = PREVIEW; pass --commit to write):
  1. Create the "Chase CC 7856" Credit Card account + 3 expense accounts
     (Advertising & Marketing, Dues & Subscriptions, Insurance).
  2. Post every card CHARGE / FEE as a credit-card charge, and every merchant
     REFUND as a credit-card credit, categorised by merchant. Card balance
     builds from $0 to $6,176.06.
  3. Payments:
     - The 5 autopays already in the books as Owner's Draw (Feb–Jun) are
       RE-POINTED to the card account -> they become real card payments.
     - The 1 external web payment (01/02, $5,281.69, paid from a non-…0108
       source) is booked as a JournalEntry crediting Owner's Withdrawal
       (owner paid the card personally). FLAGGED for confirmation.

Idempotent: charges use an expected-vs-actual multiset on
(date, amount, account, credit-flag); re-points and the JE are guarded.
"""
from __future__ import annotations

import sys
import glob
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from tiktok_qbo.qbo.env import load_creds
from tiktok_qbo.qbo.client import QboClient, QboError
from robotx_qbo.ingest.chase_cc_pdf import parse_chase_cc
from robotx_qbo.cc_category import categorize, NEW_ACCOUNTS

COMMIT = "--commit" in sys.argv
CC_ACCOUNT = "Chase CC 7856"
BANK = "BOA-0108"
EXT_PAYMENT_DOC = "RX-CC-EXTPAY-0102"   # the 01/02 external web payment JE


def d2(x) -> str:
    return f"{Decimal(str(x)):.2f}"


def log(m: str) -> None:
    print(("[WRITE] " if COMMIT else "[preview] ") + m)


class P:
    def __init__(self):
        self.c = QboClient(load_creds(Path(".env.robotx.prod")))
        self.errors: list[str] = []
        self._reload()

    def _reload(self):
        self.names = {a["Name"]: a["Id"] for a in self.all("Account")}

    def all(self, e):
        out, start = [], 1
        while True:                       # QBO caps queries at 100 rows
            page = self.c.query(f"SELECT * FROM {e} STARTPOSITION {start} MAXRESULTS 100") \
                       .get("QueryResponse", {}).get(e, [])
            out += page
            if len(page) < 100:
                return out
            start += 100

    def aid(self, name):
        return self.names.get(name)

    def post(self, path, body, label):
        if not COMMIT:
            return {"_": "preview"}
        try:
            return self.c.post(path, body)
        except QboError as ex:
            self.errors.append(f"{label}: {str(ex)[:160]}")
            print("   ERROR", label, "->", str(ex)[:160])
            return None


def main():
    p = P()
    print(f"\n{'#'*60}\n{'COMMIT - WRITING TO REAL BOOKS' if COMMIT else 'PREVIEW (no writes)'}\n{'#'*60}\n")

    # 1. ACCOUNTS ---------------------------------------------------------------
    print("-- accounts --")
    for name, atype in NEW_ACCOUNTS + [(CC_ACCOUNT, "Credit Card")]:
        if name in p.names:
            print(f"  [exists] {name}"); continue
        log(f"create {atype} account: {name}")
        r = p.post("account", {"Name": name, "AccountType": atype}, f"acct {name}")
        if r and COMMIT:
            p.names[name] = r["Account"]["Id"]
    if COMMIT:
        p._reload()
    CC = p.names.get(CC_ACCOUNT)
    OWN_DRAW = p.names.get("Owner's Withdrawal")

    # 2. LOAD + CLASSIFY --------------------------------------------------------
    charges, payments = [], []          # charges: (t, acct, is_credit); payments: t
    for f in sorted(glob.glob("inputs/robotx_cc/*.pdf")):
        for t in parse_chase_cc(f):
            if t.kind == "payment":
                payments.append(t)
            elif t.kind == "fee":
                charges.append((t, "Bank Service Charge", False))
            elif t.kind == "refund":
                charges.append((t, categorize(t.description), True))
            else:
                charges.append((t, categorize(t.description), False))

    # 3. CHARGES / FEES / REFUNDS ----------------------------------------------
    print("-- card charges / fees / refunds --")
    exp = defaultdict(list)             # (date, amt, acct, credit) -> [t...]
    for (t, acct, is_credit) in charges:
        exp[(f"{t.date:%Y-%m-%d}", d2(abs(t.amount)), acct, is_credit)].append(t)
    act = Counter()
    for r in p.all("Purchase"):
        if r.get("AccountRef", {}).get("value") != CC:
            continue
        ln = r["Line"][0].get("AccountBasedExpenseLineDetail", {}).get("AccountRef", {}).get("value")
        act[(r["TxnDate"], d2(r["TotalAmt"]), ln, bool(r.get("Credit", False)))] += 1

    stats = Counter()
    for (ds, amt, acct, is_credit), lst in exp.items():
        acct_id = p.aid(acct)
        key = (ds, amt, acct_id, is_credit)
        missing = len(lst) - act.get(key, 0)
        for t in (lst[len(lst) - missing:] if missing > 0 else []):
            body = {"PaymentType": "CreditCard", "AccountRef": {"value": CC},
                    "TxnDate": ds, "PrivateNote": f"RobotX CC 7856 | {t.description[:70]}",
                    "Line": [{"Amount": amt, "DetailType": "AccountBasedExpenseLineDetail",
                              "Description": t.description[:90],
                              "AccountBasedExpenseLineDetail": {"AccountRef": {"value": acct_id}}}]}
            if is_credit:
                body["Credit"] = True
            log(f"{'credit' if is_credit else 'charge'} {ds} ${amt} -> {acct}")
            p.post("purchase", body, f"cc {ds} {amt} {acct}")
            stats["credit" if is_credit else "charge"] += 1

    # 4. PAYMENTS ---------------------------------------------------------------
    print("-- payments (re-point owner-draws + external JE) --")
    # 4a. re-point the 5 bank autopays already booked as Owner's Draw -> card
    cc_paydates = {f"{t.date:%Y-%m-%d}": d2(abs(t.amount)) for t in payments}
    for r in p.all("Purchase"):
        note = (r.get("PrivateNote") or "").lower()
        if "credit crd" not in note:
            continue
        ln = r["Line"][0]["AccountBasedExpenseLineDetail"]["AccountRef"]
        if ln.get("value") == CC:
            stats["payment already"] += 1; continue
        log(f"re-point payment {r['TxnDate']} ${d2(r['TotalAmt'])}  Owner's Draw -> {CC_ACCOUNT}")
        full = dict(r)
        full["Line"][0]["AccountBasedExpenseLineDetail"]["AccountRef"] = {"value": CC}
        full["PrivateNote"] = f"RobotX | CC 7856 payment | {r.get('PrivateNote','')}"[:200]
        p.post("purchase", full, f"repoint {r['TxnDate']}")
        stats["payment repointed"] += 1

    # 4b. external web payment 01/02 $5,281.69 -> JE (Debit CC / Credit Owner's Draw)
    ext = next((t for t in payments if t.date.month == 1 and t.date.day == 2), None)
    have_je = any(j.get("DocNumber") == EXT_PAYMENT_DOC for j in p.all("JournalEntry"))
    if ext and not have_je:
        amt = d2(abs(ext.amount))
        log(f"JE external card payment {ext.date} ${amt}  (Dr {CC_ACCOUNT} / Cr Owner's Withdrawal)  <FLAG>")
        p.post("journalentry", {"DocNumber": EXT_PAYMENT_DOC, "TxnDate": f"{ext.date:%Y-%m-%d}",
            "PrivateNote": "Card paid 01/02 from a non-…0108 source (owner personal) — CONFIRM",
            "Line": [
                {"Amount": amt, "DetailType": "JournalEntryLineDetail",
                 "JournalEntryLineDetail": {"PostingType": "Debit", "AccountRef": {"value": CC}}},
                {"Amount": amt, "DetailType": "JournalEntryLineDetail",
                 "JournalEntryLineDetail": {"PostingType": "Credit", "AccountRef": {"value": OWN_DRAW}}}]},
               "ext payment JE")
        stats["payment external JE"] += 1
    elif have_je:
        stats["payment external already"] += 1

    # 5. REPORT -----------------------------------------------------------------
    print(f"\n{'='*60}\nSTATS: {dict(stats)}")
    if p.errors:
        print(f"ERRORS ({len(p.errors)}):")
        for e in p.errors[:15]:
            print("  ", e)
    if COMMIT:
        p._reload()
        for n in [CC_ACCOUNT, BANK]:
            b = p.c.query(f"SELECT CurrentBalance FROM Account WHERE Name='{n}'")["QueryResponse"]["Account"][0]
            print(f"  {n}: ${Decimal(str(b['CurrentBalance'])):,.2f}")
        print("  (target: Chase CC 7856 = 6,176.06 ; BOA-0108 unchanged = 748,500.63)")
    else:
        print("\nPREVIEW only. Re-run with --commit to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
