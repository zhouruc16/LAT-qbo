from collections import defaultdict
from decimal import Decimal
from tiktok_qbo.models import NormalizedRow, CreditMemoLine, CreditMemoBatch
from tiktok_qbo.money import money_sum, to_money


def group_credit_memos(rows: list[NormalizedRow], shop_id: str) -> list[CreditMemoBatch]:
    rows_by_date = defaultdict(list)
    for r in rows:
        if r.classification not in ("refund", "chargeback"):
            continue
        if r.statement_date is None:
            continue
        rows_by_date[r.statement_date].append(r)

    batches: list[CreditMemoBatch] = []
    for stmt_date in sorted(rows_by_date.keys()):
        day_rows = rows_by_date[stmt_date]
        lines = [
            CreditMemoLine(
                sku_id=r.sku_id,
                product_name=r.product_name,
                quantity=r.quantity,
                unit_price=to_money(
                    (abs(r.customer_refund) / r.quantity) if r.quantity else 0
                ),
                amount=to_money(abs(r.customer_refund)),
            )
            for r in day_rows
        ]
        total = money_sum(l.amount for l in lines)
        batches.append(CreditMemoBatch(
            shop_id=shop_id,
            statement_date=stmt_date,
            doc_number=f"CM-{shop_id}-{stmt_date.strftime('%Y%m%d')}",
            lines=lines,
            total_amount=total,
            source_order_ids=[r.order_id for r in day_rows],
        ))
    return batches
