"""Fix JE RX-CC-EXTPAY-0102 in RobotX Inc's production books.

The 2026-01-02 $5,281.69 payment of Chase CC ...7856 was guessed to be the
owner paying personally (credit Owner's Withdrawal, flagged CONFIRM). Owner
confirmed 2026-07-20 it was actually paid by RobotX AI Inc. from its Chase
...3205 account (already booked there as "Due from RobotX Inc.").

This re-points the JE's credit line: Owner's Withdrawal -> Due to RobotX AI
Inc. (Other Current Liability), and updates the note. Amounts unchanged.

Default = PREVIEW; pass --commit to write.
"""
from __future__ import annotations

import sys
from pathlib import Path

from tiktok_qbo.qbo.env import load_creds
from tiktok_qbo.qbo.client import QboClient

COMMIT = "--commit" in sys.argv
DOC = "RX-CC-EXTPAY-0102"
LIAB = "Due to RobotX AI Inc."
NOTE = ("Card paid 01/02 by RobotX AI Inc. (Chase ...3205) — owner confirmed "
        "2026-07-20; intercompany, mirrors 'Due from RobotX Inc.' in RobotX AI books")


def log(m: str) -> None:
    print(("[WRITE] " if COMMIT else "[preview] ") + m)


def main() -> int:
    c = QboClient(load_creds(Path(".env.robotx.prod")))
    names = {a["Name"]: a["Id"] for a in
             c.query("SELECT * FROM Account MAXRESULTS 1000")
              .get("QueryResponse", {}).get("Account", [])}

    je = c.query(f"SELECT * FROM JournalEntry WHERE DocNumber = '{DOC}'") \
          .get("QueryResponse", {}).get("JournalEntry", [])
    if not je:
        print(f"ERROR: JE {DOC} not found", file=sys.stderr)
        return 1
    je = je[0]

    credit = next(ln for ln in je["Line"]
                  if ln["JournalEntryLineDetail"]["PostingType"] == "Credit")
    cur_name = next((n for n, i in names.items()
                     if i == credit["JournalEntryLineDetail"]["AccountRef"]["value"]), "?")
    if cur_name == LIAB:
        print(f"Already fixed: credit line points at {LIAB}. Nothing to do.")
        return 0
    if cur_name != "Owner's Withdrawal":
        print(f"ERROR: credit line points at '{cur_name}', expected Owner's Withdrawal "
              f"— aborting, inspect manually.", file=sys.stderr)
        return 1

    liab_id = names.get(LIAB)
    if not liab_id:
        log(f"create account {LIAB} (Other Current Liability)")
        if COMMIT:
            r = c.post("account", {"Name": LIAB, "AccountType": "Other Current Liability"})
            liab_id = r["Account"]["Id"]

    log(f"JE {DOC} {je['TxnDate']}: credit {credit['Amount']} "
        f"{cur_name} -> {LIAB}; update note")
    if COMMIT:
        credit["JournalEntryLineDetail"]["AccountRef"] = {"value": liab_id}
        credit["Description"] = "Paid by RobotX AI Inc. (intercompany)"
        je["PrivateNote"] = NOTE
        c.post("journalentry", je)
        print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
