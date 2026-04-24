import csv
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from tiktok_qbo.models import StatementRow, PaymentRow
from tiktok_qbo.money import money_sum, close_enough


@dataclass
class Mismatch:
    kind: str                     # "identity_1" | "identity_2"
    payment_id: str
    statement_id: str
    expected: Decimal
    computed: Decimal
    diff: Decimal


@dataclass
class ReconcileResult:
    passed: bool
    mismatches: list[Mismatch]


def check_identity_2(statements: list[StatementRow]) -> list[Mismatch]:
    out: list[Mismatch] = []
    for s in statements:
        computed = money_sum([
            s.net_sales, s.shipping, s.fees, s.adjustments, -s.reserve_amount,
        ])
        if not close_enough(computed, s.payable_amount):
            out.append(Mismatch(
                kind="identity_2",
                payment_id=s.payment_id,
                statement_id=s.statement_id,
                expected=s.payable_amount,
                computed=computed,
                diff=(s.payable_amount - computed).quantize(Decimal("0.01")),
            ))
    return out


def check_identity_1(
    statements: list[StatementRow],
    payments: list[PaymentRow],
) -> list[Mismatch]:
    by_payment: dict[str, list[StatementRow]] = defaultdict(list)
    for s in statements:
        if s.payment_id:
            by_payment[s.payment_id].append(s)

    out: list[Mismatch] = []
    pay_by_id = {p.payment_id: p for p in payments}
    for payment_id, group in by_payment.items():
        payment = pay_by_id.get(payment_id)
        if payment is None:
            continue
        computed = money_sum(s.payable_amount for s in group)
        if not close_enough(computed, payment.payment_amount):
            out.append(Mismatch(
                kind="identity_1",
                payment_id=payment_id,
                statement_id=",".join(sorted(s.statement_id for s in group)),
                expected=payment.payment_amount,
                computed=computed,
                diff=(payment.payment_amount - computed).quantize(Decimal("0.01")),
            ))
    return out


def run_reconcile(
    statements: list[StatementRow],
    payments: list[PaymentRow],
    hash: str,
    state_dir: Path,
) -> ReconcileResult:
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    mismatches = check_identity_2(statements) + check_identity_1(statements, payments)

    csv_path = state_dir / f"reconcile-{hash}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["kind", "payment_id", "statement_id", "expected", "computed", "diff"])
        for m in mismatches:
            w.writerow([m.kind, m.payment_id, m.statement_id,
                        str(m.expected), str(m.computed), str(m.diff)])

    passed = not mismatches
    marker = "pass" if passed else "fail"
    (state_dir / f"reconcile-{hash}.{marker}").write_text(
        f"{len(mismatches)} mismatches\n", encoding="utf-8"
    )
    return ReconcileResult(passed=passed, mismatches=mismatches)
