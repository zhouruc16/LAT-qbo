from collections import defaultdict
from decimal import Decimal
from tiktok_qbo.models import NormalizedRow, InvoiceLine, InvoiceBatch
from tiktok_qbo.money import money_sum, to_money


def group_invoices(rows: list[NormalizedRow], shop_id: str) -> list[InvoiceBatch]:
    sales_by_date = defaultdict(list)
    for r in rows:
        if r.classification != "sale":
            continue
        if r.order_delivery_date is None:
            continue
        sales_by_date[r.order_delivery_date].append(r)

    batches: list[InvoiceBatch] = []
    for delivery_date in sorted(sales_by_date.keys()):
        day_rows = sales_by_date[delivery_date]
        lines = [
            InvoiceLine(
                sku_id=r.sku_id,
                product_name=r.product_name,
                quantity=r.quantity,
                unit_price=to_money(
                    (r.customer_payment / r.quantity) if r.quantity else 0
                ),
                amount=r.customer_payment,
            )
            for r in day_rows
        ]
        total = money_sum(l.amount for l in lines)
        batches.append(InvoiceBatch(
            shop_id=shop_id,
            delivery_date=delivery_date,
            doc_number=f"INV-{shop_id}-{delivery_date.strftime('%Y%m%d')}",
            lines=lines,
            total_amount=total,
            source_order_ids=[r.order_id for r in day_rows],
        ))
    return batches
