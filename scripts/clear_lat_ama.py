"""Clear owner-explained groups out of 'Ask My Accountant' (LAT production).

Owner's answers (2026-07-20), round 1:
  - TikTok Inc / TikTok Shop payouts ......... Sales of Product Income
  - all perfume-supplier transfers/Zelles .... Cost of goods sold ("payout of
    inventory"; includes the Miami Trading Zone reversal, which nets out)
  - BorderX: "borderx and borderx group borrow money from lat"
      transfers to CHK 4665 (borderx media) .. Due from BorderX Media LLC
      transfers to CHK 9445 (borderx group) .. Due from BorderX Group LLC
      Shopify income under BorderX Group ..... Due from BorderX Group LLC
      Zelle from BORDERX GROUP ............... Due from BorderX Group LLC
        (their income landing in LAT's bank reduces what they owe)
  - unambiguous billers (no owner input needed):
      SoCal Edison -> Electricity, T-Mobile -> Phone service,
      CA Franchise Tax Board -> Taxes paid

Still pending (owner): American Express groups, Qiandai Zhao Zelles,
SBA EIDL / vehicle / Audi loans, wire out, misc Zelles, checks, Temu/Whatnot.

Matches entities by the "[group]" prefix embedded in each line Description
by post_lat_2026.py. Only touches lines still pointing at Ask My Accountant.
Default = PREVIEW; pass --commit to write.
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

from tiktok_qbo.qbo.env import load_creds
from tiktok_qbo.qbo.client import QboClient, QboError

COMMIT = "--commit" in sys.argv
TAG = "LAT26"

DUE_MEDIA = "Due from BorderX Media LLC"
DUE_GROUP = "Due from BorderX Group LLC"

NEW_ACCOUNTS = [(DUE_MEDIA, "Other Current Asset"), (DUE_GROUP, "Other Current Asset")]

GROUP_TO_ACCT = {
    "TikTok Inc payout (LELNU)": "Sales of Product Income",
    "TikTok Shop payout (LELNU)": "Sales of Product Income",
    "Transfer to PERFUME CENTER OF AM": "Cost of goods sold",
    "Transfer to Miami Trading Zone": "Cost of goods sold",
    "Transfer to Miami Trading Zone (reversal)": "Cost of goods sold",
    "Transfer to E.T Perfumes Inc": "Cost of goods sold",
    "Transfer to MB Fragrances LLC": "Cost of goods sold",
    "Transfer to Perfume Plus Distrib": "Cost of goods sold",
    "Transfer to HAZ International In": "Cost of goods sold",
    "Zelle to Perfume Source Inc": "Cost of goods sold",
    "Zelle to perfume source": "Cost of goods sold",
    "Zelle to THEE PERFUME PLUS INC": "Cost of goods sold",
    "Transfer to CHK 4665 (BorderX Media LLC)": DUE_MEDIA,
    "Transfer to CHK 9445": DUE_GROUP,
    "Shopify (BorderX Group LLC)": DUE_GROUP,
    "Zelle from BORDERX GROUP LLC": DUE_GROUP,
    "SoCal Edison (electricity)": "Electricity",
    "T-Mobile (phone)": "Phone service",
    "CA Franchise Tax Board": "Taxes paid",
}

_GROUP_RE = re.compile(r"^\[(.*?)\] ")


def log(m: str) -> None:
    print(("[WRITE] " if COMMIT else "[preview] ") + m)


class P:
    def __init__(self):
        self.c = QboClient(load_creds(Path(".env.lat.prod")))
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
        print("ERROR: Ask My Accountant not found", file=sys.stderr)
        return 1
    for name, atype in NEW_ACCOUNTS:
        p.ensure_account(name, atype)
    missing = [a for a in set(GROUP_TO_ACCT.values()) if a not in p.names
               and a not in dict(NEW_ACCOUNTS)]
    if missing:
        print(f"ERROR: target accounts missing from CoA: {missing}", file=sys.stderr)
        return 1

    moved = Counter()
    moved_amt: dict[str, Decimal] = Counter()
    n_left = 0

    def handle(entity, kind):
        nonlocal n_left
        note = entity.get("PrivateNote", "") or ""
        if f"{TAG}:" not in note:
            return
        line = entity["Line"][0]
        detail_key = "DepositLineDetail" if kind == "deposit" else "AccountBasedExpenseLineDetail"
        cur = line.get(detail_key, {}).get("AccountRef", {}).get("value")
        if cur != ama:
            return
        m = _GROUP_RE.match(line.get("Description") or "")
        g = m.group(1) if m else None
        acct = GROUP_TO_ACCT.get(g)
        if acct is None:
            n_left += 1
            return
        aid = p.names.get(acct)
        amt = Decimal(str(entity["TotalAmt"]))
        if not aid:
            log(f"skip {entity['TxnDate']} {amt} -> {acct} (account pending creation)")
            return
        full = dict(entity)
        full["Line"][0][detail_key]["AccountRef"] = {"value": aid}
        p.post(kind, full, f"repoint {kind} {entity['TxnDate']} {amt}")
        moved[f"{g} -> {acct}"] += 1
        moved_amt[f"{g} -> {acct}"] += amt

    for e in p.all("Purchase"):
        handle(e, "purchase")
    for e in p.all("Deposit"):
        handle(e, "deposit")

    print(f"\n{'Moved' if COMMIT else 'Would move'}:")
    for k in sorted(moved, key=lambda k: -abs(moved_amt[k])):
        print(f"  {moved[k]:4d}x {moved_amt[k]:>13} {k}")
    print(f"\nTotal {sum(moved.values())} moved; {n_left} left in AMA pending owner.")
    if p.errors:
        print(f"\n{len(p.errors)} ERRORS:")
        for e in p.errors:
            print("  ", e)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
