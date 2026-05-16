"""Demo: build OLD vs NEW invoice payload side-by-side for one statement.

Read-only. Does NOT touch QBO. Does NOT modify the pipeline.

Picks one statement_id from the Q1 LELNU xlsx and prints:
  - The current "service" shape (one summary line per invoice)
  - The new "non-inventory product" shape (one line per SKU per invoice)

Master Table_updated.xlsx is used to attach product names.

Usage:
    python scripts/demo_inventory_invoice.py
    python scripts/demo_inventory_invoice.py <statement_id>
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook

from tiktok_qbo.ingest.lat_xlsx import read_order_details, read_statements
from tiktok_qbo.money import money_sum, to_money

SHOP = "USLCPLELNU"
DOWNLOADS = Path.home() / "Downloads"
Q1_XLSX = DOWNLOADS / "1-3-2024 (1).xlsx"
MASTER = DOWNLOADS / "Master Table_updated.xlsx"

PLACEHOLDER_CUSTOMER_ID = "58"        # TikTok Shop LELNU (from sandbox)
PLACEHOLDER_SALES_ACCOUNT_ID = "1150040002"  # Sales - TikTok LELNU
PLACEHOLDER_ADJUSTMENT_ITEM_REF = "TBD-platform-adjustment-item"


def load_master(path: Path) -> dict[str, str]:
    """Return {sku_id -> product_name} from the Master Table."""
    wb = load_workbook(path, data_only=True)
    ws = wb.active
    out: dict[str, str] = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[0]:
            continue
        sku = str(row[0]).strip()
        name = str(row[1]).strip() if row[1] else ""
        if sku:
            out[sku] = name
    return out


def pick_demo_statement(rows, statements) -> str:
    """Pick a statement that has multiple delivery dates and multiple SKUs.

    Prefers a statement with at least 2 delivery dates and 5+ unique SKUs so
    the demo shows the itemization clearly.
    """
    by_stmt_dd_sku: dict[str, dict] = defaultdict(lambda: defaultdict(set))
    for r in rows:
        if not r.sku_id or not r.statement_id or r.classification != "sale":
            continue
        if r.order_delivery_date is None:
            continue
        by_stmt_dd_sku[r.statement_id][r.order_delivery_date].add(r.sku_id)

    candidates = []
    for stmt_id, dd_map in by_stmt_dd_sku.items():
        max_skus_in_one_day = max((len(s) for s in dd_map.values()), default=0)
        n_dates = len(dd_map)
        n_skus = sum(len(s) for s in dd_map.values())
        # Rank: (busiest single day, total SKUs, dates count) — favors statements
        # whose first invoice will have many per-SKU lines.
        candidates.append((max_skus_in_one_day, n_skus, n_dates, stmt_id))
    candidates.sort(reverse=True)
    return candidates[0][3] if candidates else ""


def build_old_invoices(rows_for_stmt, statement_id: str) -> list[dict]:
    """Mirror the current build_invoice_body() — one summary line per invoice."""
    by_dd: dict = defaultdict(list)
    for r in rows_for_stmt:
        net = Decimal(r.raw.get("Net sales", "0") or "0")
        if net <= 0:
            continue
        bucket_date = r.order_delivery_date or r.statement_date
        by_dd[bucket_date].append(r)

    out = []
    for dd in sorted(by_dd.keys()):
        group = by_dd[dd]
        net_sales = to_money(money_sum(
            Decimal(r.raw.get("Net sales", "0") or "0") for r in group
        ))
        doc = f"INV-{statement_id[-8:]}-{dd.strftime('%y%m%d')}"
        body = {
            "DocNumber": doc,
            "TxnDate": dd.isoformat(),
            "CustomerRef": {"value": PLACEHOLDER_CUSTOMER_ID},
            "PrivateNote": f"TikTok statement {statement_id}; {len(group)} order rows; "
                           f"delivery date {dd.isoformat()}",
            "Line": [
                {
                    "DetailType": "SalesItemLineDetail",
                    "Amount": float(net_sales),
                    "Description": f"TikTok LELNU sales delivered {dd.isoformat()}",
                    "SalesItemLineDetail": {
                        "ItemAccountRef": {"value": PLACEHOLDER_SALES_ACCOUNT_ID},
                    },
                }
            ],
        }
        out.append(body)
    return out


def build_new_invoices(rows_for_stmt, statement_id: str, master: dict[str, str]) -> list[dict]:
    """New shape — one line per SKU per (statement, delivery date) invoice.

    Same DocNumber, same total. Each line carries ItemRef (placeholder until
    the items.py bootstrap creates real Item Ids), Qty, UnitPrice.
    """
    by_dd: dict = defaultdict(list)
    for r in rows_for_stmt:
        net = Decimal(r.raw.get("Net sales", "0") or "0")
        if net <= 0:
            continue
        bucket_date = r.order_delivery_date or r.statement_date
        by_dd[bucket_date].append(r)

    out = []
    for dd in sorted(by_dd.keys()):
        group = by_dd[dd]
        net_sales_total = to_money(money_sum(
            Decimal(r.raw.get("Net sales", "0") or "0") for r in group
        ))

        # Group by SKU. Rows without a SKU go to a sentinel "platform
        # adjustment" line (E1 from the design).
        by_sku_qty: dict[str, int] = defaultdict(int)
        by_sku_amt: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        name_votes: dict[str, Counter] = defaultdict(Counter)
        no_sku_amt = Decimal("0")
        for r in group:
            sku = r.sku_id.strip() if r.sku_id else ""
            net = Decimal(r.raw.get("Net sales", "0") or "0")
            if not sku or not sku.isdigit():
                no_sku_amt += net
                continue
            by_sku_qty[sku] += int(r.quantity or 0)
            by_sku_amt[sku] += net
            if r.product_name:
                name_votes[sku][r.product_name] += int(r.quantity or 1)

        lines = []
        for sku in sorted(by_sku_amt.keys()):
            qty = by_sku_qty[sku] or 1   # at least 1 even if qty column is blank
            amt = to_money(by_sku_amt[sku])
            unit_price = to_money(amt / qty) if qty else amt
            name = master.get(sku) or (
                name_votes[sku].most_common(1)[0][0]
                if name_votes[sku] else f"SKU {sku}"
            )
            lines.append({
                "DetailType": "SalesItemLineDetail",
                "Amount": float(amt),
                "Description": name[:4000],
                "SalesItemLineDetail": {
                    # In production this becomes a real Item Id from items.py
                    # bootstrap (Type=NonInventory, Sku=sku, IncomeAccount=Sales).
                    "ItemRef": {"value": f"<item:{sku}>", "name": name},
                    "Qty": qty,
                    "UnitPrice": float(unit_price),
                },
            })

        if no_sku_amt != 0:
            lines.append({
                "DetailType": "SalesItemLineDetail",
                "Amount": float(to_money(no_sku_amt)),
                "Description": "TikTok platform adjustment (no SKU)",
                "SalesItemLineDetail": {
                    "ItemRef": {"value": PLACEHOLDER_ADJUSTMENT_ITEM_REF,
                                "name": "TikTok Platform Adjustment"},
                },
            })

        doc = f"INV-{statement_id[-8:]}-{dd.strftime('%y%m%d')}"
        body = {
            "DocNumber": doc,
            "TxnDate": dd.isoformat(),
            "CustomerRef": {"value": PLACEHOLDER_CUSTOMER_ID},
            "PrivateNote": f"TikTok statement {statement_id}; {len(group)} order rows; "
                           f"delivery date {dd.isoformat()}",
            "Line": lines,
        }
        # Sanity check: per-line amounts must sum to the same total
        line_sum = to_money(sum(Decimal(str(l["Amount"])) for l in lines))
        assert line_sum == net_sales_total, (
            f"line sum {line_sum} != net_sales_total {net_sales_total} for {doc}"
        )
        out.append(body)
    return out


def main() -> None:
    if not Q1_XLSX.exists():
        print(f"Q1 xlsx not found at {Q1_XLSX}")
        sys.exit(1)
    if not MASTER.exists():
        print(f"Master table not found at {MASTER}")
        sys.exit(1)

    print(f"Loading {Q1_XLSX.name} ...")
    rows = read_order_details(Q1_XLSX, SHOP)
    statements = read_statements(Q1_XLSX, SHOP)
    print(f"  {len(rows):,} order rows, {len(statements)} statements")

    print(f"Loading {MASTER.name} ...")
    master = load_master(MASTER)
    print(f"  {len(master)} SKUs in master table")
    print()

    if len(sys.argv) > 1:
        stmt_id = sys.argv[1]
    else:
        stmt_id = pick_demo_statement(rows, statements)
        print(f"Auto-picked statement: {stmt_id}")

    rows_for_stmt = [r for r in rows if r.statement_id == stmt_id]
    if not rows_for_stmt:
        print(f"No rows for statement {stmt_id!r}")
        sys.exit(1)

    # Some quick diagnostics
    by_dd_sku = defaultdict(set)
    for r in rows_for_stmt:
        if r.classification == "sale" and r.sku_id and r.order_delivery_date:
            by_dd_sku[r.order_delivery_date].add(r.sku_id)
    print(f"  {len(rows_for_stmt)} rows in statement")
    print(f"  delivery dates with sales: {len(by_dd_sku)}")
    for dd in sorted(by_dd_sku.keys()):
        print(f"    {dd}: {len(by_dd_sku[dd])} unique SKUs")
    print()

    old = build_old_invoices(rows_for_stmt, stmt_id)
    new = build_new_invoices(rows_for_stmt, stmt_id, master)

    # Print side-by-side for the first delivery-date invoice
    if not old or not new:
        print("No positive-net-sales invoices in this statement.")
        return

    # Show the invoice with the most lines in the NEW shape (most illustrative).
    showcase_idx = max(range(len(new)), key=lambda i: len(new[i]["Line"]))

    print("=" * 78)
    print(f"OLD shape  (current production code)  —  {len(old)} invoice(s); showing #{showcase_idx + 1}")
    print("=" * 78)
    print(json.dumps(old[showcase_idx], indent=2, default=str))

    print()
    print("=" * 78)
    print(f"NEW shape  (per-SKU non-inventory)    —  {len(new)} invoice(s); showing #{showcase_idx + 1}")
    print("=" * 78)
    print(json.dumps(new[showcase_idx], indent=2, default=str))

    # Totals comparison
    print()
    print("=" * 78)
    print("TOTAL CHECK  (old total == new total per invoice)")
    print("=" * 78)
    print(f"{'DocNumber':<22} {'OldTotal':>12} {'NewTotal':>12} {'Diff':>10} {'OldLines':>9} {'NewLines':>9}")
    for o, n in zip(old, new):
        ot = sum(Decimal(str(l["Amount"])) for l in o["Line"])
        nt = sum(Decimal(str(l["Amount"])) for l in n["Line"])
        diff = ot - nt
        marker = "" if diff == 0 else "  <-- MISMATCH"
        print(f"{o['DocNumber']:<22} {float(ot):>12.2f} {float(nt):>12.2f} {float(diff):>10.2f} "
              f"{len(o['Line']):>9} {len(n['Line']):>9}{marker}")


if __name__ == "__main__":
    main()
