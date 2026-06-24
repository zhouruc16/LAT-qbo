"""Apply the owner's ("boss") notes to the already-posted Jan–Apr 2026 books:
re-point the items that were parked in "Ask My Accountant" to their real
accounts (New American Title -> COGS, …7880 -> Rent, the $9k check + teller
cash -> Outside Services, POS Shanghai -> Travel, the $95k/$497,550 deposits
-> Sales).

Method: for every 2026 transaction whose classifier target is NOT "Ask My
Accountant", find the posted Purchase (outflow) or Deposit (inflow) that is
still sitting on Ask My Accountant with the same date+amount, and update just
that line's account. Entries already on the right account are left alone, so
this is idempotent. COSCO ($2,500 in) and the Pho Ha Noi mobile check stay
parked (still unidentified) — the classifier keeps them on Ask My Accountant,
so they are never matched here.

Default = PREVIEW (writes NOTHING). Pass --commit to write.
--prod uses .env.robotx.prod; default is .env.robotx (sandbox).
"""
from __future__ import annotations

import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

from tiktok_qbo.qbo.env import load_creds
from tiktok_qbo.qbo.client import QboClient, QboError
from robotx_qbo.ingest.load import load_all
from robotx_qbo.classify import classify

COMMIT = "--commit" in sys.argv
ENVFILE = ".env.robotx.prod" if "--prod" in sys.argv else ".env.robotx"
ACCT_ALIAS = {"Professional Fees": "Legal & Professional Fees"}


def d2(x) -> str:
    return f"{Decimal(str(x)):.2f}"


def log(m: str) -> None:
    print(("[WRITE] " if COMMIT else "[preview] ") + m)


def main():
    c = QboClient(load_creds(Path(ENVFILE)))
    print(f"\n{'#'*64}\n{'COMMIT — ' + ENVFILE if COMMIT else 'PREVIEW (' + ENVFILE + ')'}\n{'#'*64}\n")

    def all_(e):
        return c.query(f"SELECT * FROM {e}").get("QueryResponse", {}).get(e, [])

    names = {a["Name"]: a["Id"] for a in all_("Account")}
    aid = lambda n: names.get(ACCT_ALIAS.get(n, n))
    ama = aid("Ask My Accountant")

    # Desired account for each 2026 txn whose classifier target is NOT AMA.
    # We only act on entries STILL sitting on Ask My Accountant below, so items
    # already posted correctly (as Invoice/Bill/Transfer) are never matched.
    # Sales are re-pointed to the Sales income account (deposit-to-income keeps
    # the change minimal vs. tearing down to Invoice+Payment).
    want: dict[tuple[str, str, str], tuple[str, bool, str]] = {}
    for cl in classify(load_all()):
        if cl.account_name == "Ask My Accountant":
            continue
        target = "Sales - Robots" if cl.category == "sale" else cl.account_name
        t = cl.txn
        direction = "in" if t.amount > 0 else "out"
        want[(f"{t.date:%Y-%m-%d}", d2(abs(t.amount)), direction)] = (
            target, cl.flagged, cl.memo)

    stats = Counter()

    # Outflows parked on AMA -> Purchase entities.
    for r in all_("Purchase"):
        line = r.get("Line", [{}])[0]
        det = line.get("AccountBasedExpenseLineDetail", {})
        if det.get("AccountRef", {}).get("value") != ama:
            continue
        key = (r["TxnDate"], d2(r["TotalAmt"]), "out")
        if key not in want:
            continue
        target, flagged, memo = want[key]
        log(f"Purchase {r['Id']} {key[0]} ${key[1]} : Ask My Accountant -> {target}"
            + (" [FLAG]" if flagged else ""))
        det["AccountRef"] = {"value": aid(target)}
        note = r.get("PrivateNote", "")
        if flagged and "PENDING" not in note:
            note = (note + " | PENDING: " + memo)[:1000]
        body = {"Id": r["Id"], "SyncToken": r["SyncToken"], "sparse": True,
                "PaymentType": r.get("PaymentType", "Check"),
                "AccountRef": r["AccountRef"], "Line": r["Line"], "PrivateNote": note}
        if COMMIT:
            try:
                c.post("purchase", body)
            except QboError as ex:
                print("   ERROR", str(ex)[:160])
        stats[f"-> {target}"] += 1

    # Inflows parked on AMA -> Deposit entities.
    for r in all_("Deposit"):
        line = r.get("Line", [{}])[0]
        det = line.get("DepositLineDetail", {})
        if det.get("AccountRef", {}).get("value") != ama:
            continue
        key = (r["TxnDate"], d2(r["TotalAmt"]), "in")
        if key not in want:
            continue
        target, flagged, memo = want[key]
        log(f"Deposit {r['Id']} {key[0]} ${key[1]} : Ask My Accountant -> {target}")
        det["AccountRef"] = {"value": aid(target)}
        body = {"Id": r["Id"], "SyncToken": r["SyncToken"], "sparse": True,
                "DepositToAccountRef": r["DepositToAccountRef"], "Line": r["Line"],
                "PrivateNote": r.get("PrivateNote", "")}
        if COMMIT:
            try:
                c.post("deposit", body)
            except QboError as ex:
                print("   ERROR", str(ex)[:160])
        stats[f"-> {target}"] += 1

    print(f"\n{'='*64}\nCORRECTIONS: {dict(stats)} (total {sum(stats.values())})")
    if not COMMIT:
        print("PREVIEW only. Re-run with --commit to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
