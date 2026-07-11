"""Third batch of owner-clarified Ask My Accountant items (production).

- 06/26 $410,900 deposit  -> Sales (US Homecoin robot sale)
- 05/22 $100 purchase     -> Interactive Brokers (Investment) asset  [opened a
                             brokerage account, so it's an asset, not an expense]

Re-points in place; bank balances unchanged. Default = PREVIEW; --commit to write.
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

from tiktok_qbo.qbo.env import load_creds
from tiktok_qbo.qbo.client import QboClient, QboError

COMMIT = "--commit" in sys.argv
INVEST_ACCT = "Interactive Brokers (Investment)"


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


def main():
    p = P()
    print(f"\n{'#'*60}\n{'COMMIT - WRITING TO REAL BOOKS' if COMMIT else 'PREVIEW (no writes)'}\n{'#'*60}\n")

    # create the investment asset account if missing
    if INVEST_ACCT not in p.names:
        log(f"create Other Current Asset account: {INVEST_ACCT}")
        r = p.post("account", {"Name": INVEST_ACCT, "AccountType": "Other Current Asset"}, "acct invest")
        if r and COMMIT:
            p.names[INVEST_ACCT] = r["Account"]["Id"]
    if COMMIT:
        p._reload()

    AMA = p.names["Ask My Accountant"]
    SALES = p.names["Sales of Product Income"]
    INVEST = p.names.get(INVEST_ACCT)
    homecoin = p.find("Customer", "DisplayName", "US Homecoin Group")
    hc_id = homecoin["Id"] if homecoin else None
    done = 0

    # 1) $410,900 deposit -> Sales (Homecoin)
    for dp in p.all("Deposit"):
        for ln in dp.get("Line", []):
            dd = ln.get("DepositLineDetail", {})
            if dp["TxnDate"] == "2026-06-26" and d2(ln["Amount"]) == "410900.00" and dd.get("AccountRef", {}).get("value") == AMA:
                log("re-point 2026-06-26   410900.00 IN   Ask My Accountant -> Sales of Product Income (US Homecoin Group)")
                full = dict(dp)
                for l in full["Line"]:
                    ld = l.get("DepositLineDetail", {})
                    if ld.get("AccountRef", {}).get("value") == AMA and d2(l["Amount"]) == "410900.00":
                        ld["AccountRef"] = {"value": SALES}
                        if hc_id and COMMIT:
                            ld["Entity"] = {"value": hc_id, "type": "Customer"}
                p.post("deposit", full, "repoint 410900")
                done += 1

    # 2) $100 purchase -> investment asset
    for pu in p.all("Purchase"):
        ln = pu["Line"][0].get("AccountBasedExpenseLineDetail", {})
        if pu["TxnDate"] == "2026-05-22" and d2(pu["TotalAmt"]) == "100.00" and ln.get("AccountRef", {}).get("value") == AMA:
            log(f"re-point 2026-05-22      100.00 OUT  Ask My Accountant -> {INVEST_ACCT}")
            full = dict(pu)
            full["Line"][0]["AccountBasedExpenseLineDetail"]["AccountRef"] = {"value": INVEST}
            p.post("purchase", full, "repoint 100")
            done += 1

    print(f"\n{'='*60}\n{'re-pointed' if COMMIT else 'would re-point'}: {done}")
    if p.errors:
        for e in p.errors:
            print("  ERROR", e)
    if COMMIT:
        bal = p.c.query("SELECT CurrentBalance FROM Account WHERE Name='Ask My Accountant'")["QueryResponse"]["Account"][0]
        print(f"Ask My Accountant balance now: ${Decimal(str(bal['CurrentBalance'])):,.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
