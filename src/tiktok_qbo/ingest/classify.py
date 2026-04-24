from decimal import Decimal
from tiktok_qbo.models import Classification

KNOWN_ROW_TYPES = {
    "Order", "Chargeback",
    "TikTok Shop reimbursement", "Logistics reimbursement",
}

def classify(raw: dict) -> Classification:
    row_type = str(raw.get("Type", "")).strip()
    if row_type == "Chargeback":
        return "chargeback"
    if row_type in ("TikTok Shop reimbursement", "Logistics reimbursement"):
        return "reimbursement"
    if row_type == "Order":
        gross_sales = Decimal(str(raw.get("Gross sales", 0) or 0))
        gross_refund = Decimal(str(raw.get("Gross sales refund", 0) or 0))
        customer_payment = Decimal(str(raw.get("Customer payment", 0) or 0))
        quantity = int(raw.get("Quantity", 0) or 0)

        if gross_sales > 0 and gross_refund == 0 and quantity > 0:
            return "sale"
        if gross_refund < 0 and gross_sales <= Decimal("0.01"):
            return "refund"
        if gross_sales == 0 and customer_payment == 0:
            return "fee_only"
        return "adjustment"
    if row_type not in KNOWN_ROW_TYPES:
        raise ValueError(f"Unknown LAT row Type: {row_type!r}")
    return "adjustment"
