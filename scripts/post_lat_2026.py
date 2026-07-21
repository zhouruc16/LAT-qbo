"""Post LAT Group Inc.'s BofA checking activity, Jan-Jun 2026, into the LAT
production QBO company (realm 9341456923092840, .env.lat.prod).

Source: BofA CSV export (stmt.csv) — 395 txns verified: beginning $24,568.71
+ net $19,092.83 = ending $43,661.54, running-balance chain unbroken.

The company file has QBO's default chart of accounts and ZERO transactions.
This script:
  1. creates "BofA Checking" (Bank) + "Ask My Accountant" (Expense),
  2. posts an opening-balance JE (LAT26-OPEN, 2025-12-31): Debit bank
     $24,568.71 / Credit Opening balance equity,
  3. posts every CSV row: Deposit (inflow) or Purchase/Check (outflow).
     External-transfer/wire fees -> "Bank fees & service charges" (existing);
     EVERYTHING else -> "Ask My Accountant" with a counterparty-group memo,
     pending owner clarification (same flow as RobotX / RobotX AI).

Idempotent: every entity tagged in PrivateNote "LAT26:<row#>:<hash>"; tagged
rows are skipped on re-run. Default = PREVIEW; pass --commit to write.
"""
from __future__ import annotations

import csv
import hashlib
import re
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from tiktok_qbo.qbo.env import load_creds
from tiktok_qbo.qbo.client import QboClient, QboError

COMMIT = "--commit" in sys.argv
ENVFILE = ".env.lat.prod"
CSV_PATH = Path(r"C:\Users\zhour\OneDrive\文档\xwechat_files"
                r"\wxid_fxfdqpu6s73512_6ce5\msg\file\2026-07\stmt.csv")
TAG = "LAT26"

BANK_NAME = "BofA Checking"
AMA = "Ask My Accountant"
FEES = "Bank fees & service charges"     # exists in LAT's default CoA
OBE = "Opening balance equity"           # exists in LAT's default CoA

OPEN_DOC = "LAT26-OPEN"
OPEN_DATE = "2025-12-31"
OPEN_AMT = Decimal("24568.71")
EXPECTED_END = Decimal("43661.54")


def money(s: str) -> Decimal:
    return Decimal(s.replace(",", ""))


def log(m: str) -> None:
    print(("[WRITE] " if COMMIT else "[preview] ") + m)


# ── counterparty grouping (drives the AMA memo + the owner's review sheet) ────
def group_of(desc: str, amt: Decimal) -> str:
    d = desc
    if "TikTok Inc DES:PAYMENT" in d:
        return "TikTok Inc payout (LELNU)"
    if "TikTok Shop DES" in d:
        return "TikTok Shop payout (LELNU)"
    if "transfer fee" in d.lower() or ("WIRE" in d and "FEE" in d.upper()):
        return "_FEE"
    m = re.search(r"TRANSFER LAT GROUP INC:(.*?) Confirmation", d)
    if m:
        name = m.group(1).strip()
        if "miami trading" in name.lower():
            name = "Miami Trading Zone"
        return f"Transfer to {name}"
    if "REVERSAL LAT GROUP INC:Miami Trading Zone" in d:
        return "Transfer to Miami Trading Zone (reversal)"
    if "CHK 4665" in d:
        return "Transfer to CHK 4665 (BorderX Media LLC)"
    if "CHK 9445" in d:
        return "Transfer to CHK 9445"
    if re.search(r"AMERICAN ?EXPRESS", d, re.I):
        if amt > 0:
            return "American Express transfer in (Qiandai Zhao)"
        return "American Express payment"
    if "RETURN OF POSTED CHECK" in d:
        return "American Express payment (returned)"
    m = re.search(r"Zelle payment to ([A-Za-z .&]+?)(?: for| Conf|$)", d)
    if m:
        return f"Zelle to {m.group(1).strip()}"
    m = re.search(r"Zelle payment from ([A-Za-z .&]+?)(?: for| Conf|$)", d)
    if m:
        return f"Zelle from {m.group(1).strip()}"
    if "Shopify" in d or "SHOPIFY" in d:
        return "Shopify (BorderX Group LLC)"
    if "temu.com" in d:
        return "Temu payout"
    if "SBA EIDL LOAN" in d:
        return "SBA EIDL loan payment"
    if "VEHICLE LOAN" in d:
        return "BofA vehicle loan payment"
    if "AUDI FINANCIAL" in d:
        return "Audi Financial payment"
    if re.search(r"Bank of America Credit Card|BANK OF AMERICA CREDIT CARD", d):
        return "BofA credit card payment"
    if "SO CAL EDISON" in d:
        return "SoCal Edison (electricity)"
    if "TMOBILE" in d:
        return "T-Mobile (phone)"
    if "FRANCHISE TAX BO" in d:
        return "CA Franchise Tax Board"
    if "NORDSTROM" in d:
        return "Nordstrom payment (Qiandai Zhao)"
    if "WIRE TYPE:WIRE OUT" in d:
        return "Wire out"
    if "Whatnot" in d:
        return "Whatnot payout"
    m = re.search(r"^Check (\d+)", d)
    if m:
        return f"Check {m.group(1)}"
    return "Other"


