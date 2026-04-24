from pathlib import Path
from tiktok_qbo.models import (
    NormalizedRow, StatementRow, PaymentRow,
)
from tiktok_qbo.plan.invoices import group_invoices
from tiktok_qbo.plan.credit_memos import group_credit_memos
from tiktok_qbo.plan.statement_je import build_statement_jes


def _read_jsonl(path: Path, model):
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(model.model_validate_json(line))
    return out


def run_plan(hash: str, shop_id: str, state_dir: Path) -> Path:
    state_dir = Path(state_dir)
    rows = _read_jsonl(state_dir / f"rows-{hash}.jsonl", NormalizedRow)
    stmts = _read_jsonl(state_dir / f"statements-{hash}.jsonl", StatementRow)
    pays = _read_jsonl(state_dir / f"payments-{hash}.jsonl", PaymentRow)

    invoices = group_invoices(rows, shop_id=shop_id)
    credit_memos = group_credit_memos(rows, shop_id=shop_id)
    jes = build_statement_jes(stmts, pays, shop_id=shop_id)

    plan_dir = state_dir / f"plan-{hash}"
    (plan_dir / "invoices").mkdir(parents=True, exist_ok=True)
    (plan_dir / "credit_memos").mkdir(parents=True, exist_ok=True)
    (plan_dir / "journal_entries").mkdir(parents=True, exist_ok=True)

    for b in invoices:
        fname = f"{b.delivery_date.strftime('%Y%m%d')}.json"
        (plan_dir / "invoices" / fname).write_text(
            b.model_dump_json(indent=2), encoding="utf-8"
        )
    for b in credit_memos:
        fname = f"{b.statement_date.strftime('%Y%m%d')}.json"
        (plan_dir / "credit_memos" / fname).write_text(
            b.model_dump_json(indent=2), encoding="utf-8"
        )
    for je in jes:
        fname = f"{je.payment_id}.json"
        (plan_dir / "journal_entries" / fname).write_text(
            je.model_dump_json(indent=2), encoding="utf-8"
        )

    return plan_dir
