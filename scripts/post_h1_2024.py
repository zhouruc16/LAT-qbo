"""Post H1 2024 LELNU TikTok data to QBO.

End-to-end driver:
  1. Ingest Q1 + Q2 xlsx (Order details, Statements, Payments)
  2. Filter to H1 (statement_date in [2024-01-01, 2024-06-30])
  3. Run identity checks (must pass)
  4. Connect to QBO, bootstrap COA + Customer
  5. Post invoices + journal entries (idempotent, skips existing)

Usage:
    python scripts/post_h1_2024.py --dry-run                  # preview only
    python scripts/post_h1_2024.py --payment-id 3459076539020317035   # single statement
    python scripts/post_h1_2024.py                            # full H1 production post
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
from tiktok_qbo.qbo.post import post_h1
from tiktok_qbo.reconcile import check_identity_1, check_identity_2

DOWNLOADS = Path(r"C:\Users\zhour\Downloads")
Q1_XLSX = DOWNLOADS / "1-3-2024.xlsx"
Q2_XLSX = DOWNLOADS / "4-6-2024.xlsx"
SHOP_ID = "PLELNU"
H1_START = date(2024, 1, 1)
H1_END = date(2024, 6, 30)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Build payloads but don't actually call QBO API")
    parser.add_argument("--payment-id", default=None,
                        help="Post only one Payment ID (for previewing)")
    args = parser.parse_args(argv)

    print("[1/4] Ingesting Q1 + Q2 xlsx...")
    rows = read_order_details(Q1_XLSX, shop_id=SHOP_ID) + read_order_details(Q2_XLSX, shop_id=SHOP_ID)
    stmts = read_statements(Q1_XLSX, shop_id=SHOP_ID) + read_statements(Q2_XLSX, shop_id=SHOP_ID)
    pays = read_payments(Q1_XLSX, shop_id=SHOP_ID) + read_payments(Q2_XLSX, shop_id=SHOP_ID)

    rows = [r for r in rows
            if r.statement_date and H1_START <= r.statement_date <= H1_END]
    stmts_h1 = [s for s in stmts if H1_START <= s.statement_date <= H1_END]
    pays_h1 = [p for p in pays if H1_START <= p.payment_initiation_date <= H1_END]

    print(f"  rows={len(rows)} statements={len(stmts_h1)} payments={len(pays_h1)}")

    print("\n[2/4] Identity checks...")
    id2 = check_identity_2(stmts_h1)
    id1 = check_identity_1(stmts_h1, pays_h1)
    if id1 or id2:
        print(f"  FAILED: {len(id2)} identity_2 + {len(id1)} identity_1 mismatches")
        return 2
    print(f"  identity_2: 0 mismatches")
    print(f"  identity_1: 0 mismatches")

    if args.payment_id:
        print(f"\n  Filtering to single Payment ID: {args.payment_id}")
        stmts_h1 = [s for s in stmts_h1 if s.payment_id == args.payment_id]
        pays_h1 = [p for p in pays_h1 if p.payment_id == args.payment_id]
        stmt_ids = {s.statement_id for s in stmts_h1}
        rows = [r for r in rows if r.statement_id in stmt_ids]
        print(f"  filtered: rows={len(rows)} statements={len(stmts_h1)} payments={len(pays_h1)}")

    print("\n[3/4] Connecting to QBO...")
    creds = load_creds()
    if creds.environment == "production":
        print(f"  *** PRODUCTION QBO (realm={creds.realm_id}) ***")
        if not args.dry_run and not args.payment_id:
            print(f"  About to post {len(stmts_h1)} statements / {len(pays_h1)} payments")
            print(f"  Press Enter to continue, or Ctrl+C to abort...")
            try:
                input()
            except KeyboardInterrupt:
                print("Aborted.")
                return 1
    client = QboClient(creds, dry_run=args.dry_run)
    coa = bootstrap_coa(client)

    print("\n[4/4] Posting...")
    stats = post_h1(client, coa, rows, stmts_h1, pays_h1)
    mode = "DRY RUN" if args.dry_run else "POSTED"
    print(f"\n[{mode}] invoices: created={stats.invoices_created} skipped={stats.invoices_skipped}")
    print(f"[{mode}] journal entries: created={stats.je_created} skipped={stats.je_skipped}")
    if stats.errors:
        print(f"[{mode}] errors: {len(stats.errors)}")
        for e in stats.errors[:10]:
            print(f"  - {e}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
