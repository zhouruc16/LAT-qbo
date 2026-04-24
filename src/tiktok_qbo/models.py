from datetime import date
from decimal import Decimal
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict

Classification = Literal["sale", "refund", "chargeback", "reimbursement", "fee_only", "adjustment"]

class _Base(BaseModel):
    model_config = ConfigDict(frozen=False, arbitrary_types_allowed=False)

class NormalizedRow(_Base):
    shop_id: str
    order_id: str
    sku_id: str
    statement_id: str
    payment_id: str
    statement_date: date | None
    order_created_date: date | None
    order_shipment_date: date | None
    order_delivery_date: date | None
    row_type: str
    classification: Classification
    customer_payment: Decimal
    customer_refund: Decimal
    gross_sales: Decimal
    quantity: int
    product_name: str
    raw: dict[str, Any]

class StatementRow(_Base):
    shop_id: str
    statement_id: str
    payment_id: str
    statement_date: date
    status: str
    total_settlement_amount: Decimal
    net_sales: Decimal
    shipping: Decimal
    fees: Decimal
    adjustments: Decimal
    reserve_amount: Decimal
    payable_amount: Decimal

class PaymentRow(_Base):
    shop_id: str
    payment_id: str
    payment_amount: Decimal
    payment_initiation_date: date
    payment_completion_date: date
    bank_posted_date: date | None = None
    bank_account_masked: str
    status: str

class ReserveRow(_Base):
    shop_id: str
    statement_id: str
    reserve_id: str
    reserve_amount: Decimal
    reserve_date: date | None
    release_date: date | None
    status: str

class InvoiceLine(_Base):
    sku_id: str
    product_name: str
    quantity: int
    unit_price: Decimal
    amount: Decimal

class InvoiceBatch(_Base):
    shop_id: str
    delivery_date: date
    doc_number: str
    lines: list[InvoiceLine]
    total_amount: Decimal
    source_order_ids: list[str]

class CreditMemoLine(_Base):
    sku_id: str
    product_name: str
    quantity: int
    unit_price: Decimal
    amount: Decimal

class CreditMemoBatch(_Base):
    shop_id: str
    statement_date: date
    doc_number: str
    lines: list[CreditMemoLine]
    total_amount: Decimal
    source_order_ids: list[str]

class JELine(_Base):
    account_role: str      # "bank", "reserve", "fees", "shipping", "adjustments", "clearing"
    side: Literal["DR", "CR"]
    amount: Decimal        # always positive; side determines posting direction
    memo: str = ""

class StatementJE(_Base):
    shop_id: str
    payment_id: str
    doc_number: str
    txn_date: date
    lines: list[JELine]
    statement_ids: list[str]
    bank_amount: Decimal
