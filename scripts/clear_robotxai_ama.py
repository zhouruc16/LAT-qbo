"""Clear owner-explained items out of 'Ask My Accountant' (Robotxai production).

Owner's explanations (2026-07-20):
  Money OUT
    - 2025-10-29 Zelle to Benny Xu ................ business trip  -> Travel
    - all transfers to acct ...6591 ............... R&D            -> Research & Development
    - all transfers to acct ...3782 ............... listing        -> Listing Expenses
    - 2x 2026-02-19 Corp E Corp $450 .............. corporate tax filing fees
                                                                    -> Taxes & Licenses
    - checks 4994-4997 (2026-06-04) ............... Employee Expense Reimbursement
  Money IN
    - ALL deposits/wires .......................... Shareholder Investment (Equity)

  Still parked in AMA (owner has not explained):
    - 2026-01-02 $5,281.69 payment to Chase CC ...7856 (RobotX Inc's card)
    - 2026-06-18 $1,170.22 Check 4993

Bank balances unchanged — only categorisation moves. Default = PREVIEW;
pass --commit to write. Idempotent: only touches lines still pointing at AMA.
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

from tiktok_qbo.qbo.env import load_creds
from tiktok_qbo.qbo.client import QboClient, QboError

COMMIT = "--commit" in sys.argv
TAG = "RXAI"

NEW_ACCOUNTS = [
    ("Shareholder Investment", "Equity"),
    ("Travel", "Expense"),
    ("Research & Development", "Expense"),
    ("Listing Expenses", "Expense"),
    ("Taxes & Licenses", "Expense"),
    ("Employee Expense Reimbursement", "Expense"),
]

REIMBURSE_CHECKS = {"4994", "4995", "4996", "4997"}
LEAVE_IN_AMA = ("7856",)          # CC payment — still unexplained
LEAVE_CHECKS = {"4993"}           # still unexplained


def out_target(purchase: dict) -> tuple[str, str] | None:
    """-> (account name, cleared-note) or None to leave in AMA."""
    desc = (purchase.get("Line", [{}])[0].get("Description", "") or "") + " " \
           + (purchase.get("PrivateNote", "") or "")
    doc = purchase.get("DocNumber", "")
    if doc in LEAVE_CHECKS or any(n in desc for n in LEAVE_IN_AMA):
        return None
    if doc in REIMBURSE_CHECKS:
        return "Employee Expense Reimbursement", "Employee expense reimbursement (owner)"
    if "6591" in desc:
        return "Research & Development", "R&D expense (owner)"
    if "3782" in desc:
        return "Listing Expenses", "Listing expense (owner)"
    if "Corp E Corp" in desc:
        return "Taxes & Licenses", "Corporate tax filing fee (owner)"
    if "Zelle" in desc:
        return "Travel", "Business trip (owner)"
    return None


def log(m: str) -> None:
    print(("[WRITE] " if COMMIT else "[preview] ") + m)


class P:
    def __init__(self):
        self.c = QboClient(load_creds(Path(".env.robotxai.prod")))
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


def main() -> int:
    p = P()
    print(f"{'COMMIT — WRITING' if COMMIT else 'PREVIEW (no writes)'}\n")
    ama = p.names.get("Ask My Accountant")
    if not ama:
        print("ERROR: Ask My Accountant account not found", file=sys.stderr)
        return 1
    for name, atype in NEW_ACCOUNTS:
        p.ensure_account(name, atype)

    n_moved = n_left = 0
    moved_total = Decimal("0")

    # ── money out: purchases whose line still points at AMA ──────────────────
    for r in p.all("Purchase"):
        note = r.get("PrivateNote", "") or ""
        if f"{TAG}:" not in note:
            continue
        line = r["Line"][0]
        cur = line.get("AccountBasedExpenseLineDetail", {}).get("AccountRef", {}).get("value")
        if cur != ama:
            continue
        tgt = out_target(r)
        ds, amt = r["TxnDate"], Decimal(str(r["TotalAmt"]))
        if tgt is None:
            log(f"leave    {ds} {amt:>12} in Ask My Accountant (unexplained)")
            n_left += 1
            continue
        acct, cleared = tgt
        aid = p.names.get(acct)
        if not aid:
            log(f"skip     {ds} {amt:>12} -> {acct} (account pending creation)")
            continue
        log(f"re-point {ds} {amt:>12} OUT  AMA -> {acct}")
        full = dict(r)
        full["Line"][0]["AccountBasedExpenseLineDetail"]["AccountRef"] = {"value": aid}
        desc = (line.get("Description") or "").split(" — ")[0]
        full["Line"][0]["Description"] = f"{desc} | {cleared}"[:300]
        p.post("purchase", full, f"repoint OUT {ds} {amt}")
        n_moved += 1
        moved_total -= amt

    # ── money in: deposits whose line still points at AMA ────────────────────
    for r in p.all("Deposit"):
        note = r.get("PrivateNote", "") or ""
        if f"{TAG}:" not in note:
            continue
        line = r["Line"][0]
        cur = line.get("DepositLineDetail", {}).get("AccountRef", {}).get("value")
        if cur != ama:
            continue
        aid = p.names.get("Shareholder Investment")
        ds, amt = r["TxnDate"], Decimal(str(r["TotalAmt"]))
        if not aid:
            log(f"skip     {ds} {amt:>12} -> Shareholder Investment (account pending creation)")
            continue
        log(f"re-point {ds} {amt:>12} IN   AMA -> Shareholder Investment")
        full = dict(r)
        full["Line"][0]["DepositLineDetail"]["AccountRef"] = {"value": aid}
        desc = (line.get("Description") or "").split(" — ")[0]
        full["Line"][0]["Description"] = f"{desc} | Shareholder investment (owner)"[:300]
        p.post("deposit", full, f"repoint IN {ds} {amt}")
        n_moved += 1
        moved_total += amt

    print(f"\n{'Moved' if COMMIT else 'Would move'} {n_moved} entities out of AMA "
          f"(net {moved_total}); {n_left} left in AMA pending owner.")
    if p.errors:
        print(f"\n{len(p.errors)} ERRORS:")
        for e in p.errors:
            print("  ", e)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