def load_rows() -> list[tuple[int, str, str, Decimal]]:
    """-> [(row#, iso-date, description, amount)]"""
    rows = list(csv.reader(CSV_PATH.open(encoding="utf-8-sig")))
    hdr = next(i for i, r in enumerate(rows) if r and r[0] == "Date")
    out = []
    n = 0
    for r in rows[hdr + 1:]:
        if len(r) < 4 or not r[0] or not r[2]:
            continue
        n += 1
        mm, dd, yyyy = r[0].split("/")
        out.append((n, f"{yyyy}-{mm}-{dd}", r[1].strip(), money(r[2])))
    return out


def row_tag(n: int, ds: str, desc: str, amt: Decimal) -> str:
    h = hashlib.sha256(f"{ds}|{amt}|{desc}".encode()).hexdigest()[:10]
    return f"{TAG}:{n:03d}:{h}"


class P:
    def __init__(self):
        self.c = QboClient(load_creds(Path(ENVFILE)))
        self.errors: list[str] = []
        self.names = {a["Name"]: a["Id"] for a in self.all("Account")}

    def all(self, e):
        out, pos = [], 1
        while True:
            page = self.c.query(f"SELECT * FROM {e} STARTPOSITION {pos} MAXRESULTS 1000") \
                .get("QueryResponse", {}).get(e, [])
            out += page
            if len(page) < 1000:
                return out
            pos += 1000

    def post(self, path, body, label):
        if not COMMIT:
            return {"_": "preview"}
        try:
            return self.c.post(path, body)
        except QboError as ex:
            self.errors.append(f"{label}: {str(ex)[:160]}")
            print("   ERROR", label, "->", str(ex)[:160])
            return None

    def ensure_account(self, name, atype):
        if name in self.names:
            return self.names[name]
        log(f"create account {name} ({atype})")
        r = self.post("account", {"Name": name, "AccountType": atype}, f"acct {name}")
        if r and COMMIT:
            self.names[name] = r["Account"]["Id"]
            return self.names[name]
        return None

    def existing_tags(self) -> set[str]:
        tags = set()
        for e in ("Deposit", "Purchase"):
            for row in self.all(e):
                note = row.get("PrivateNote", "") or ""
                if f"{TAG}:" in note:
                    tags.add(note.split(" | ")[0].strip())
        return tags


