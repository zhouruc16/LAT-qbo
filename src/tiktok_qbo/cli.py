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

    args = p.parse_args(argv)
    rc = args.fn(args)
    return rc or 0


if __name__ == "__main__":
    sys.exit(main())
