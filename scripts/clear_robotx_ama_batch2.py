"""Clear a second batch of now-explained items out of 'Ask My Accountant' (prod).

Owner clarifications (2026-07): robot purchases (Scale Robotics, Unitree),
office rent (Linkhome Realty), and robot shipping. Each is re-pointed from
Ask My Accountant to its correct account; bank balances unchanged. Default =
PREVIEW; pass --commit. Idempotent.
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

from tiktok_qbo.qbo.env import load_creds
from tiktok_qbo.qbo.client import QboClient, QboError

COMMIT = "--commit" in sys.argv

# (date, abs-amount, target account, vendor name or None)
CORRECTIONS = [
    ("2026-06-26", "379109.00", "Cost of Goods Sold", "Scale Robotics Inc"),
    ("2026-06-02", "219194.00", "Cost of Goods Sold", "Unitree"),
    ("2026-06-25", "24000.00",  "Rent",               "Linkhome Realty Group"),
    ("2026-06-25", "1000.00",   "Rent",               "Linkhome Realty Group"),
    ("2026-06-26", "33801.34",  "Freight & Shipping", None),
]


def d2(x): return f"{Decimal(str(x)):.2f}"
def log(m): print(("[WRITE] " if COMMIT else "[preview] ") + m)


class P:
    def __init__(self):
        self.c = QboClient(load_creds(Path(".env.robotx.prod")))
        self.errors = []
        self.names = {a["Name"]: a["Id"] for a in self.all("Account")}

    def all(self, e):
        out, s = [], 1
        while True:
            pg = self.c.query(f"SELECT * FROM {e} STARTPOSITION {s} MAXRESULTS 100").get("QueryResponse", {}).get(e, [])
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

    def vendor(self, name):
        ex = self.find("Vendor", "DisplayName", name)
        if ex:
            return ex["Id"]
        r = self.post("vendor", {"DisplayName": name}, f"vendor {name}")
        return r["Vendor"]["Id"] if r and COMMIT else None


def main():
    p = P()
    print(f"\n{'#'*60}\n{'COMMIT - WRITING TO REAL BOOKS' if COMMIT else 'PREVIEW (no writes)'}\n{'#'*60}\n")
    AMA = p.names["Ask My Accountant"]
    purchases = p.all("Purchase")
    done = 0
    for (ds, amt, acct, vend) in CORRECTIONS:
        target = p.names.get(acct)
        match = next((r for r in purchases
                      if r["TxnDate"] == ds and d2(r["TotalAmt"]) == amt
                      and r["Line"][0].get("AccountBasedExpenseLineDetail", {})
                             .get("AccountRef", {}).get("value") == AMA), None)
        if not match:
            print(f"  [done/na] {ds} {amt:>12} -> already cleared or not in AMA"); continue
        log(f"re-point {ds} {amt:>12}  Ask My Accountant -> {acct}" + (f"  ({vend})" if vend else ""))
        full = dict(match)
        full["Line"][0]["AccountBasedExpenseLineDetail"]["AccountRef"] = {"value": target}
        if vend:
            vid = p.vendor(vend)
            if vid and COMMIT:
                full["EntityRef"] = {"value": vid, "type": "Vendor"}
        p.post("purchase", full, f"repoint {ds} {amt}")
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