def main() -> int:
    rows = load_rows()
    net = sum(a for *_, a in rows)
    print(f"Parsed {len(rows)} txns; beg {OPEN_AMT} + net {net} = {OPEN_AMT + net} "
          f"(expected {EXPECTED_END})")
    if OPEN_AMT + net != EXPECTED_END:
        print("FATAL: does not tie to ending balance — aborting.", file=sys.stderr)
        return 1

    p = P()
    print(f"Connected. Company has {len(p.names)} accounts.\n")
    bank = p.ensure_account(BANK_NAME, "Bank")
    ama = p.ensure_account(AMA, "Expense")
    fees = p.names.get(FEES)
    obe = p.names.get(OBE)
    if COMMIT and (not fees or not obe):
        print(f"FATAL: expected existing accounts missing: "
              f"{FEES if not fees else ''} {OBE if not obe else ''}", file=sys.stderr)
        return 1

    # opening balance JE
    if COMMIT:
        exist = self_je = p.c.query(
            f"SELECT * FROM JournalEntry WHERE DocNumber = '{OPEN_DOC}'") \
            .get("QueryResponse", {}).get("JournalEntry", [])
    else:
        exist = []
    if exist:
        print(f"opening JE {OPEN_DOC} already present — skipping.")
    else:
        log(f"opening JE {OPEN_DOC} {OPEN_DATE}: Debit {BANK_NAME} {OPEN_AMT} / "
            f"Credit {OBE}")
        p.post("journalentry", {
            "DocNumber": OPEN_DOC, "TxnDate": OPEN_DATE,
            "PrivateNote": f"{TAG}: opening balance per BofA stmt 01/01/2026",
            "Line": [
                {"Amount": float(OPEN_AMT), "DetailType": "JournalEntryLineDetail",
                 "JournalEntryLineDetail": {"PostingType": "Debit",
                                            "AccountRef": {"value": bank}}},
                {"Amount": float(OPEN_AMT), "DetailType": "JournalEntryLineDetail",
                 "JournalEntryLineDetail": {"PostingType": "Credit",
                                            "AccountRef": {"value": obe}}},
            ]}, "opening JE")

    done = p.existing_tags() if COMMIT else set()
    if done:
        print(f"{len(done)} rows already posted — skipping those.")

    by_group: dict[str, list] = defaultdict(lambda: [0, Decimal(0)])
    n_posted = n_skipped = 0
    for n, ds, desc, amt in rows:
        g = group_of(desc, amt)
        is_fee = g == "_FEE"
        acct_name = FEES if is_fee else AMA
        by_group[FEES if is_fee else g][0] += 1
        by_group[FEES if is_fee else g][1] += amt
        tag = row_tag(n, ds, desc, amt)
        if tag in done:
            n_skipped += 1
            continue
        aid = fees if is_fee else ama
        memo = desc if is_fee else f"[{g}] {desc}"
        note = f"{tag} | {desc[:150]}"
        if amt > 0:
            body = {"DepositToAccountRef": {"value": bank}, "TxnDate": ds,
                    "PrivateNote": note,
                    "Line": [{"Amount": float(amt), "DetailType": "DepositLineDetail",
                              "DepositLineDetail": {"AccountRef": {"value": aid}},
                              "Description": memo[:300]}]}
            p.post("deposit", body, f"deposit {ds} {amt}")
        else:
            body = {"AccountRef": {"value": bank}, "PaymentType": "Check",
                    "TxnDate": ds, "PrivateNote": note,
                    "Line": [{"Amount": float(-amt),
                              "DetailType": "AccountBasedExpenseLineDetail",
                              "AccountBasedExpenseLineDetail": {"AccountRef": {"value": aid}},
                              "Description": memo[:300]}]}
            m = re.search(r"^Check (\d+)", desc)
            if m:
                body["DocNumber"] = m.group(1)
            p.post("purchase", body, f"purchase {ds} {amt}")
        n_posted += 1
        if COMMIT and n_posted % 50 == 0:
            print(f"   ... {n_posted} posted")

    print(f"\n{'WROTE' if COMMIT else 'Would write'} {n_posted} entities "
          f"({n_skipped} already present) + opening JE.")
    print("\nBy counterparty group (count, net):")
    for g, (cnt, tot) in sorted(by_group.items(), key=lambda x: -abs(x[1][1])):
        print(f"  {cnt:4d}x {tot:>13} {g}")
    print(f"\nRegister should end at: {OPEN_AMT + net}")
    if p.errors:
        print(f"\n{len(p.errors)} ERRORS:")
        for e in p.errors:
            print("  ", e)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
