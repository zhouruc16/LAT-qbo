"""Manual smoke test: run full pipeline against the real Q2 2024 LAT file.

Not a pytest test — this hits a real file outside the repo and is for human
inspection of plan outputs before we wire up QBO posting.
"""
import sys
from pathlib import Path
from tiktok_qbo.ingest.pipeline import run_ingest
from tiktok_qbo.plan.pipeline import run_plan
from tiktok_qbo.reconcile import run_reconcile
from tiktok_qbo.models import StatementRow, PaymentRow


def _read_jsonl(path, model):
    return [model.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main():
    lat = Path.home() / "Downloads" / "4-6-2024.xlsx"
    state = Path("state")
    ingest = run_ingest(lat, shop_id="PLELNU", state_dir=state)
    print(f"ingest: hash={ingest.hash} rows={ingest.n_rows} "
          f"statements={ingest.n_statements} payments={ingest.n_payments}")

    plan_dir = run_plan(hash=ingest.hash, shop_id="PLELNU", state_dir=state)
    print(f"plan: {plan_dir}")
    print(f"  invoices       : {len(list((plan_dir/'invoices').glob('*.json')))}")
    print(f"  credit memos   : {len(list((plan_dir/'credit_memos').glob('*.json')))}")
    print(f"  journal entries: {len(list((plan_dir/'journal_entries').glob('*.json')))}")

    stmts = _read_jsonl(state / f"statements-{ingest.hash}.jsonl", StatementRow)
    pays = _read_jsonl(state / f"payments-{ingest.hash}.jsonl", PaymentRow)
    result = run_reconcile(stmts, pays, hash=ingest.hash, state_dir=state)
    print(f"reconcile: {'PASS' if result.passed else 'FAIL'} "
          f"({len(result.mismatches)} mismatches)")
    for m in result.mismatches[:10]:
        print(f"  {m.kind} payment={m.payment_id} stmt={m.statement_id} "
              f"expected={m.expected} computed={m.computed} diff={m.diff}")
    sys.exit(0 if result.passed else 2)


if __name__ == "__main__":
    main()
