"""Reclassify RobotX Inc's 2025-11-10 $680,000 deposit (bank ref
"Deposit 2093321044", BOA-0108) from Common Stock to Sales of Product Income,
payer US Homecoin Group.

Context: the entry predates the pipeline (original bookkeeper booked it as
Common Stock). Owner instructed (relayed by user 2026-07-31) that it was in
fact a robot-sale payment from US Homecoin Group. Consequences were flagged
and acknowledged: 2025 income +$680k (tax), Common Stock -> $0.

Default = PREVIEW; pass --commit to write.
"""
from __future__ import annotations

import sys
from pathlib import Path

from tiktok_qbo.qbo.env import load_creds
from tiktok_qbo.qbo.client import QboClient

COMMIT = "--commit" in sys.argv
NOTE = ("Deposit 2093321044 | was Common Stock; reclassified to Sales of Product "
        "Income (US Homecoin Group) per owner's instruction relayed 2026-07-31")


def main() -> int:
    c = QboClient(load_creds(Path(".env.robotx.prod")))
    accts = {a["Id"]: a["Name"] for a in
             c.query("SELECT * FROM Account MAXRESULTS 1000")["QueryResponse"]["Account"]}
    ids = {n: i for i, n in accts.items()}

    deps = c.query("SELECT * FROM Deposit WHERE TxnDate = '2025-11-10'") \
        ["QueryResponse"].get("Deposit", [])
    dep = next((d for d in deps if float(d["TotalAmt"]) == 680000.0), None)
    if dep is None:
        print("ERROR: 2025-11-10 $680,000 deposit not found", file=sys.stderr)
        return 1
    ln = dep["Line"][0]
    cur = accts[ln["DepositLineDetail"]["AccountRef"]["value"]]
    print(f"Deposit Id {dep['Id']} | line account currently: {cur}")
    if cur == "Sales of Product Income":
        print("Already reclassified — nothing to do.")
        return 0
    if cur != "Common Stock":
        print(f"ERROR: expected Common Stock, found '{cur}' — aborting.", file=sys.stderr)
        return 1

    cust = c.query("SELECT * FROM Customer WHERE DisplayName = 'US Homecoin Group'") \
        ["QueryResponse"].get("Customer", [])
    cid = cust[0]["Id"] if cust else None
    print("US Homecoin Group customer Id:", cid or "(none — line posts without entity)")

    mode = "[WRITE]" if COMMIT else "[preview]"
    print(f"{mode} re-point $680,000 deposit line: Common Stock -> Sales of Product Income")
    if not COMMIT:
        return 0

    ln["DepositLineDetail"]["AccountRef"] = {"value": ids["Sales of Product Income"]}
    if cid:
        ln["DepositLineDetail"]["Entity"] = {"value": cid, "type": "CUSTOMER"}
    ln["Description"] = ("Robot sale — US Homecoin Group (reclass from Common Stock "
                         "per owner instruction 2026-07-31)")
    dep["PrivateNote"] = NOTE
    c.post("deposit", dep)

    accts2 = c.query("SELECT * FROM Account MAXRESULTS 1000")["QueryResponse"]["Account"]
    cs = next(a.get("CurrentBalance") for a in accts2 if a["Name"] == "Common Stock")
    print("Done. Common Stock balance now:", cs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
