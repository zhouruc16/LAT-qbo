"""Post two RobotX Inc sales invoices (robot sales) as Accounts Receivable.

Source PDFs (user, 2026-08-03):
  INV-2026-0429  Qlight AI Inc        goods $848,364.46 + 9.75% tax $82,715.54 = $931,080.00
  INV-2026-0505  US Homecoin Group    goods $806,400.91 + 9.75% tax $78,624.09 = $885,025.00

Follows the books' existing conventions: item "Robots" (-> Sales of Product
Income) for goods lines with qty/unit price, item "Sales Tax" (-> Sales Tax
Payable) for the tax line, TaxCode NON so AST doesn't recompute. No payment
is recorded — both stay open in A/R until paid.

Idempotent by DocNumber. Default = PREVIEW; pass --commit to write.
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

from tiktok_qbo.qbo.env import load_creds
from tiktok_qbo.qbo.client import QboClient

COMMIT = "--commit" in sys.argv

INVOICES = [
    {
        "doc": "INV-2026-0429", "date": "2026-04-29", "due": "2026-05-06",
        "customer": "Qlight AI Inc",
        "lines": [
            ("G1 Edu Plus (G1edu-U2)", 1, "53784.46"),
            ("A2-W Pro", 5, "46900.00"),
            ("A2 Pro", 6, "40900.00"),
            ("R1 Edu Smart (R1 EDU U2)", 8, "18900.00"),
            ("Go2-W Ultimate (Go2-W-U4)", 5, "20890.00"),
            ("Go2 Edu Plus (Go2 EDU-U2)", 4, "13205.00"),
            ("Servo Robotic Arm D1", 2, "2160.00"),
            ("Go2-W Self-charging Board (incl. fast charger)", 3, "480.00"),
            ("Go2-Remote Controller", 3, "150.00"),
        ],
        "tax": "82715.54", "total": "931080.00",
    },
    {
        "doc": "INV-2026-0505", "date": "2026-05-05", "due": "2026-05-12",
        "customer": "US Homecoin Group",
        "lines": [
            ("G1 Edu Plus (G1edu-U2)", 1, "53410.91"),
            ("A2-W Pro", 5, "46900.00"),
            ("A2 Pro", 5, "40900.00"),
            ("R1 Edu Smart (R1 EDU U2)", 8, "18900.00"),
            ("Go2-W Ultimate (Go2-W-U4)", 4, "20890.00"),
            ("Go2 Edu Plus (Go2 EDU-U2)", 6, "13205.00"),
        ],
        "tax": "78624.09", "total": "885025.00",
    },
]


def log(m: str) -> None:
    print(("[WRITE] " if COMMIT else "[preview] ") + m)


def main() -> int:
    c = QboClient(load_creds(Path(".env.robotx.prod")))
    items = {i["Name"]: i["Id"] for i in
             c.query("SELECT * FROM Item MAXRESULTS 1000")["QueryResponse"]["Item"]}
    for needed in ("Robots", "Sales Tax"):
        if needed not in items:
            print(f"ERROR: item '{needed}' missing", file=sys.stderr)
            return 1

    for inv in INVOICES:
        # integrity check
        goods = sum(Decimal(u) * q for _, q, u in inv["lines"])
        assert goods + Decimal(inv["tax"]) == Decimal(inv["total"]), inv["doc"]

        exist = c.query(f"SELECT * FROM Invoice WHERE DocNumber = '{inv['doc']}'") \
            .get("QueryResponse", {}).get("Invoice", [])
        if exist:
            print(f"{inv['doc']} already exists (Id {exist[0]['Id']}) — skipping.")
            continue

        cust = c.query(f"SELECT * FROM Customer WHERE DisplayName = '{inv['customer']}'") \
            .get("QueryResponse", {}).get("Customer", [])
        if cust:
            cid = cust[0]["Id"]
        else:
            log(f"create customer {inv['customer']}")
            cid = c.post("customer", {"DisplayName": inv["customer"]})["Customer"]["Id"] \
                if COMMIT else None

        lines = []
        for desc, qty, unit in inv["lines"]:
            amt = float(Decimal(unit) * qty)
            lines.append({"Amount": amt, "DetailType": "SalesItemLineDetail",
                          "Description": desc,
                          "SalesItemLineDetail": {
                              "ItemRef": {"value": items["Robots"]},
                              "Qty": qty, "UnitPrice": float(Decimal(unit)),
                              "TaxCodeRef": {"value": "NON"}}})
        lines.append({"Amount": float(Decimal(inv["tax"])), "DetailType": "SalesItemLineDetail",
                      "Description": "CA sales tax 9.75%",
                      "SalesItemLineDetail": {"ItemRef": {"value": items["Sales Tax"]},
                                              "TaxCodeRef": {"value": "NON"}}})
        body = {"DocNumber": inv["doc"], "TxnDate": inv["date"], "DueDate": inv["due"],
                "CustomerRef": {"value": cid}, "Line": lines,
                "PrivateNote": f"{inv['doc']} | robot sale per PDF invoice; posted 2026-08-03"}
        log(f"invoice {inv['doc']} {inv['date']} {inv['customer']}: "
            f"{len(lines)} lines, total {inv['total']}")
        if COMMIT:
            r = c.post("invoice", body)
            got = Decimal(str(r["Invoice"]["TotalAmt"]))
            print(f"   posted Id {r['Invoice']['Id']}, QBO total {got}",
                  "OK" if got == Decimal(inv["total"]) else "*** TOTAL MISMATCH ***")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
