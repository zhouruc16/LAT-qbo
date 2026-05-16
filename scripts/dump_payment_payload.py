"""Dump the actual JSON payload that would be POSTed for one Payment ID.

Read-only against QBO (uses dry-run client). Captures every body the
pipeline would send for ONE payment, organized by entity type.

Usage:
    python scripts/dump_payment_payload.py 3459122815798382955
    python scripts/dump_payment_payload.py 3459122815798382955 --out payload.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from tiktok_qbo.ingest.lat_xlsx import (
    read_order_details, read_payments, read_statements,
)
from tiktok_qbo.qbo.client import QboClient
from tiktok_qbo.qbo.coa import bootstrap_coa
from tiktok_qbo.qbo.env import load_creds
from tiktok_qbo.qbo.items import (
    bootstrap_items, build_sku_name_map, load_master_table,
)
from tiktok_qbo.qbo.post import post_h1

DOWN = Path(r"C:\Users\zhour\Downloads")
SHOP = "PLELNU"
H1_START, H1_END = date(2024, 1, 1), date(2024, 6, 30)


class CapturingClient:
    """Wraps a real QboClient but intercepts post() to capture bodies."""

    def __init__(self, real: QboClient):
        self.real = real
        self.captured: list[tuple[str, dict]] = []
        self._counter = 0

    def query(self, sql: str) -> dict:
        return self.real.query(sql)

    def get(self, path: str, params=None):
        return self.real.get(path, params)

    def post(self, path: str, body: dict) -> dict:
        from tiktok_qbo.qbo.client import _qbo_entity_key
        self.captured.append((path, body))
        self._counter += 1
        entity_key = _qbo_entity_key(path)
        # Synthetic Id like QboClient.dry_run does
        return {entity_key: {**body, "Id": f"DRY-{path}-{self._counter}"}}


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("payment_id")
    p.add_argument("--out", default=None,
                   help="Write full JSON to this file (default: stdout summary only)")
    args = p.parse_args(argv)

    print(f"Ingesting xlsx ...")
    stmts = read_statements(DOWN/'1-3-2024.xlsx', shop_id=SHOP) + read_statements(DOWN/'4-6-2024.xlsx', shop_id=SHOP)
    pays  = read_payments (DOWN/'1-3-2024.xlsx', shop_id=SHOP) + read_payments (DOWN/'4-6-2024.xlsx', shop_id=SHOP)
    rows  = read_order_details(DOWN/'1-3-2024.xlsx', shop_id=SHOP) + read_order_details(DOWN/'4-6-2024.xlsx', shop_id=SHOP)
    stmts = [s for s in stmts if H1_START <= s.statement_date <= H1_END and s.payment_id == args.payment_id]
    pays  = [p for p in pays  if p.payment_id == args.payment_id]
    stmt_ids = {s.statement_id for s in stmts}
    rows  = [r for r in rows if r.statement_id in stmt_ids]
    print(f"  filtered: rows={len(rows)} statements={len(stmts)} payments={len(pays)}")

    creds = load_creds()
    real = QboClient(creds, dry_run=False)  # capturing wrapper handles dry-run
    cap = CapturingClient(real)
    coa = bootstrap_coa(cap)

    master = load_master_table(DOWN / "Master Table_updated.xlsx")
    sku_to_name = build_sku_name_map(master, rows)
    print(f"Bootstrapping {len(sku_to_name)} items (synthetic IDs in dry-run)...")
    item_refs = bootstrap_items(cap, sku_to_name, coa.sales_id, verbose=False)

    print(f"Building payloads for payment {args.payment_id}...")
    stats = post_h1(cap, coa, rows, stmts, pays, item_refs)
    if stats.errors:
        print("ERRORS:")
        for e in stats.errors:
            print(f"  - {e}")

    # Group captures by entity path
    by_path: dict[str, list[dict]] = {}
    for path, body in cap.captured:
        by_path.setdefault(path, []).append(body)

    print()
    print(f"{'entity':<14} {'count':>6}")
    for path, bodies in sorted(by_path.items()):
        print(f"{path:<14} {len(bodies):>6}")
    print()

    # Pick one of each entity to print as a sample
    for path in ("item", "invoice", "creditmemo", "payment", "journalentry"):
        bodies = by_path.get(path, [])
        if not bodies:
            continue
        # For invoice/CM, prefer the one with the most lines.
        if path in ("invoice", "creditmemo"):
            sample = max(bodies, key=lambda b: len(b.get("Line", [])))
        else:
            sample = bodies[0]
        print("=" * 78)
        print(f"SAMPLE {path}  ({len(sample.get('Line', []))} line(s))")
        print("=" * 78)
        print(json.dumps(sample, indent=2, default=str))
        print()

    if args.out:
        Path(args.out).write_text(
            json.dumps({k: v for k, v in by_path.items()}, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"Full payload written to {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
