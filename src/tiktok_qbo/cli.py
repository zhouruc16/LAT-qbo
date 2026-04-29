import argparse
import sys
from pathlib import Path

from tiktok_qbo.models import StatementRow, PaymentRow
from tiktok_qbo.ingest.pipeline import run_ingest
from tiktok_qbo.plan.pipeline import run_plan
from tiktok_qbo.reconcile import run_reconcile


def _read_jsonl(path: Path, model):
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(model.model_validate_json(line))
    return out


def _cmd_ingest(args):
    result = run_ingest(Path(args.file), shop_id=args.shop, state_dir=Path(args.state_dir))
    print(f"hash={result.hash} rows={result.n_rows} "
          f"statements={result.n_statements} payments={result.n_payments} "
          f"reserves={result.n_reserves}")

def _cmd_plan(args):
    plan_dir = run_plan(hash=args.hash, shop_id=args.shop, state_dir=Path(args.state_dir))
    print(f"plan written to {plan_dir}")

def _cmd_reconcile(args):
    state = Path(args.state_dir)
    stmts = _read_jsonl(state / f"statements-{args.hash}.jsonl", StatementRow)
    pays = _read_jsonl(state / f"payments-{args.hash}.jsonl", PaymentRow)
    result = run_reconcile(stmts, pays, hash=args.hash, state_dir=state)
    label = "PASS" if result.passed else "FAIL"
    print(f"reconcile {label} — {len(result.mismatches)} mismatches")
    return 0 if result.passed else 2


def _cmd_auth(args):
    from tiktok_qbo.qbo.auth import run_auth_flow
    from tiktok_qbo.qbo.env import load_env
    env_path = Path(args.env) if args.env else None
    if env_path is None:
        used = load_env()
        if used is None:
            print("ERROR: no .env found", file=sys.stderr)
            return 1
        env_path = used
    run_auth_flow(env_path)
    return 0


def _cmd_qbo_init(args):
    from tiktok_qbo.qbo.client import QboClient
    from tiktok_qbo.qbo.coa import bootstrap_coa
    from tiktok_qbo.qbo.env import load_creds
    creds = load_creds()
    client = QboClient(creds, dry_run=args.dry_run)
    print(f"Connecting to {creds.environment} (realm={creds.realm_id})")
    refs = bootstrap_coa(client)
    print(f"\nCoA refs: {refs}")
    return 0


def _cmd_post(args):
    from tiktok_qbo.qbo.client import QboClient
    from tiktok_qbo.qbo.coa import bootstrap_coa
    from tiktok_qbo.qbo.env import load_creds
    from tiktok_qbo.qbo.post import post_h1
    from tiktok_qbo.models import NormalizedRow

    state = Path(args.state_dir)
    rows = _read_jsonl(state / f"rows-{args.hash}.jsonl", NormalizedRow)
    stmts = _read_jsonl(state / f"statements-{args.hash}.jsonl", StatementRow)
    pays = _read_jsonl(state / f"payments-{args.hash}.jsonl", PaymentRow)

    if args.payment_id:
        stmts = [s for s in stmts if s.payment_id == args.payment_id]
        pays = [p for p in pays if p.payment_id == args.payment_id]
        stmt_ids = {s.statement_id for s in stmts}
        rows = [r for r in rows if r.statement_id in stmt_ids]

    creds = load_creds()
    client = QboClient(creds, dry_run=args.dry_run)
    coa = bootstrap_coa(client)
    stats = post_h1(client, coa, rows, stmts, pays)
    mode = "DRY RUN" if args.dry_run else "POSTED"
    print(f"\n[{mode}] invoices: created={stats.invoices_created} skipped={stats.invoices_skipped}")
    print(f"[{mode}] journal entries: created={stats.je_created} skipped={stats.je_skipped}")
    if stats.errors:
        print(f"[{mode}] errors: {len(stats.errors)}")
        for e in stats.errors[:5]:
            print(f"  - {e}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="tiktok_qbo")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("ingest")
    pi.add_argument("file")
    pi.add_argument("--shop", default="PLELNU")
    pi.add_argument("--state-dir", default="state")
    pi.set_defaults(fn=_cmd_ingest)

    pp = sub.add_parser("plan")
    pp.add_argument("--hash", required=True)
    pp.add_argument("--shop", default="PLELNU")
    pp.add_argument("--state-dir", default="state")
    pp.set_defaults(fn=_cmd_plan)

    pr = sub.add_parser("reconcile")
    pr.add_argument("--hash", required=True)
    pr.add_argument("--state-dir", default="state")
    pr.set_defaults(fn=_cmd_reconcile)

    pa = sub.add_parser("auth", help="OAuth flow: get/refresh QBO refresh token")
    pa.add_argument("--env", default=None)
    pa.set_defaults(fn=_cmd_auth)

    pq = sub.add_parser("qbo-init", help="Auto-create chart of accounts + customer in QBO")
    pq.add_argument("--dry-run", action="store_true")
    pq.set_defaults(fn=_cmd_qbo_init)

    po = sub.add_parser("post", help="Post invoices + journal entries to QBO")
    po.add_argument("--hash", required=True)
    po.add_argument("--state-dir", default="state")
    po.add_argument("--payment-id", default=None,
                    help="Post only one Payment ID (for previewing)")
    po.add_argument("--dry-run", action="store_true")
    po.set_defaults(fn=_cmd_post)

    args = p.parse_args(argv)
    rc = args.fn(args)
    return rc or 0


if __name__ == "__main__":
    sys.exit(main())
