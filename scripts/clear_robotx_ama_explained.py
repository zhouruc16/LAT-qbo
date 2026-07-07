"""Clear the 9 already-explained items out of 'Ask My Accountant' (production).

Each is re-pointed from Ask My Accountant to its correct account (per the boss's
earlier explanations). Bank balances are unchanged — only the categorisation
moves. Default = PREVIEW; pass --commit to write. Idempotent: skips any item
already re-pointed.
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

from tiktok_qbo.qbo.env import load_creds
from tiktok_qbo.qbo.client import QboClient, QboError

COMMIT = "--commit" in sys.argv

# (date, abs-amount, direction, target account, entity name, entity type)
CORRECTIONS = [
    ("2026-03-23", "95000.00",  "in",  "Sales of Product Income", "US Homecoin Group", "Customer"),
    ("2026-03-25", "497550.00", "in",  "Sales of Product Income", "US Homecoin Group", "Customer"),
    ("2026-03-26", "2500.00",   "out", "Outside Services",         "Sam", "Vendor"),
    ("2026-03-30", "497000.00", "out", "Cost of Goods Sold",       "New American Title Company", "Vendor"),
    ("2026-03-31", "15000.00",  "out", "Rent",                     None, None),
    ("2026-04-03", "5000.00",   "out", "Outside Services",         "Sam", "Vendor"),
    ("2026-04-13", "9000.00",   "out", "Outside Services",         "Sam", "Vendor"),
    ("2026-04-17", "109.52",    "out", "Travel",                   "Sam", "Vendor"),
    ("2026-04-20", "269.36",    "out", "Travel",                   "Sam", "Vendor"),
]


def d2(x) -> str:
    return f"{Decimal(str(x)):.2f}"


def log(m: str) -> None:
    print(("[WRITE] " if COMMIT else "[preview] ") + m)


class P:
    def __init__(self):
        self.c = QboClient(load_creds(Path(".env.robotx.prod")))
        self.errors: list[str] = []
        self.names = {a["Name"]: a["Id"] for a in self.all("Account")}

    def all(self, e):
        out, s = [], 1
        while True:
            pg = self.c.query(f"SELECT * FROM {e} STARTPOSITION {s} MAXRESULTS 100") \
                     .get("QueryResponse", {}).get(e, [])
            out += pg
            if len(pg) < 100:
                return out
            s += 100

    def find(self, e, f, v):
        s = str(v).replace("'", "\\'")
        r = self.c.query(f"SELECT * FROM {e} WHERE {f} = '{s}'").get("QueryResponse", {}).get(e, [])
        return r[0] if r else None

    def post(self, path, body, label):
        if not COMMIT:
            return {"_": "preview"}
        try:
            return self.c.post(path, body)
        except QboError as ex:
            self.errors.append(f"{label}: {str(ex)[:160]}")
            print("   ERROR", label, "->", str(ex)[:160])
            return None

    def entity_id(self, name, etype):
        ex = self.find(etype, "DisplayName", name)
        if ex:
            return ex["Id"]
        r = self.post(etype.lower(), {"DisplayName": name}, f"{etype} {name}")
        return r[etype]["Id"] if r and COMMIT else None


def main():
    p = P()
    print(f"\n{'#'*60}\n{'COMMIT - WRITING TO REAL BOOKS' if COMMIT else 'PREVIEW (no writes)'}\n{'#'*60}\n")
    AMA = p.names["Ask My Accountant"]
    purchases = p.all("Purchase")
    deposits = p.all("Deposit")
    done = 0

    for (ds, amt, direction, acct, ename, etype) in CORRECTIONS:
        target = p.names.get(acct)
        if not target:
            print(f"  [skip] account missing: {acct}"); continue

        if direction == "out":
            match = next((r for r in purchases
                          if r["TxnDate"] == ds and d2(r["TotalAmt"]) == amt
                          and r["Line"][0].get("AccountBasedExpenseLineDetail", {})
                                 .get("AccountRef", {}).get("value") == AMA), None)
            if not match:
                # already re-pointed (or not found in AMA)
                still = next((r for r in purchases if r["TxnDate"] == ds and d2(r["TotalAmt"]) == amt), None)
                print(f"  [done] {ds} {amt:>12} -> already cleared" if still else
                      f"  [MISS] {ds} {amt:>12} not found"); continue
            log(f"re-point {ds} {amt:>12} OUT  Ask My Accountant -> {acct}")
            full = dict(match)
            full["Line"][0]["AccountBasedExpenseLineDetail"]["AccountRef"] = {"value": target}
            if ename:
                vid = p.entity_id(ename, etype)
                if vid and COMMIT:
                    full["EntityRef"] = {"value": vid, "type": etype}
            p.post("purchase", full, f"repoint OUT {ds}")
            done += 1

        else:  # in (deposit)
            match = None
            for r in deposits:
                for ln in r.get("Line", []):
                    dd = ln.get("DepositLineDetail", {})
                    if r["TxnDate"] == ds and d2(ln["Amount"]) == amt and dd.get("AccountRef", {}).get("value") == AMA:
                        match = (r, ln); break
                if match:
                    break
            if not match:
                print(f"  [done] {ds} {amt:>12} -> already cleared / not in AMA"); continue
            r, ln = match
            log(f"re-point {ds} {amt:>12} IN   Ask My Accountant -> {acct}  ({ename})")
            full = dict(r)
            for l in full["Line"]:
                if l.get("DepositLineDetail", {}).get("AccountRef", {}).get("value") == AMA and d2(l["Amount"]) == amt:
                    l["DepositLineDetail"]["AccountRef"] = {"value": target}
                    if ename:
                        cid = p.entity_id(ename, etype)
                        if cid and COMMIT:
                            l["DepositLineDetail"]["Entity"] = {"value": cid, "type": etype}
            p.post("deposit", full, f"repoint IN {ds}")
            done += 1

    print(f"\n{'='*60}\n{'re-pointed' if COMMIT else 'would re-point'}: {done}")
    if p.errors:
        print(f"ERRORS ({len(p.errors)}):")
        for e in p.errors:
            print("  ", e)
    if COMMIT:
        bal = p.c.query("SELECT CurrentBalance FROM Account WHERE Name='Ask My Accountant'")["QueryResponse"]["Account"][0]
        print(f"Ask My Accountant balance now: ${Decimal(str(bal['CurrentBalance'])):,.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
