"""Post H2 2024 (Jul-Dec) LELNU TikTok data to QBO.

Companion to post_h1_2024.py — same flow, different input files and
date range.

Sources:
  - 7-9-2024 update.xlsx   : Q3 (statement dates 2024-07-01 → 2024-09-30)
                             NOTE: the file '7-9-2024.xlsx' in Downloads/
                             actually contains Q3 2025 data and is NOT used.
                             The '7-9-2024 update.xlsx' file is the correct
                             Q3 2024 export.
  - 10-12-2024.xlsx        : Q4 main (statement dates 2024-09-27 → 2024-12-25)
  - 1226-31-2024.xlsx      : late-Dec supplementary (2024-12-26 → 2024-12-31)

Filtered to statement_date in [2024-07-01, 2024-12-31].

Q3/Q4 boundary statements (statement_date 2024-09-27 → 2024-09-30) appear
in both Q3 and Q4 files; idempotency by DocNumber prevents double-posting.

Usage:
    python scripts/post_h2_2024.py --dry-run                  # preview only
    python scripts/post_h2_2024.py --payment-id <id>          # single payment
    python scripts/post_h2_2024.py                            # full post
    python scripts/post_h2_2024.py --no-items                 # skip item itemization
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from tiktok_qbo.ingest.lat_xlsx import (
    read_order_details, read_statements, read_payments,
)
from tiktok_qbo.qbo.client import QboClient
from tiktok_qbo.qbo.coa import bootstrap_coa
from tiktok_qbo.qbo.env import load_creds
from tiktok_qbo.qbo.items import (
    ItemRefs, bootstrap_items, build_sku_name_map, load_master_table,
)
from tiktok_qbo.qbo.post import post_h1  # name is historical; function is generic
from tiktok_qbo.reconcile import check_identity_1, check_identity_2

DOWNLOADS_TT = Path(r"C:\Users\zhour\Downloads\TT202425 statement")
DOWNLOADS = Path(r"C:\Users\zhour\Downloads")
Q3_XLSX = DOWNLOADS / "7-9-2024 update.xlsx"
Q4_XLSX = DOWNLOADS_TT / "10-12-2024.xlsx"
LATE_DEC_XLSX = DOWNLOADS_TT / "1226-31-2024.xlsx"
MASTER_TABLE = DOWNLOADS / "Master Table_updated.xlsx"
SHOP_ID = "PLELNU"
PERIOD_START = date(2024, 7, 1)   # everything after H1
PERIOD_END = date(2024, 12, 31)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Build payloads but don't actually call QBO API")
    parser.add_argument("--payment-id", default=None,
                        help="Post only one Payment ID (for previewing)")
    parser.add_argument("--no-items", action="store_true",
                        help="Skip Non-Inventory item bootstrap; post invoices "
                             "with the legacy single-summary-line shape")
    args = parser.parse_args(argv)

    print(f"[1/4] Ingesting H2 2024 (Q3 + Q4 + late-Dec)...")
    print(f"  {Q3_XLSX.name}")
    print(f"  {Q4_XLSX.name}")
    print(f"  {LATE_DEC_XLSX.name}")

    # Dedup at file-boundary level: Q3 file and Q4 file overlap on
    # 2024-09-27..09-30 boundary statements. Each file's Order details
    # sheet re-reports the same rows for these overlap statements. We
    # process files in order (Q3 first); for each statement_id, we keep
    # rows from whichever file first reported it. Same for payments.
    rows: list = []
    stmts: list = []
    pays: list = []
    seen_stmt_ids: set[str] = set()
    seen_pay_ids: set[str] = set()
    for xlsx in [Q3_XLSX, Q4_XLSX, LATE_DEC_XLSX]:
        file_stmts = read_statements(xlsx, shop_id=SHOP_ID)
        file_pays = read_payments(xlsx, shop_id=SHOP_ID)
        file_rows = read_order_details(xlsx, shop_id=SHOP_ID)
        new_stmt_ids = set()
        for s in file_stmts:
            if s.statement_id not in seen_stmt_ids:
                stmts.append(s)
                seen_stmt_ids.add(s.statement_id)
                new_stmt_ids.add(s.statement_id)
        for r in file_rows:
            if r.statement_id in new_stmt_ids:
                rows.append(r)
        for p in file_pays:
            if p.payment_id not in seen_pay_ids:
                pays.append(p)
                seen_pay_ids.add(p.payment_id)

    # Filter to target period
    rows = [r for r in rows
            if r.statement_date and PERIOD_START <= r.statement_date <= PERIOD_END]
    stmts_p = [s for s in stmts if PERIOD_START <= s.statement_date <= PERIOD_END]
    pays_p = [p for p in pays if PERIOD_START <= p.payment_initiation_date <= PERIOD_END]

    print(f"  rows={len(rows)} statements={len(stmts_p)} payments={len(pays_p)}")
    if stmts_p:
        stmt_dates = sorted({s.statement_date for s in stmts_p})
        print(f"  statement_date range: {stmt_dates[0]} - {stmt_dates[-1]}")

    print("\n[2/4] Identity checks...")
    id2 = check_identity_2(stmts_p)
    id1 = check_identity_1(stmts_p, pays_p)
    if id1 or id2:
        print(f"  FAILED: {len(id2)} identity_2 + {len(id1)} identity_1 mismatches")
        # Show the first few so user can investigate
        for m in (id2[:3] + id1[:3]):
            print(f"    {m}")
        return 2
    print(f"  identity_2: 0 mismatches")
    print(f"  identity_1: 0 mismatches")

    if args.payment_id:
        print(f"\n  Filtering to single Payment ID: {args.payment_id}")
        stmts_p = [s for s in stmts_p if s.payment_id == args.payment_id]
        pays_p = [p for p in pays_p if p.payment_id == args.payment_id]
        stmt_ids = {s.statement_id for s in stmts_p}
        rows = [r for r in rows if r.statement_id in stmt_ids]
        print(f"  filtered: rows={len(rows)} statements={len(stmts_p)} payments={len(pays_p)}")

    print("\n[3/4] Connecting to QBO...")
    creds = load_creds()
    if creds.environment == "production":
        print(f"  *** PRODUCTION QBO (realm={creds.realm_id}) ***")
        if not args.dry_run and not args.payment_id:
            print(f"  About to post {len(stmts_p)} statements / {len(pays_p)} payments")
            print(f"  Press Enter to continue, or Ctrl+C to abort...")
            try:
                input()
            except KeyboardInterrupt:
                print("Aborted.")
                return 1
    client = QboClient(creds, dry_run=args.dry_run)
    coa = bootstrap_coa(client)

    item_refs: ItemRefs | None = None
    if not args.no_items:
        print("\n[3.5/4] Bootstrapping Non-Inventory items from Master Table...")
        if not MASTER_TABLE.exists():
            print(f"  ERROR: {MASTER_TABLE} not found. "
                  f"Run with --no-items to skip itemization, or re-export the Master Table.")
            return 2
        master = load_master_table(MASTER_TABLE)
        sku_to_name = build_sku_name_map(master, rows)
        print(f"  master table: {len(master)} priced SKUs")
        print(f"  H2 universe : {len(sku_to_name)} SKUs to bootstrap")
        item_refs = bootstrap_items(client, sku_to_name, coa.sales_id)

    print("\n[4/4] Posting...")
    stats = post_h1(client, coa, rows, stmts_p, pays_p, item_refs)
    mode = "DRY RUN" if args.dry_run else "POSTED"
    print(f"\n[{mode}] invoices:        created={stats.invoices_created} skipped={stats.invoices_skipped}")
    print(f"[{mode}] credit memos:    created={stats.cm_created} skipped={stats.cm_skipped}")
    print(f"[{mode}] receive payments: created={stats.payment_created} skipped={stats.payment_skipped}")
    print(f"[{mode}] journal entries: created={stats.je_created} skipped={stats.je_skipped}")
    if stats.errors:
        print(f"[{mode}] errors: {len(stats.errors)}")
        for e in stats.errors[:10]:
            print(f"  - {e}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
