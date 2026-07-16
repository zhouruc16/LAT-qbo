"""Fourth batch — clears the final 16 (2025) items from Ask My Accountant (prod).

Owner clarifications (2026-07):
  - 05-06 $251,000 loan from shareholder / 05-19 $250,000 repaid  -> Shareholder Loan
  - 05-07 $250,000 Unitree robot wire that BOUNCED / 05-16 $250,000 return
        -> Vendor Deposits (nets to $0)
  - deposits 10-17..12-24  -> Sales (customer Shiplot LLC)
  - 12-23 $2,500 company event (cash) -> Meals
  - transfers to acct …6733 -> COGS (Newton Robotics Inc)
  - transfers to acct …5527 + 12-30 $423,001 -> COGS (OpenLive Technology)

Re-points in place; bank balances unchanged. Default = PREVIEW; --commit to write.
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

from tiktok_qbo.qbo.env import load_creds
from tiktok_qbo.qbo.client import QboClient, QboError

COMMIT = "--commit" in sys.argv

# each: (date, abs-amount, dir, account, entity-name, entity-type)
C = [
    ("2025-05-06", "251000.00", "in",  "Shareholder Loan",        None, None),
    ("2025-05-16", "250000.00", "in",  "Vendor Deposits",         None, None),
    ("2025-10-17", "50000.00",  "in",  "Sales of Product Income", "Shiplot LLC", "Customer"),
    ("2025-11-05", "478280.00", "in",  "Sales of Product Income", "Shiplot LLC", "Customer"),
    ("2025-12-16", "150000.00", "in",  "Sales of Product Income", "Shiplot LLC", "Customer"),
    ("2025-12-23", "472060.00", "in",  "Sales of Product Income", "Shiplot LLC", "Customer"),
    ("2025-12-24", "300000.00", "in",  "Sales of Product Income", "Shiplot LLC", "Customer"),
    ("2025-05-07", "250000.00", "out", "Vendor Deposits",         "Unitree", "Vendor"),
    ("2025-05-19", "250000.00", "out", "Shareholder Loan",        None, None),
    ("2025-12-23", "2500.00",   "out", "Meals",                   None, None),
    ("2025-12-26", "398750.00", "out", "Cost of Goods Sold",      "Newton Robotics Inc", "Vendor"),
    ("2025-12-29", "436322.00", "out", "Cost of Goods Sold",      "Newton Robotics Inc", "Vendor"),
    ("2025-12-29", "415208.00", "out", "Cost of Goods Sold",      "Newton Robotics Inc", "Vendor"),
    ("2025-12-26", "53500.00",  "out", "Cost of Goods Sold",      "OpenLive Technology", "Vendor"),
    ("2025-12-29", "573001.00", "out", "Cost of Goods Sold",      "OpenLive Technology", "Vendor"),
    ("2025-12-30", "423001.00", "out", "Cost of Goods Sold",      "OpenLive Technology", "Vendor"),
]


def d2(x): return f"{Decimal(str(x)):.2f}"
def log(m): print(("[WRITE] " if COMMIT else "[preview] ") + m)


class P:
    def __init__(self):
        self.c = QboClient(load_creds(Path(".env.robotx.prod")))
        self.errors = []
        self._reload()

    def _reload(self):
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

    def entity_id(self, name, etype):
        ex = self.find(etype, "DisplayName", name)
        if ex:
            return ex["Id"]
        r = self.post(etype.lower(), {"DisplayName": name}, f"{etype} {name}")
        return r[etype]["Id"] if r and COMMIT else None


def main():
    p = P()
    print(f"\n{'#'*60}\n{'COMMIT - WRITING TO REAL BOOKS' if COMMIT else 'PREVIEW (no writes)'}\n{'#'*60}\n")
    if "Shareholder Loan" not in p.names:
        log("create Other Current Liability account: Shareholder Loan")
        r = p.post("account", {"Name": "Shareholder Loan", "AccountType": "Other Current Liability"}, "acct SHL")
        if r and COMMIT:
            p.names["Shareholder Loan"] = r["Account"]["Id"]
    if COMMIT:
        p._reload()

    AMA = p.names["Ask My Accountant"]
    purchases = p.all("Purchase")
    deposits = p.all("Deposit")
    done = 0
    for (ds, amt, direction, acct, ename, etype) in C:
        target = p.names.get(acct)
        if not target:
            print(f"  [skip] account missing: {acct}"); continue
        if direction == "out":
            m = next((r for r in purchases if r["TxnDate"] == ds and d2(r["TotalAmt"]) == amt
                      and r["Line"][0].get("AccountBasedExpenseLineDetail", {}).get("AccountRef", {}).get("value") == AMA), None)
            if not m:
                print(f"  [done/na] {ds} {amt:>12} OUT -> already cleared"); continue
            log(f"re-point {ds} {amt:>12} OUT  -> {acct}" + (f" ({ename})" if ename else ""))
            full = dict(m)
            full["Line"][0]["AccountBasedExpenseLineDetail"]["AccountRef"] = {"value": target}
            if ename:
                vid = p.entity_id(ename, etype)
                if vid and COMMIT:
                    full["EntityRef"] = {"value": vid, "type": etype}
            p.post("purchase", full, f"repoint {ds} {amt}"); done += 1
        else:
            hit = None
            for r in deposits:
                for ln in r.get("Line", []):
                    if r["TxnDate"] == ds and d2(ln["Amount"]) == amt and ln.get("DepositLineDetail", {}).get("AccountRef", {}).get("value") == AMA:
                        hit = r; break
                if hit:
                    break
            if not hit:
                print(f"  [done/na] {ds} {amt:>12} IN  -> already cleared"); continue
            log(f"re-point {ds} {amt:>12} IN   -> {acct}" + (f" ({ename})" if ename else ""))
            full = dict(hit)
            for l in full["Line"]:
                ld = l.get("DepositLineDetail", {})
                if ld.get("AccountRef", {}).get("value") == AMA and d2(l["Amount"]) == amt:
                    ld["AccountRef"] = {"value": target}
                    if ename:
                        cid = p.entity_id(ename, etype)
                        if cid and COMMIT:
                            ld["Entity"] = {"value": cid, "type": etype}
            p.post("deposit", full, f"repoint {ds} {amt}"); done += 1

    print(f"\n{'='*60}\n{'re-pointed' if COMMIT else 'would re-point'}: {done}")
    for e in p.errors:
        print("  ERROR", e)
    if COMMIT:
        bal = p.c.query("SELECT CurrentBalance FROM Account WHERE Name='Ask My Accountant'")["QueryResponse"]["Account"][0]
        print(f"Ask My Accountant balance now: ${Decimal(str(bal['CurrentBalance'])):,.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
