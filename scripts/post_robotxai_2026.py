"""Post RobotX AI Inc. (Robotxai) Chase ...3205 activity, Oct 2025 - Jun 2026,
into the Robotxai production QBO company.

The account opened at $0.00 in Oct 2025, so there is no opening-balance JE:
posting every statement transaction rebuilds the register to $190,406.26
(the Jun 2026 ending balance).

Classification: only bank fees are deterministic (-> Bank Service Charges).
Everything else — incoming wires from Brightedge/Renovix/individuals, branch
deposits, transfers to external accts ...6591/...3782, the payment to Chase
card ...7856 (RobotX's credit card — inter-company?), Zelle, Corp E Corp ACH,
and the five June checks — is parked in "Ask My Accountant" with a descriptive
memo, pending owner clarification. The books balance and the register ties;
the owner then clears AMA item by item (same flow as the RobotX cleanup).

Idempotent: every entity is tagged in PrivateNote with "RXAI:<txn_id>";
anything already tagged is skipped, so re-running only posts the shortfall.

Default = PREVIEW (writes NOTHING). Pass --commit to write.
Credentials: .env.robotxai.prod (production only — this company has no sandbox).
"""
from __future__ import annotations

import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from tiktok_qbo.qbo.env import load_creds
from tiktok_qbo.qbo.client import QboClient, QboError
from robotx_qbo.ingest.chase_pdf import parse_chase
from robotx_qbo.models import Txn

COMMIT = "--commit" in sys.argv
ENVFILE = ".env.robotxai.prod"
STMT_DIR = Path("inputs/robotxai")
TAG = "RXAI"

BANK_NAME = "Chase Checking - 3205"
AMA = "Ask My Accountant"
FEES = "Bank Service Charges"
EXPECTED_END = Decimal("190406.26")   # Jun 2026 statement ending balance


def log(m: str) -> None:
    print(("[WRITE] " if COMMIT else "[preview] ") + m)


# ── classification ────────────────────────────────────────────────────────────
def target_account(t: Txn) -> tuple[str, str]:
    """-> (account_name, memo). Fees are deterministic; the rest is parked in
    Ask My Accountant with a memo naming the counterparty for the owner."""
    d = t.description
    dl = d.lower()
    if t.kind == "fee":
        return FEES, d
    if t.kind == "check":
        return AMA, f"{d} — payee unknown, PENDING owner"
    if "chase card ending in 7856" in dl:
        return AMA, "Payment to Chase CC ...7856 (RobotX Inc's card?) — PENDING owner"
    if "zelle" in dl:
        return AMA, f"{d[:70]} — PENDING owner"
    if "corp e corp" in dl:
        return AMA, "Corp E Corp ACH debit — PENDING owner"
    if "online transfer to chk" in dl or "online transfer from chk" in dl:
        return AMA, f"{d[:70]} — external acct, PENDING owner"
    if t.amount > 0:
        return AMA, f"Incoming: {d[:80]} — PENDING owner"
    return AMA, f"{d[:80]} — PENDING owner"


# ── poster ────────────────────────────────────────────────────────────────────
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

    def ensure_account(self, name: str, atype: str) -> str | None:
        if name in self.names:
            return self.names[name]
        log(f"create account {name} ({atype})")
        r = self.post("account", {"Name": name, "AccountType": atype}, f"acct {name}")
        if r and COMMIT:
            aid = r["Account"]["Id"]
            self.names[name] = aid
            return aid
        return None

    def existing_tags(self) -> set[str]:
        tags: set[str] = set()
        for e in ("Deposit", "Purchase"):
            for row in self.all(e):
                note = row.get("PrivateNote", "")
                if f"{TAG}:" in note:
                    tags.add(note.split(f"{TAG}:", 1)[1].split()[0].strip("|").strip())
        return tags


def main() -> int:
    txns: list[Txn] = []
    for f in sorted(STMT_DIR.glob("*.pdf")):
        txns.extend(parse_chase(f))
    txns.sort(key=lambda t: (t.date, t.description))
    net = sum(t.amount for t in txns)
    print(f"Parsed {len(txns)} txns from {len(list(STMT_DIR.glob('*.pdf')))} statements; "
          f"net={net} (expected ending balance {EXPECTED_END})")
    if net != EXPECTED_END:
        print("FATAL: parsed net does not equal the Jun 2026 ending balance — aborting.",
              file=sys.stderr)
        return 1

    p = P()
    print(f"\nConnected. Company has {len(p.names)} accounts.")

    bank = p.ensure_account(BANK_NAME, "Bank")
    ama = p.ensure_account(AMA, "Expense")
    fees = p.ensure_account(FEES, "Expense")

    done = p.existing_tags() if COMMIT else set()
    if done:
        print(f"{len(done)} txns already posted (tag {TAG}) — will skip.")

    by_acct: dict[str, Decimal] = defaultdict(Decimal)
    n_posted = n_skipped = 0
    for t in txns:
        acct_name, memo = target_account(t)
        by_acct[acct_name] += t.amount
        if t.txn_id in done:
            n_skipped += 1
            continue
        aid = {AMA: ama, FEES: fees}[acct_name]
        note = f"{TAG}:{t.txn_id} | {t.description[:80]}"
        ds = f"{t.date:%Y-%m-%d}"
        amt = float(abs(t.amount))
        if t.amount > 0:
            log(f"deposit  {ds} {abs(t.amount):>12} -> {acct_name}  ({memo[:60]})")
            body = {"DepositToAccountRef": {"value": bank}, "TxnDate": ds,
                    "PrivateNote": note,
                    "Line": [{"Amount": amt, "DetailType": "DepositLineDetail",
                              "DepositLineDetail": {"AccountRef": {"value": aid}},
                              "Description": memo[:200]}]}
            p.post("deposit", body, f"deposit {ds} {amt}")
        else:
            kindlbl = "check   " if t.kind == "check" else "purchase"
            log(f"{kindlbl} {ds} {abs(t.amount):>12} -> {acct_name}  ({memo[:60]})")
            body = {"AccountRef": {"value": bank}, "PaymentType": "Check",
                    "TxnDate": ds, "PrivateNote": note,
                    "Line": [{"Amount": amt,
                              "DetailType": "AccountBasedExpenseLineDetail",
                              "AccountBasedExpenseLineDetail": {"AccountRef": {"value": aid}},
                              "Description": memo[:200]}]}
            if t.check_no:
                body["DocNumber"] = t.check_no
            p.post("purchase", body, f"purchase {ds} {amt}")
        n_posted += 1

    print(f"\n{'WROTE' if COMMIT else 'Would write'} {n_posted} entities "
          f"({n_skipped} already present).")
    print("\nNet by target account:")
    for name, total in sorted(by_acct.items()):
        print(f"  {name:25s} {total:>14}")
    print(f"  {'-> ' + BANK_NAME:25s} {sum(by_acct.values()):>14}  (register balance)")
    if p.errors:
        print(f"\n{len(p.errors)} ERRORS:")
        for e in p.errors:
            print("  ", e)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
