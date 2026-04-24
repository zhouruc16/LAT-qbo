from collections import defaultdict
from decimal import Decimal
from tiktok_qbo.models import StatementRow, PaymentRow, JELine, StatementJE
from tiktok_qbo.money import money_sum, to_money


def _leg(role: str, amount: Decimal) -> JELine:
    amt = to_money(amount)
    if amt >= 0:
        return JELine(account_role=role, side="DR", amount=amt)
    return JELine(account_role=role, side="CR", amount=to_money(abs(amt)))


def _doc_number(shop_id: str, payment_id: str) -> str:
    tail = payment_id[-12:] if len(payment_id) > 12 else payment_id
    return f"JE-{shop_id}-{tail}"


def build_statement_jes(
    statements: list[StatementRow],
    payments: list[PaymentRow],
    shop_id: str,
) -> list[StatementJE]:
    pay_by_id = {p.payment_id: p for p in payments}
    stmts_by_payment: dict[str, list[StatementRow]] = defaultdict(list)
    for s in statements:
        if not s.payment_id:
            continue
        stmts_by_payment[s.payment_id].append(s)

    jes: list[StatementJE] = []
    for payment_id in sorted(stmts_by_payment.keys()):
        payment = pay_by_id.get(payment_id)
        if payment is None:
            continue
        group = stmts_by_payment[payment_id]

        bank = to_money(payment.payment_amount)
        reserve = to_money(money_sum(s.reserve_amount for s in group))
        fees_abs = to_money(abs(money_sum(s.fees for s in group)))
        shipping = money_sum(s.shipping for s in group)
        adjustments = money_sum(s.adjustments for s in group)

        prelim_lines = [
            JELine(account_role="bank", side="DR", amount=bank),
            JELine(account_role="reserve", side="DR", amount=reserve),
            JELine(account_role="fees", side="DR", amount=fees_abs),
            _leg("shipping", shipping),
            _leg("adjustments", adjustments),
        ]
        dr_total = sum((l.amount for l in prelim_lines if l.side == "DR"), Decimal("0"))
        cr_total = sum((l.amount for l in prelim_lines if l.side == "CR"), Decimal("0"))
        clearing_amount = to_money(dr_total - cr_total)

        lines = prelim_lines + [
            JELine(account_role="clearing", side="CR", amount=clearing_amount),
        ]

        jes.append(StatementJE(
            shop_id=shop_id,
            payment_id=payment_id,
            doc_number=_doc_number(shop_id, payment_id),
            txn_date=payment.payment_completion_date,
            lines=lines,
            statement_ids=sorted(s.statement_id for s in group),
            bank_amount=bank,
        ))
    return jes
