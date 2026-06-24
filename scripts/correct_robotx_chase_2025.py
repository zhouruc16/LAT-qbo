"""Re-point the 7 Chase 2025 transactions that were entered into production
separately (deposits -> Common Stock, transfers/withdrawals -> Uncategorized
Expense / Owner's Withdrawal) so they match robotx_qbo.classify:

  US Homecoin deposits ($452k, $683k) -> Sales (revenue, not capital)
  …7880 ($5,500)                      -> Rent
  …5527 transfers + withdrawals       -> Ask My Accountant (still unidentified)

Only the specific Chase 2025 transactions present in our statement data are
touched (matched by bank+date+amount+direction). The $680k Singularity deposit
and the $632,500 Nov withdrawal are NOT touched — we have no statement for them
and the payer relationship is unconfirmed.

Method: update only the line's AccountRef on the existing Deposit / Purchase
(lowest-risk; no delete/recreate). Idempotent: entries already on the target
account are skipped.

Default = PREVIEW (writes NOTHING). Pass --commit to write.
--prod uses .env.robotx.prod (the live company); default .env.robotx (sandbox).
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
ACCT_ALIAS = ({"Sales - Robots": "Sales of Product Income",
               "Cost of Goods - Robots": "Cost of Goods Sold"} if PROD else
              {"Professional Fees": "Legal & Professional Fees"})
CHASE_BANK = "BOA-0108" if PROD else "Chase Checking - 0108"


def d2(x) -> str:
    return f"{Decimal(str(x)):.2f}"


def log(m: str) -> None:
    print(("[WRITE] " if COMMIT else "[preview] ") + m)


def all_(c, e):
    out, pos = [], 1
    while True:
        pg = c.query(f"SELECT * FROM {e} STARTPOSITION {pos} MAXRESULTS 1000") \
            .get("QueryResponse", {}).get(e, [])
        out += pg
        if len(pg) < 1000:
            return out
        pos += 1000


def main():
    c = QboClient(load_creds(Path(ENVFILE)))
    print(f"\n{'#'*64}\n{'COMMIT — ' + ENVFILE if COMMIT else 'PREVIEW (' + ENVFILE + ')'}\n{'#'*64}\n")
    names = {a["Name"]: a["Id"] for a in all_(c, "Account")}
    aid = lambda n: names.get(ACCT_ALIAS.get(n, n))
    chase = names[CHASE_BANK]

    # Target account for each Chase 2025 transaction, from the classifier.
    want: dict[tuple[str, str, str], str] = {}
    for cl in classify(load_2025()):
        if cl.txn.account != "chase":
            continue
        t = cl.txn
        want[(f"{t.date:%Y-%m-%d}", d2(abs(t.amount)), "in" if t.amount > 0 else "out")] = cl.account_name

    stats = Counter()

    for r in all_(c, "Deposit"):
        if not r["TxnDate"].startswith("2025"):
            continue
        key = (r["TxnDate"], d2(r["TotalAmt"]), "in")
        if key not in want:
            continue
        det = r.get("Line", [{}])[0].get("DepositLineDetail", {})
        target_id = aid(want[key])
        if det.get("AccountRef", {}).get("value") == target_id:
            stats["already"] += 1; continue
        cur = det.get("AccountRef", {}).get("name")
        log(f"Deposit {r['Id']} {key[0]} ${key[1]} : {cur} -> {want[key]}")
        det["AccountRef"] = {"value": target_id}
        body = {"Id": r["Id"], "SyncToken": r["SyncToken"], "sparse": True,
                "DepositToAccountRef": r["DepositToAccountRef"], "Line": r["Line"],
                "PrivateNote": (r.get("PrivateNote", "") + " | reclass->" + want[key])[:1000]}
        if COMMIT:
            try:
                c.post("deposit", body)
            except QboError as ex:
                print("   ERROR", str(ex)[:160])
        stats[f"-> {want[key]}"] += 1

    for r in all_(c, "Purchase"):
        if not r["TxnDate"].startswith("2025"):
            continue
        if r.get("AccountRef", {}).get("value") != chase:
            continue
        key = (r["TxnDate"], d2(r["TotalAmt"]), "out")
        if key not in want:
            continue
        det = r.get("Line", [{}])[0].get("AccountBasedExpenseLineDetail", {})
        target_id = aid(want[key])
        if det.get("AccountRef", {}).get("value") == target_id:
            stats["already"] += 1; continue
        cur = det.get("AccountRef", {}).get("name")
        log(f"Purchase {r['Id']} {key[0]} ${key[1]} : {cur} -> {want[key]}")
        det["AccountRef"] = {"value": target_id}
        body = {"Id": r["Id"], "SyncToken": r["SyncToken"], "sparse": True,
                "PaymentType": r.get("PaymentType", "Check"), "AccountRef": r["AccountRef"],
                "Line": r["Line"],
                "PrivateNote": (r.get("PrivateNote", "") + " | reclass->" + want[key])[:1000]}
        if COMMIT:
            try:
                c.post("purchase", body)
            except QboError as ex:
                print("   ERROR", str(ex)[:160])
        stats[f"-> {want[key]}"] += 1

    print(f"\n{'='*64}\nRECLASSIFIED: {dict(stats)}")
    if not COMMIT:
        print("PREVIEW only. Re-run with --commit to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
