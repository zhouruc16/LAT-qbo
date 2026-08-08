"""Create UCLA A/R invoices from the two purchase orders (production QBO).

UCLA buys robots FROM RobotX -> each PO becomes an Invoice (debit A/R, credit
Sales of Product Income), Net 30. Pre-tax PO amounts; no sales-tax line (UCLA's
partial R&D exemption rate to be handled separately). Default = PREVIEW;
--commit to write. Idempotent on DocNumber (the PO number).
"""
from __future__ import annotations

import re
import sys
from decimal import Decimal
from pathlib import Path

import pdfplumber

from tiktok_qbo.qbo.env import load_creds
from tiktok_qbo.qbo.client import QboClient, QboError

COMMIT = "--commit" in sys.argv
CUSTOMER = "University of California Los Angeles (UCLA)"
PODIR = Path(r"C:/Users/zhour/OneDrive/文档/xwechat_files/wxid_fxfdqpu6s73512_6ce5/msg/file/2026-07")
POS = [
    ("PO_01600000548083(2).pdf", "01600000548083", "2026-04-16"),
    ("PO_01600000548084.pdf",     "01600000548084", "2026-04-30"),
]
LINE_RX = re.compile(r"\d+ of \d+ .*?RobotX Inc - (.+?)\s+([\d,]+\.\d{2}) USD (\d+) ([\d,]+\.\d{2}) USD")


def parse_po(fn):
    t = "\n".join((p.extract_text() or "") for p in pdfplumber.open(PODIR / fn).pages)
    out = []
    for m in LINE_RX.finditer(t):
        desc, unit, qty, ext = m.groups()
        out.append((desc.strip(), Decimal(unit.replace(",", "")), int(qty), Decimal(ext.replace(",", ""))))
    return out


def log(m): print(("[WRITE] " if COMMIT else "[preview] ") + m)


def main():
    c = QboClient(load_creds(Path(".env.robotx.prod")))
    print(f"\n{'#'*60}\n{'COMMIT - WRITING TO REAL BOOKS' if COMMIT else 'PREVIEW (no writes)'}\n{'#'*60}\n")

    def q1(e, f, v):
        s = str(v).replace("'", "\\'")
        r = c.query(f"SELECT * FROM {e} WHERE {f} = '{s}'").get("QueryResponse", {}).get(e, [])
        return r[0] if r else None

    # customer + item
    cust = q1("Customer", "DisplayName", CUSTOMER)
    if cust:
        cust_id = cust["Id"]
    else:
        log(f"create customer: {CUSTOMER}")
        cust_id = c.post("customer", {"DisplayName": CUSTOMER}) ["Customer"]["Id"] if COMMIT else "C-UCLA"
    item = q1("Item", "Name", "Robots")
    item_id = item["Id"] if item else None

    existing = {i.get("DocNumber") for i in c.query("SELECT * FROM Invoice").get("QueryResponse", {}).get("Invoice", [])}

    for fn, docnum, tdate in POS:
        lines = parse_po(fn)
        total = sum(l[3] for l in lines)
        if docnum in existing:
            print(f"  [exists] invoice {docnum} — skip"); continue
        print(f"\n  Invoice {docnum}  ({tdate}, Net 30)  {len(lines)} lines  total ${total:,.2f}")
        inv_lines = []
        for desc, unit, qty, ext in lines:
            print(f"    - {desc[:46]:46} {qty} x {float(unit):>10,.2f} = {float(ext):>11,.2f}")
            inv_lines.append({
                "Amount": f"{ext:.2f}", "DetailType": "SalesItemLineDetail",
                "Description": desc[:200],
                "SalesItemLineDetail": {"ItemRef": {"value": item_id}, "Qty": qty,
                                        "UnitPrice": f"{unit:.2f}"}})
        body = {"DocNumber": docnum, "TxnDate": tdate,
                "CustomerRef": {"value": cust_id},
                "SalesTermRef": None,
                "PrivateNote": f"UCLA PO {docnum} — robots sold to UCLA (Net 30)",
                "Line": inv_lines}
        body = {k: v for k, v in body.items() if v is not None}
        log(f"create Invoice {docnum} -> A/R ${total:,.2f}")
        if COMMIT:
            try:
                c.post("invoice", body)
            except QboError as ex:
                print("   ERROR", str(ex)[:200])

    if not COMMIT:
        print("\nPREVIEW only. Re-run with --commit to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
