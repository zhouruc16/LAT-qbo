# TikTok LAT → QBO Harness — Part 1: Local Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build stages 1–3 (ingest → plan → reconcile) of the TikTok LAT → QBO harness. Produces QBO-ready JSON artifacts from a real LAT `.xlsx` and proves they reconcile, with zero QBO API involvement.

**Architecture:** Python CLI with five modules per stage. Each stage reads only the previous stage's on-disk artifacts (JSONL / per-entity JSON). Decimal-for-money, dataclasses serialized as JSON. TDD throughout: write the failing test, make it pass, commit.

**Tech Stack:** Python 3.11+, `openpyxl` (xlsx read), `pydantic` v2 (dataclass validation + JSON), `pyyaml` (config), `pytest`, standard library everywhere else.

**Spec:** `docs/superpowers/specs/2026-04-23-tiktok-qbo-harness-design.md` — §§1, 2, 3, 5, 6.

**Prerequisite:** Working directory is `C:\Users\zhour\OneDrive\文档\accounting`. It is **not** currently a git repo; Task 1 initializes one.

---

## File Structure (locked before tasks)

Created by this plan:

```
tiktok_qbo/                        (package root in src/)
  src/tiktok_qbo/
    __init__.py
    cli.py                         # argparse dispatcher (ingest / plan / reconcile)
    config.py                      # load shops.yaml, accounts.yaml
    money.py                       # Decimal helpers, 2dp quantize
    dates.py                       # LAT date parsing
    models.py                      # NormalizedRow, StatementRow, PaymentRow, ReserveRow,
                                   # InvoiceBatch, CreditMemoBatch, StatementJE, JELine, etc.
    ingest/
      __init__.py
      lat_xlsx.py                  # sheet-by-sheet readers
      classify.py                  # 6-way decision tree
      pipeline.py                  # orchestrates ingest, writes JSONL, computes hash
    plan/
      __init__.py
      invoices.py                  # sale rows → InvoiceBatch per (shop, delivery_date)
      credit_memos.py              # refund/chargeback → CreditMemoBatch per (shop, stmt_date)
      statement_je.py              # Statements+Payments → StatementJE per (shop, payment_id)
      pipeline.py                  # runs all three groupers, writes plan-<hash>/
    reconcile.py                   # Identity 1 & 2 + CSV diagnostic
  config/
    shops.yaml
    accounts.yaml
    overrides.yaml                 # empty placeholder
  tests/
    conftest.py                    # fixture factory
    fixtures/
      tiny.xlsx                    # built programmatically in conftest
    test_money.py
    test_dates.py
    test_classify.py
    test_ingest_lat_xlsx.py
    test_ingest_pipeline.py
    test_plan_invoices.py
    test_plan_credit_memos.py
    test_plan_statement_je.py
    test_reconcile.py
    test_cli.py
  pyproject.toml
  .env.example
  .gitignore
  README.md                        # pipeline-only quickstart
```

Rationale: split per responsibility (ingest / plan / reconcile) and per entity type within plan (invoices / credit_memos / statement_je). Each module is small enough to hold in context. `pipeline.py` files are thin orchestrators so core logic stays pure and testable.

---

## Task 1: Project scaffold + git init

**Files:**
- Create: `.gitignore`, `pyproject.toml`, `.env.example`, `README.md`, `src/tiktok_qbo/__init__.py`, `config/shops.yaml`, `config/accounts.yaml`, `config/overrides.yaml`, `tests/conftest.py`

- [ ] **Step 1: Initialize git repo**

```bash
cd "C:/Users/zhour/OneDrive/文档/accounting"
git init
git config user.email "zhouruc16@gmail.com"
git config user.name "LAT Group"
```

- [ ] **Step 2: Write `.gitignore`**

```
__pycache__/
*.pyc
.venv/
.env
state/
inputs/
*.egg-info/
.pytest_cache/
.mypy_cache/
dist/
build/
```

- [ ] **Step 3: Write `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "tiktok_qbo"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "openpyxl>=3.1",
    "pydantic>=2.5",
    "pyyaml>=6.0",
    "python-dotenv>=1.0",
    "requests>=2.31",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-cov>=4.1"]

[project.scripts]
tiktok_qbo = "tiktok_qbo.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

- [ ] **Step 4: Write `.env.example`**

```
QBO_CLIENT_ID=
QBO_CLIENT_SECRET=
QBO_REFRESH_TOKEN=
QBO_REALM_ID=
QBO_ENV=sandbox
```

- [ ] **Step 5: Write `config/shops.yaml`**

```yaml
shops:
  PLELNU:
    display_name: "TikTok Shop PLELNU"
    customer_name: "TikTok Shop PLELNU Customer"
    clearing_account: "TikTok Clearing — PLELNU"
    reserve_account: "TikTok Reserve — PLELNU"
    bank_account_masked: "********9247"
```

- [ ] **Step 6: Write `config/accounts.yaml`**

```yaml
accounts:
  bank: "LAT Group Checking"
  sales_income: "Sales of Product Income"
  merchant_fees: "Merchant Fees — TikTok"
  shipping_expense: "Shipping Expense — TikTok"
  refunds_returns: "Refunds & Returns — TikTok"
  chargeback_fees: "Chargeback Fees — TikTok"
  reimbursements: "Reimbursements — TikTok"
  affiliate_commissions: "Affiliate Commissions — TikTok"
```

- [ ] **Step 7: Write `config/overrides.yaml`**

```yaml
excluded_statement_ids: []
excluded_payment_ids: []
```

- [ ] **Step 8: Write `src/tiktok_qbo/__init__.py`**

```python
__version__ = "0.1.0"
```

- [ ] **Step 9: Write `README.md`**

```markdown
# tiktok_qbo

Local pipeline for transforming TikTok LAT settlement .xlsx into QBO-ready JSON.

## Install
    pip install -e ".[dev]"

## Run (stages 1-3; QBO posting not in this plan)
    tiktok_qbo ingest    inputs/lat-PLELNU-2024Q2.xlsx
    tiktok_qbo plan      --hash <H>
    tiktok_qbo reconcile --hash <H>

Outputs land in `state/`.
```

- [ ] **Step 10: Install the package**

Run: `pip install -e ".[dev]"`
Expected: installs without error; `pytest --version` works.

- [ ] **Step 11: Write `tests/conftest.py` (empty placeholder — filled in later tasks)**

```python
# Fixtures added per-task.
```

- [ ] **Step 12: Commit**

```bash
git add .gitignore pyproject.toml .env.example README.md src/tiktok_qbo/__init__.py config/ tests/conftest.py
git commit -m "chore: project scaffold for tiktok_qbo"
```

---

## Task 2: money and dates helpers

**Files:**
- Create: `src/tiktok_qbo/money.py`, `src/tiktok_qbo/dates.py`
- Test: `tests/test_money.py`, `tests/test_dates.py`

- [ ] **Step 1: Write failing tests `tests/test_money.py`**

```python
from decimal import Decimal
from tiktok_qbo.money import to_money, money_sum, close_enough

def test_to_money_quantizes_to_two_places():
    assert to_money("1.235") == Decimal("1.24")       # banker's rounding
    assert to_money(1) == Decimal("1.00")
    assert to_money("") == Decimal("0.00")
    assert to_money(None) == Decimal("0.00")

def test_money_sum_preserves_two_places():
    assert money_sum([Decimal("1.005"), Decimal("2.005")]) == Decimal("3.01")

def test_close_enough_tolerates_one_cent():
    assert close_enough(Decimal("10.00"), Decimal("10.01"))
    assert not close_enough(Decimal("10.00"), Decimal("10.02"))
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_money.py -v`
Expected: `ModuleNotFoundError: No module named 'tiktok_qbo.money'`.

- [ ] **Step 3: Implement `src/tiktok_qbo/money.py`**

```python
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Iterable

TWOPLACES = Decimal("0.01")
TOLERANCE = Decimal("0.01")

def to_money(value) -> Decimal:
    if value in (None, ""):
        return Decimal("0.00")
    return Decimal(str(value)).quantize(TWOPLACES, rounding=ROUND_HALF_EVEN)

def money_sum(values: Iterable[Decimal]) -> Decimal:
    total = Decimal("0")
    for v in values:
        total += Decimal(str(v))
    return total.quantize(TWOPLACES, rounding=ROUND_HALF_EVEN)

def close_enough(a: Decimal, b: Decimal, tolerance: Decimal = TOLERANCE) -> bool:
    return abs(Decimal(str(a)) - Decimal(str(b))) <= tolerance
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_money.py -v`
Expected: 3 passed.

- [ ] **Step 5: Write failing tests `tests/test_dates.py`**

```python
from datetime import date
from tiktok_qbo.dates import parse_lat_date

def test_parse_lat_date_accepts_iso():
    assert parse_lat_date("2024-05-01") == date(2024, 5, 1)

def test_parse_lat_date_accepts_slashed():
    assert parse_lat_date("05/01/2024") == date(2024, 5, 1)

def test_parse_lat_date_accepts_datetime_obj():
    from datetime import datetime
    assert parse_lat_date(datetime(2024, 5, 1, 12, 0, 0)) == date(2024, 5, 1)

def test_parse_lat_date_returns_none_on_empty():
    assert parse_lat_date("") is None
    assert parse_lat_date(None) is None

def test_parse_lat_date_raises_on_garbage():
    import pytest
    with pytest.raises(ValueError):
        parse_lat_date("not a date")
```

- [ ] **Step 6: Run to verify fail**

Run: `pytest tests/test_dates.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 7: Implement `src/tiktok_qbo/dates.py`**

```python
from datetime import date, datetime

_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S")

def parse_lat_date(value) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    for fmt in _FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognized LAT date: {value!r}")
```

- [ ] **Step 8: Run both files**

Run: `pytest tests/test_money.py tests/test_dates.py -v`
Expected: 8 passed.

- [ ] **Step 9: Commit**

```bash
git add src/tiktok_qbo/money.py src/tiktok_qbo/dates.py tests/test_money.py tests/test_dates.py
git commit -m "feat: money and date helpers"
```

---

## Task 3: Data model (pydantic dataclasses)

**Files:**
- Create: `src/tiktok_qbo/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write failing test `tests/test_models.py`**

```python
from datetime import date
from decimal import Decimal
from tiktok_qbo.models import (
    NormalizedRow, StatementRow, PaymentRow, ReserveRow,
    InvoiceLine, InvoiceBatch,
    CreditMemoLine, CreditMemoBatch,
    JELine, StatementJE,
)

def test_normalized_row_json_roundtrip():
    r = NormalizedRow(
        shop_id="PLELNU", order_id="577xyz", sku_id="SKU1",
        statement_id="S1", payment_id="P1",
        statement_date=date(2024,5,1), order_created_date=date(2024,4,28),
        order_shipment_date=date(2024,4,29), order_delivery_date=date(2024,4,30),
        row_type="Order", classification="sale",
        customer_payment=Decimal("15.99"), customer_refund=Decimal("0.00"),
        gross_sales=Decimal("15.99"), quantity=1, product_name="Widget",
        raw={"Type": "Order"},
    )
    data = r.model_dump(mode="json")
    r2 = NormalizedRow.model_validate(data)
    assert r2 == r

def test_statement_je_requires_six_lines_allowed_to_be_empty_before_build():
    # Empty lines list is allowed at construction; invariant checked by builder.
    je = StatementJE(
        shop_id="PLELNU", payment_id="P1", doc_number="JE-PLELNU-000000000001",
        txn_date=date(2024,5,1), lines=[], statement_ids=["S1"],
        bank_amount=Decimal("100.00"),
    )
    assert je.payment_id == "P1"

def test_invoice_batch_total_is_sum_of_lines():
    batch = InvoiceBatch(
        shop_id="PLELNU", delivery_date=date(2024,4,30),
        doc_number="INV-PLELNU-20240430",
        lines=[
            InvoiceLine(sku_id="A", product_name="A", quantity=1,
                        unit_price=Decimal("10.00"), amount=Decimal("10.00")),
            InvoiceLine(sku_id="B", product_name="B", quantity=2,
                        unit_price=Decimal("5.00"), amount=Decimal("10.00")),
        ],
        total_amount=Decimal("20.00"),
        source_order_ids=["o1","o2"],
    )
    assert batch.total_amount == sum(l.amount for l in batch.lines)
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_models.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `src/tiktok_qbo/models.py`**

```python
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
    reserve_date: date
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
```

- [ ] **Step 4: Run**

Run: `pytest tests/test_models.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/tiktok_qbo/models.py tests/test_models.py
git commit -m "feat: dataclass model layer"
```

---

## Task 4: Classifier (6-way decision tree)

**Files:**
- Create: `src/tiktok_qbo/ingest/__init__.py`, `src/tiktok_qbo/ingest/classify.py`
- Test: `tests/test_classify.py`

- [ ] **Step 1: Write failing test `tests/test_classify.py`**

```python
from decimal import Decimal
from tiktok_qbo.ingest.classify import classify

def mk(row_type="Order", gross_sales="0", gross_sales_refund="0",
       customer_payment="0", quantity=1):
    return {
        "Type": row_type,
        "Gross sales": Decimal(gross_sales),
        "Gross sales refund": Decimal(gross_sales_refund),
        "Customer payment": Decimal(customer_payment),
        "Quantity": quantity,
    }

def test_sale_positive_gross():
    assert classify(mk(gross_sales="15.99", customer_payment="15.99")) == "sale"

def test_refund_negative_gross_refund():
    assert classify(mk(gross_sales_refund="-15.99")) == "refund"

def test_chargeback_row_type():
    assert classify(mk(row_type="Chargeback")) == "chargeback"

def test_tiktok_reimbursement():
    assert classify(mk(row_type="TikTok Shop reimbursement")) == "reimbursement"

def test_logistics_reimbursement():
    assert classify(mk(row_type="Logistics reimbursement")) == "reimbursement"

def test_fee_only_zero_everything():
    assert classify(mk(gross_sales="0", customer_payment="0")) == "fee_only"

def test_zero_quantity_sale_becomes_adjustment():
    # E9: Quantity=0 disqualifies a sale classification.
    assert classify(mk(gross_sales="15.99", customer_payment="15.99", quantity=0)) == "adjustment"

def test_unknown_row_type_raises():
    import pytest
    with pytest.raises(ValueError):
        classify(mk(row_type="SomethingNew"))
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_classify.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Create `src/tiktok_qbo/ingest/__init__.py`** (empty file)

```python
```

- [ ] **Step 4: Implement `src/tiktok_qbo/ingest/classify.py`**

```python
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
```

- [ ] **Step 5: Run**

Run: `pytest tests/test_classify.py -v`
Expected: 8 passed.

- [ ] **Step 6: Commit**

```bash
git add src/tiktok_qbo/ingest/ tests/test_classify.py
git commit -m "feat: LAT row classifier"
```

---

## Task 5: LAT xlsx readers (Order details + Statements + Payments + Reserve)

**Files:**
- Create: `src/tiktok_qbo/ingest/lat_xlsx.py`, `tests/conftest.py` (append fixtures), `tests/fixtures/build_tiny.py`
- Test: `tests/test_ingest_lat_xlsx.py`

- [ ] **Step 1: Append fixture factory to `tests/conftest.py`**

```python
from decimal import Decimal
from datetime import date
from pathlib import Path
import pytest
from openpyxl import Workbook

@pytest.fixture
def tiny_xlsx(tmp_path: Path) -> Path:
    """Build a minimal LAT xlsx covering sale + refund + chargeback + fee_only +
    one Statement + one Payment + one Reserve row."""
    wb = Workbook()

    od = wb.active
    od.title = "Order details"
    od.append([
        "Statement ID", "Payment ID", "Statement date", "Shop ID", "Order ID",
        "SKU ID", "Product name", "Quantity", "Type",
        "Order created date", "Order shipment date", "Order delivery date",
        "Gross sales", "Gross sales refund", "Customer payment", "Customer refund",
    ])
    # sale
    od.append(["S1","P1","2024-05-12","PLELNU","O1","SKU-A","Widget",1,"Order",
               "2024-04-28","2024-04-29","2024-04-30","15.99","0","15.99","0"])
    # refund
    od.append(["S1","P1","2024-05-12","PLELNU","O2","SKU-B","Gadget",1,"Order",
               "2024-04-10","2024-04-11","2024-04-12","0","-9.99","0","9.99"])
    # chargeback
    od.append(["S1","P1","2024-05-12","PLELNU","O3","SKU-C","Thing",1,"Chargeback",
               "2024-03-20","2024-03-21","2024-03-22","0","0","0","0"])
    # fee-only (platform fee row)
    od.append(["S1","P1","2024-05-12","PLELNU","O4","SKU-A","Widget",1,"Order",
               "2024-04-28","2024-04-29","2024-04-30","0","0","0","0"])

    s = wb.create_sheet("Statements")
    s.append(["Shop ID","Statement ID","Payment ID","Statement date","Status",
              "Total settlement amount","Net sales","Shipping","Fees",
              "Adjustments","Reserve amount","Payable amount"])
    # payable = 15.99 - 9.99 + 0 (fees) + 0 (shipping) + 0 (adj) - 0 (reserve) = 6.00
    s.append(["PLELNU","S1","P1","2024-05-12","Paid",
              "6.00","6.00","0","0","0","0","6.00"])

    p = wb.create_sheet("Payments")
    p.append(["Shop ID","Payment ID","Payment amount","Payment initiation date",
              "Payment completion date","Bank account","Status"])
    p.append(["PLELNU","P1","6.00","2024-05-12","2024-05-13","********9247","Completed"])

    r = wb.create_sheet("Reserve details")
    r.append(["Shop ID","Statement ID","Reserve ID","Reserve amount",
              "Reserve date","Release date","Status"])
    r.append(["PLELNU","S1","R1","0","2024-05-12","","Released"])

    path = tmp_path / "tiny.xlsx"
    wb.save(path)
    return path
```

- [ ] **Step 2: Write failing test `tests/test_ingest_lat_xlsx.py`**

```python
from decimal import Decimal
from datetime import date
from tiktok_qbo.ingest.lat_xlsx import (
    read_order_details, read_statements, read_payments, read_reserves,
)

def test_read_order_details_produces_four_normalized_rows(tiny_xlsx):
    rows = read_order_details(tiny_xlsx, shop_id="PLELNU")
    assert len(rows) == 4
    classes = [r.classification for r in rows]
    assert classes == ["sale", "refund", "chargeback", "fee_only"]
    sale = rows[0]
    assert sale.order_id == "O1"
    assert sale.customer_payment == Decimal("15.99")
    assert sale.order_delivery_date == date(2024, 4, 30)

def test_read_statements(tiny_xlsx):
    stmts = read_statements(tiny_xlsx, shop_id="PLELNU")
    assert len(stmts) == 1
    assert stmts[0].statement_id == "S1"
    assert stmts[0].payable_amount == Decimal("6.00")

def test_read_payments(tiny_xlsx):
    pays = read_payments(tiny_xlsx, shop_id="PLELNU")
    assert len(pays) == 1
    assert pays[0].payment_id == "P1"
    assert pays[0].payment_completion_date == date(2024, 5, 13)

def test_read_reserves(tiny_xlsx):
    res = read_reserves(tiny_xlsx, shop_id="PLELNU")
    assert len(res) == 1
    assert res[0].reserve_id == "R1"

def test_shop_mismatch_raises(tiny_xlsx):
    import pytest
    with pytest.raises(ValueError, match="shop"):
        read_order_details(tiny_xlsx, shop_id="WRONG")
```

- [ ] **Step 3: Run to verify fail**

Run: `pytest tests/test_ingest_lat_xlsx.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 4: Implement `src/tiktok_qbo/ingest/lat_xlsx.py`**

```python
from pathlib import Path
from decimal import Decimal
from openpyxl import load_workbook
from tiktok_qbo.models import (
    NormalizedRow, StatementRow, PaymentRow, ReserveRow,
)
from tiktok_qbo.ingest.classify import classify
from tiktok_qbo.dates import parse_lat_date
from tiktok_qbo.money import to_money


def _sheet_rows(path: Path, sheet_name: str):
    wb = load_workbook(path, read_only=True, data_only=True)
    if sheet_name not in wb.sheetnames:
        raise ValueError(f"Missing sheet: {sheet_name}")
    ws = wb[sheet_name]
    iter_rows = ws.iter_rows(values_only=True)
    header = next(iter_rows)
    for row in iter_rows:
        if row is None or all(v is None for v in row):
            continue
        yield dict(zip(header, row))
    wb.close()


def _require_shop(raw: dict, expected: str, path: Path):
    got = str(raw.get("Shop ID", "")).strip()
    if got and got != expected:
        raise ValueError(
            f"shop mismatch in {path.name}: expected {expected!r}, got {got!r}"
        )


def read_order_details(path: Path, shop_id: str) -> list[NormalizedRow]:
    out: list[NormalizedRow] = []
    for raw in _sheet_rows(Path(path), "Order details"):
        _require_shop(raw, shop_id, Path(path))
        classification = classify(raw)
        out.append(NormalizedRow(
            shop_id=shop_id,
            order_id=str(raw.get("Order ID", "") or ""),
            sku_id=str(raw.get("SKU ID", "") or ""),
            statement_id=str(raw.get("Statement ID", "") or ""),
            payment_id=str(raw.get("Payment ID", "") or ""),
            statement_date=parse_lat_date(raw.get("Statement date")),
            order_created_date=parse_lat_date(raw.get("Order created date")),
            order_shipment_date=parse_lat_date(raw.get("Order shipment date")),
            order_delivery_date=parse_lat_date(raw.get("Order delivery date")),
            row_type=str(raw.get("Type", "") or ""),
            classification=classification,
            customer_payment=to_money(raw.get("Customer payment")),
            customer_refund=to_money(raw.get("Customer refund")),
            gross_sales=to_money(raw.get("Gross sales")),
            quantity=int(raw.get("Quantity") or 0),
            product_name=str(raw.get("Product name", "") or ""),
            raw={k: (str(v) if v is not None else "") for k, v in raw.items()},
        ))
    return out


def read_statements(path: Path, shop_id: str) -> list[StatementRow]:
    out: list[StatementRow] = []
    for raw in _sheet_rows(Path(path), "Statements"):
        _require_shop(raw, shop_id, Path(path))
        out.append(StatementRow(
            shop_id=shop_id,
            statement_id=str(raw.get("Statement ID", "") or ""),
            payment_id=str(raw.get("Payment ID", "") or ""),
            statement_date=parse_lat_date(raw.get("Statement date")),
            status=str(raw.get("Status", "") or ""),
            total_settlement_amount=to_money(raw.get("Total settlement amount")),
            net_sales=to_money(raw.get("Net sales")),
            shipping=to_money(raw.get("Shipping")),
            fees=to_money(raw.get("Fees")),
            adjustments=to_money(raw.get("Adjustments")),
            reserve_amount=to_money(raw.get("Reserve amount")),
            payable_amount=to_money(raw.get("Payable amount")),
        ))
    return out


def read_payments(path: Path, shop_id: str) -> list[PaymentRow]:
    out: list[PaymentRow] = []
    for raw in _sheet_rows(Path(path), "Payments"):
        _require_shop(raw, shop_id, Path(path))
        out.append(PaymentRow(
            shop_id=shop_id,
            payment_id=str(raw.get("Payment ID", "") or ""),
            payment_amount=to_money(raw.get("Payment amount")),
            payment_initiation_date=parse_lat_date(raw.get("Payment initiation date")),
            payment_completion_date=parse_lat_date(raw.get("Payment completion date")),
            bank_account_masked=str(raw.get("Bank account", "") or ""),
            status=str(raw.get("Status", "") or ""),
        ))
    return out


def read_reserves(path: Path, shop_id: str) -> list[ReserveRow]:
    out: list[ReserveRow] = []
    for raw in _sheet_rows(Path(path), "Reserve details"):
        _require_shop(raw, shop_id, Path(path))
        out.append(ReserveRow(
            shop_id=shop_id,
            statement_id=str(raw.get("Statement ID", "") or ""),
            reserve_id=str(raw.get("Reserve ID", "") or ""),
            reserve_amount=to_money(raw.get("Reserve amount")),
            reserve_date=parse_lat_date(raw.get("Reserve date")),
            release_date=parse_lat_date(raw.get("Release date")),
            status=str(raw.get("Status", "") or ""),
        ))
    return out
```

- [ ] **Step 5: Run**

Run: `pytest tests/test_ingest_lat_xlsx.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add src/tiktok_qbo/ingest/lat_xlsx.py tests/test_ingest_lat_xlsx.py tests/conftest.py
git commit -m "feat: LAT xlsx readers for 4 sheets"
```

---

## Task 6: Ingest pipeline orchestration + hash

**Files:**
- Create: `src/tiktok_qbo/ingest/pipeline.py`
- Test: `tests/test_ingest_pipeline.py`

- [ ] **Step 1: Write failing test `tests/test_ingest_pipeline.py`**

```python
import json
from tiktok_qbo.ingest.pipeline import run_ingest

def test_run_ingest_writes_four_jsonl_artifacts(tiny_xlsx, tmp_path):
    state_dir = tmp_path / "state"
    result = run_ingest(tiny_xlsx, shop_id="PLELNU", state_dir=state_dir)
    h = result.hash
    assert (state_dir / f"rows-{h}.jsonl").exists()
    assert (state_dir / f"statements-{h}.jsonl").exists()
    assert (state_dir / f"payments-{h}.jsonl").exists()
    assert (state_dir / f"reserves-{h}.jsonl").exists()

    with (state_dir / f"rows-{h}.jsonl").open() as f:
        rows = [json.loads(line) for line in f]
    assert len(rows) == 4
    assert rows[0]["classification"] == "sale"

def test_run_ingest_hash_is_stable(tiny_xlsx, tmp_path):
    a = run_ingest(tiny_xlsx, shop_id="PLELNU", state_dir=tmp_path / "a")
    b = run_ingest(tiny_xlsx, shop_id="PLELNU", state_dir=tmp_path / "b")
    assert a.hash == b.hash
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_ingest_pipeline.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `src/tiktok_qbo/ingest/pipeline.py`**

```python
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from tiktok_qbo.ingest.lat_xlsx import (
    read_order_details, read_statements, read_payments, read_reserves,
)


@dataclass
class IngestResult:
    hash: str
    n_rows: int
    n_statements: int
    n_payments: int
    n_reserves: int


def _content_hash(path: Path) -> str:
    h = hashlib.sha256()
    h.update(Path(path).read_bytes())
    return h.hexdigest()[:16]


def _write_jsonl(path: Path, items) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(item.model_dump_json() + "\n")


def run_ingest(xlsx_path, shop_id: str, state_dir: Path) -> IngestResult:
    xlsx_path = Path(xlsx_path)
    state_dir = Path(state_dir)
    h = _content_hash(xlsx_path)

    rows = read_order_details(xlsx_path, shop_id=shop_id)
    stmts = read_statements(xlsx_path, shop_id=shop_id)
    pays = read_payments(xlsx_path, shop_id=shop_id)
    reserves = read_reserves(xlsx_path, shop_id=shop_id)

    _write_jsonl(state_dir / f"rows-{h}.jsonl", rows)
    _write_jsonl(state_dir / f"statements-{h}.jsonl", stmts)
    _write_jsonl(state_dir / f"payments-{h}.jsonl", pays)
    _write_jsonl(state_dir / f"reserves-{h}.jsonl", reserves)

    return IngestResult(
        hash=h, n_rows=len(rows), n_statements=len(stmts),
        n_payments=len(pays), n_reserves=len(reserves),
    )
```

- [ ] **Step 4: Run**

Run: `pytest tests/test_ingest_pipeline.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/tiktok_qbo/ingest/pipeline.py tests/test_ingest_pipeline.py
git commit -m "feat: ingest orchestration with content-hash artifacts"
```

---

## Task 7: Invoice grouping (sales → InvoiceBatch per delivery_date)

**Files:**
- Create: `src/tiktok_qbo/plan/__init__.py`, `src/tiktok_qbo/plan/invoices.py`
- Test: `tests/test_plan_invoices.py`

- [ ] **Step 1: Write failing test `tests/test_plan_invoices.py`**

```python
from datetime import date
from decimal import Decimal
from tiktok_qbo.models import NormalizedRow
from tiktok_qbo.plan.invoices import group_invoices

def mk_sale(order_id, sku, qty, amount, delivery):
    return NormalizedRow(
        shop_id="PLELNU", order_id=order_id, sku_id=sku,
        statement_id="S1", payment_id="P1",
        statement_date=date(2024,5,12),
        order_created_date=date(2024,4,28),
        order_shipment_date=date(2024,4,29),
        order_delivery_date=delivery,
        row_type="Order", classification="sale",
        customer_payment=Decimal(amount), customer_refund=Decimal("0"),
        gross_sales=Decimal(amount), quantity=qty, product_name=f"P-{sku}",
        raw={},
    )

def test_groups_sales_by_delivery_date():
    rows = [
        mk_sale("O1","A",1,"10.00", date(2024,4,30)),
        mk_sale("O2","B",2,"20.00", date(2024,4,30)),
        mk_sale("O3","A",1,"15.00", date(2024,5,1)),
    ]
    batches = group_invoices(rows, shop_id="PLELNU")
    assert len(batches) == 2
    b1, b2 = batches
    assert b1.delivery_date == date(2024,4,30)
    assert b1.total_amount == Decimal("30.00")
    assert b1.doc_number == "INV-PLELNU-20240430"
    assert sorted(b1.source_order_ids) == ["O1","O2"]
    assert b2.delivery_date == date(2024,5,1)
    assert b2.total_amount == Decimal("15.00")

def test_rows_without_delivery_date_are_deferred(tmp_path):
    rows = [
        mk_sale("O1","A",1,"10.00", date(2024,4,30)),
        mk_sale("ONoDate","A",1,"99.00", None),
    ]
    batches = group_invoices(rows, shop_id="PLELNU")
    assert len(batches) == 1
    assert "ONoDate" not in batches[0].source_order_ids

def test_only_sale_classification_included():
    rows = [
        mk_sale("O1","A",1,"10.00", date(2024,4,30)),
    ]
    rows[0].classification = "adjustment"
    assert group_invoices(rows, shop_id="PLELNU") == []
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_plan_invoices.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Create `src/tiktok_qbo/plan/__init__.py`** (empty file)

```python
```

- [ ] **Step 4: Implement `src/tiktok_qbo/plan/invoices.py`**

```python
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
```

- [ ] **Step 5: Run**

Run: `pytest tests/test_plan_invoices.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add src/tiktok_qbo/plan/__init__.py src/tiktok_qbo/plan/invoices.py tests/test_plan_invoices.py
git commit -m "feat: invoice grouping by delivery date"
```

---

## Task 8: Credit memo grouping (refund/chargeback → CreditMemoBatch per statement_date)

**Files:**
- Create: `src/tiktok_qbo/plan/credit_memos.py`
- Test: `tests/test_plan_credit_memos.py`

- [ ] **Step 1: Write failing test `tests/test_plan_credit_memos.py`**

```python
from datetime import date
from decimal import Decimal
from tiktok_qbo.models import NormalizedRow
from tiktok_qbo.plan.credit_memos import group_credit_memos

def mk(order_id, classification, refund, stmt_date, sku="A"):
    return NormalizedRow(
        shop_id="PLELNU", order_id=order_id, sku_id=sku,
        statement_id="S1", payment_id="P1",
        statement_date=stmt_date,
        order_created_date=date(2024,4,1),
        order_shipment_date=date(2024,4,2),
        order_delivery_date=date(2024,4,3),
        row_type="Order", classification=classification,
        customer_payment=Decimal("0"), customer_refund=Decimal(refund),
        gross_sales=Decimal("0"), quantity=1, product_name=f"P-{sku}",
        raw={},
    )

def test_groups_refunds_and_chargebacks_by_statement_date():
    rows = [
        mk("O1", "refund", "9.99", date(2024,5,12)),
        mk("O2", "chargeback", "25.00", date(2024,5,12)),
        mk("O3", "refund", "5.00", date(2024,5,19)),
    ]
    batches = group_credit_memos(rows, shop_id="PLELNU")
    assert len(batches) == 2
    first = batches[0]
    assert first.statement_date == date(2024,5,12)
    assert first.doc_number == "CM-PLELNU-20240512"
    assert first.total_amount == Decimal("34.99")
    assert sorted(first.source_order_ids) == ["O1","O2"]

def test_refund_amount_is_positive_on_the_line():
    rows = [mk("O1","refund","9.99", date(2024,5,12))]
    batches = group_credit_memos(rows, shop_id="PLELNU")
    assert batches[0].lines[0].amount == Decimal("9.99")
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_plan_credit_memos.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `src/tiktok_qbo/plan/credit_memos.py`**

```python
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
```

- [ ] **Step 4: Run**

Run: `pytest tests/test_plan_credit_memos.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/tiktok_qbo/plan/credit_memos.py tests/test_plan_credit_memos.py
git commit -m "feat: credit memo grouping by statement date"
```

---

## Task 9: Statement JE builder (6-leg per Payment ID)

**Files:**
- Create: `src/tiktok_qbo/plan/statement_je.py`
- Test: `tests/test_plan_statement_je.py`

- [ ] **Step 1: Write failing test `tests/test_plan_statement_je.py`**

```python
from datetime import date
from decimal import Decimal
from tiktok_qbo.models import StatementRow, PaymentRow
from tiktok_qbo.plan.statement_je import build_statement_jes

def mk_stmt(stmt_id, payment_id, net, shipping, fees, adj, reserve, payable):
    return StatementRow(
        shop_id="PLELNU", statement_id=stmt_id, payment_id=payment_id,
        statement_date=date(2024,5,12), status="Paid",
        total_settlement_amount=Decimal(payable),
        net_sales=Decimal(net), shipping=Decimal(shipping),
        fees=Decimal(fees), adjustments=Decimal(adj),
        reserve_amount=Decimal(reserve), payable_amount=Decimal(payable),
    )

def mk_payment(payment_id, amount):
    return PaymentRow(
        shop_id="PLELNU", payment_id=payment_id,
        payment_amount=Decimal(amount),
        payment_initiation_date=date(2024,5,12),
        payment_completion_date=date(2024,5,13),
        bank_account_masked="********9247", status="Completed",
    )

def test_single_payment_single_statement_six_legs():
    stmts = [mk_stmt("S1","P1","100","10","-15","0","5","90")]
    pays  = [mk_payment("P1","90")]
    jes = build_statement_jes(stmts, pays, shop_id="PLELNU")
    assert len(jes) == 1
    je = jes[0]
    assert je.doc_number == "JE-PLELNU-P1"[: len("JE-PLELNU-") ] + "P1"  # see impl
    assert je.txn_date == date(2024,5,13)
    assert je.bank_amount == Decimal("90.00")

    by_role = {l.account_role: l for l in je.lines}
    assert by_role["bank"].side == "DR" and by_role["bank"].amount == Decimal("90.00")
    assert by_role["reserve"].side == "DR" and by_role["reserve"].amount == Decimal("5.00")
    assert by_role["fees"].side == "DR" and by_role["fees"].amount == Decimal("15.00")
    assert by_role["shipping"].side == "DR" and by_role["shipping"].amount == Decimal("10.00")
    assert by_role["adjustments"].side == "DR" and by_role["adjustments"].amount == Decimal("0.00")
    assert by_role["clearing"].side == "CR" and by_role["clearing"].amount == Decimal("120.00")

    debits = sum(l.amount for l in je.lines if l.side == "DR")
    credits = sum(l.amount for l in je.lines if l.side == "CR")
    assert debits == credits

def test_shipping_and_adjustments_flip_side_when_negative():
    # Shipping paid back to seller (positive inflow) reduces the JE's DR side.
    stmts = [mk_stmt("S1","P1","100","-7","-15","-3","0","75")]
    pays  = [mk_payment("P1","75")]
    je = build_statement_jes(stmts, pays, shop_id="PLELNU")[0]
    by_role = {l.account_role: l for l in je.lines}
    assert by_role["shipping"].side == "CR" and by_role["shipping"].amount == Decimal("7.00")
    assert by_role["adjustments"].side == "CR" and by_role["adjustments"].amount == Decimal("3.00")
    debits = sum(l.amount for l in je.lines if l.side == "DR")
    credits = sum(l.amount for l in je.lines if l.side == "CR")
    assert debits == credits

def test_multiple_statements_per_payment_roll_up():
    stmts = [
        mk_stmt("S1","P1","50","5","-7","0","2","46"),
        mk_stmt("S2","P1","30","0","-3","0","1","26"),
    ]
    pays = [mk_payment("P1","72")]
    je = build_statement_jes(stmts, pays, shop_id="PLELNU")[0]
    by_role = {l.account_role: l for l in je.lines}
    assert by_role["bank"].amount == Decimal("72.00")
    assert by_role["fees"].amount == Decimal("10.00")
    assert by_role["reserve"].amount == Decimal("3.00")
    assert by_role["clearing"].amount == Decimal("72.00")   # 46+26
    assert sorted(je.statement_ids) == ["S1","S2"]

def test_pending_statement_without_payment_is_skipped():
    stmts = [mk_stmt("S1","", "100","0","-5","0","0","95")]  # payment_id blank
    pays  = []
    assert build_statement_jes(stmts, pays, shop_id="PLELNU") == []
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_plan_statement_je.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `src/tiktok_qbo/plan/statement_je.py`**

```python
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

        bank = payment.payment_amount
        reserve = money_sum(s.reserve_amount for s in group)
        fees = to_money(abs(money_sum(s.fees for s in group)))
        shipping = money_sum(s.shipping for s in group)
        adjustments = money_sum(s.adjustments for s in group)
        clearing_credit = money_sum(s.payable_amount for s in group)

        lines = [
            JELine(account_role="bank", side="DR", amount=to_money(bank)),
            JELine(account_role="reserve", side="DR", amount=to_money(reserve)),
            JELine(account_role="fees", side="DR", amount=fees),
            _leg("shipping", shipping),
            _leg("adjustments", adjustments),
            JELine(account_role="clearing", side="CR", amount=to_money(clearing_credit)),
        ]

        jes.append(StatementJE(
            shop_id=shop_id,
            payment_id=payment_id,
            doc_number=_doc_number(shop_id, payment_id),
            txn_date=payment.payment_completion_date,
            lines=lines,
            statement_ids=sorted(s.statement_id for s in group),
            bank_amount=to_money(bank),
        ))
    return jes
```

- [ ] **Step 4: Run**

Run: `pytest tests/test_plan_statement_je.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/tiktok_qbo/plan/statement_je.py tests/test_plan_statement_je.py
git commit -m "feat: six-leg statement journal entry builder"
```

---

## Task 10: Plan pipeline orchestration

**Files:**
- Create: `src/tiktok_qbo/plan/pipeline.py`
- Test: `tests/test_plan_pipeline.py`

- [ ] **Step 1: Write failing test `tests/test_plan_pipeline.py`**

```python
import json
from tiktok_qbo.ingest.pipeline import run_ingest
from tiktok_qbo.plan.pipeline import run_plan

def test_run_plan_writes_three_entity_dirs(tiny_xlsx, tmp_path):
    state_dir = tmp_path / "state"
    ingest = run_ingest(tiny_xlsx, shop_id="PLELNU", state_dir=state_dir)
    plan_dir = run_plan(hash=ingest.hash, shop_id="PLELNU", state_dir=state_dir)

    inv_files  = list((plan_dir / "invoices").glob("*.json"))
    cm_files   = list((plan_dir / "credit_memos").glob("*.json"))
    je_files   = list((plan_dir / "journal_entries").glob("*.json"))

    assert len(inv_files) == 1          # one sale on 2024-04-30
    assert len(cm_files) == 1           # refund + chargeback both on 2024-05-12
    assert len(je_files) == 1           # one payment P1

    inv = json.loads(inv_files[0].read_text(encoding="utf-8"))
    assert inv["doc_number"] == "INV-PLELNU-20240430"
    assert inv["total_amount"] == "15.99"
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_plan_pipeline.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `src/tiktok_qbo/plan/pipeline.py`**

```python
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
```

- [ ] **Step 4: Run**

Run: `pytest tests/test_plan_pipeline.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/tiktok_qbo/plan/pipeline.py tests/test_plan_pipeline.py
git commit -m "feat: plan orchestration writes per-entity JSON"
```

---

## Task 11: Reconcile — Identity 2 (per Statement)

**Files:**
- Create: `src/tiktok_qbo/reconcile.py` (initial version)
- Test: `tests/test_reconcile.py` (Identity 2 cases only)

- [ ] **Step 1: Write failing test `tests/test_reconcile.py`**

```python
from datetime import date
from decimal import Decimal
from tiktok_qbo.models import StatementRow
from tiktok_qbo.reconcile import check_identity_2, Mismatch

def mk(stmt_id, net, shipping, fees, adj, reserve, payable):
    return StatementRow(
        shop_id="PLELNU", statement_id=stmt_id, payment_id="P1",
        statement_date=date(2024,5,12), status="Paid",
        total_settlement_amount=Decimal(payable),
        net_sales=Decimal(net), shipping=Decimal(shipping),
        fees=Decimal(fees), adjustments=Decimal(adj),
        reserve_amount=Decimal(reserve), payable_amount=Decimal(payable),
    )

def test_identity_2_passes_when_payable_matches_computed():
    # 100 + 10 + (-15) + 0 - 5 = 90
    stmts = [mk("S1","100","10","-15","0","5","90")]
    assert check_identity_2(stmts) == []

def test_identity_2_flags_off_by_more_than_one_cent():
    # computed = 90, payable reported = 91.00 → diff 1.00
    stmts = [mk("S1","100","10","-15","0","5","91.00")]
    diffs = check_identity_2(stmts)
    assert len(diffs) == 1
    assert isinstance(diffs[0], Mismatch)
    assert diffs[0].statement_id == "S1"
    assert diffs[0].diff == Decimal("1.00")

def test_identity_2_tolerates_penny():
    stmts = [mk("S1","100","10","-15","0","5","90.01")]
    assert check_identity_2(stmts) == []
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_reconcile.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `src/tiktok_qbo/reconcile.py` (Identity 2 only for now)**

```python
from dataclasses import dataclass
from decimal import Decimal
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
```

- [ ] **Step 4: Run**

Run: `pytest tests/test_reconcile.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/tiktok_qbo/reconcile.py tests/test_reconcile.py
git commit -m "feat: reconcile identity 2 (per statement)"
```

---

## Task 12: Reconcile — Identity 1 (per Payment) + diagnostic CSV

**Files:**
- Modify: `src/tiktok_qbo/reconcile.py` (add `check_identity_1`, `run_reconcile`, CSV writer)
- Modify: `tests/test_reconcile.py` (append Identity 1 tests)

- [ ] **Step 1: Append failing tests to `tests/test_reconcile.py`**

```python
from pathlib import Path
from tiktok_qbo.reconcile import check_identity_1, run_reconcile
from tiktok_qbo.models import PaymentRow

def mk_pay(pid, amount):
    return PaymentRow(
        shop_id="PLELNU", payment_id=pid, payment_amount=Decimal(amount),
        payment_initiation_date=date(2024,5,12),
        payment_completion_date=date(2024,5,13),
        bank_account_masked="********9247", status="Completed",
    )

def test_identity_1_passes_when_sum_payable_equals_payment():
    stmts = [
        mk("S1","50","5","-7","0","2","46"),   # payable 46 (50+5-7+0-2)
        mk("S2","30","0","-3","0","1","26"),   # payable 26 (30+0-3+0-1)
    ]
    for s in stmts: s.payment_id = "P1"
    pays = [mk_pay("P1","72")]
    assert check_identity_1(stmts, pays) == []

def test_identity_1_flags_payment_mismatch():
    stmts = [mk("S1","50","5","-7","0","2","46")]
    stmts[0].payment_id = "P1"
    pays = [mk_pay("P1","50")]       # bank says 50, statements sum to 46 → diff 4
    diffs = check_identity_1(stmts, pays)
    assert len(diffs) == 1
    assert diffs[0].payment_id == "P1"
    assert diffs[0].diff == Decimal("4.00")

def test_run_reconcile_writes_pass_marker_and_csv(tmp_path):
    stmts = [mk("S1","50","5","-7","0","2","46")]
    stmts[0].payment_id = "P1"
    pays = [mk_pay("P1","46")]
    result = run_reconcile(stmts, pays, hash="abc", state_dir=tmp_path)
    assert result.passed
    assert (tmp_path / "reconcile-abc.pass").exists()
    assert (tmp_path / "reconcile-abc.csv").exists()

def test_run_reconcile_writes_fail_marker_on_mismatch(tmp_path):
    stmts = [mk("S1","50","5","-7","0","2","99")]   # identity_2 fails
    stmts[0].payment_id = "P1"
    pays = [mk_pay("P1","99")]
    result = run_reconcile(stmts, pays, hash="abc", state_dir=tmp_path)
    assert not result.passed
    assert (tmp_path / "reconcile-abc.fail").exists()
    body = (tmp_path / "reconcile-abc.csv").read_text(encoding="utf-8")
    assert "S1" in body
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_reconcile.py -v`
Expected: 4 new tests fail with `ImportError` / `AttributeError`.

- [ ] **Step 3: Extend `src/tiktok_qbo/reconcile.py`**

Append to the existing file:

```python
import csv
from collections import defaultdict
from pathlib import Path


@dataclass
class ReconcileResult:
    passed: bool
    mismatches: list[Mismatch]


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
```

- [ ] **Step 4: Run all reconcile tests**

Run: `pytest tests/test_reconcile.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/tiktok_qbo/reconcile.py tests/test_reconcile.py
git commit -m "feat: identity 1 + reconcile runner with diagnostic CSV"
```

---

## Task 13: CLI subcommands (ingest / plan / reconcile)

**Files:**
- Create: `src/tiktok_qbo/cli.py`, `src/tiktok_qbo/config.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing test `tests/test_cli.py`**

```python
import subprocess
import sys
import json
from pathlib import Path
from tiktok_qbo.ingest.pipeline import run_ingest

def _run(args, cwd):
    return subprocess.run(
        [sys.executable, "-m", "tiktok_qbo.cli", *args],
        cwd=cwd, capture_output=True, text=True,
    )

def test_cli_ingest_prints_hash(tiny_xlsx, tmp_path):
    r = _run(["ingest", str(tiny_xlsx), "--shop", "PLELNU",
              "--state-dir", str(tmp_path / "state")], cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "hash=" in r.stdout

def test_cli_plan_uses_prior_ingest(tiny_xlsx, tmp_path):
    state = tmp_path / "state"
    ingest = run_ingest(tiny_xlsx, shop_id="PLELNU", state_dir=state)
    r = _run(["plan", "--hash", ingest.hash, "--shop", "PLELNU",
              "--state-dir", str(state)], cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert (state / f"plan-{ingest.hash}" / "invoices").exists()

def test_cli_reconcile_reports_pass(tiny_xlsx, tmp_path):
    state = tmp_path / "state"
    ingest = run_ingest(tiny_xlsx, shop_id="PLELNU", state_dir=state)
    r = _run(["reconcile", "--hash", ingest.hash,
              "--state-dir", str(state)], cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "PASS" in r.stdout
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_cli.py -v`
Expected: fails — `python -m tiktok_qbo.cli` not yet runnable.

- [ ] **Step 3: Implement `src/tiktok_qbo/config.py`**

```python
from pathlib import Path
import yaml

def load_shops(path: Path | str = "config/shops.yaml") -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))["shops"]
```

- [ ] **Step 4: Implement `src/tiktok_qbo/cli.py`**

```python
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
```

- [ ] **Step 5: Run**

Run: `pytest tests/test_cli.py -v`
Expected: 3 passed.

- [ ] **Step 6: Run the entire test suite**

Run: `pytest -v`
Expected: all tests green (≈ 30 tests).

- [ ] **Step 7: Commit**

```bash
git add src/tiktok_qbo/cli.py src/tiktok_qbo/config.py tests/test_cli.py
git commit -m "feat: CLI subcommands ingest/plan/reconcile"
```

---

## Task 14: Real-data smoke test on Q2 2024 LAT

**Files:**
- Create: `scripts/smoke_q2_2024.py`
- Modify: `README.md` (add smoke-test section)

- [ ] **Step 1: Write `scripts/smoke_q2_2024.py`**

```python
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
```

- [ ] **Step 2: Run the smoke test**

Run: `python scripts/smoke_q2_2024.py`
Expected: prints counts; ideally reconcile PASS. If FAIL, inspect `state/reconcile-<hash>.csv` and decide: bug, LAT anomaly, or an override needed. Record findings in a commit message or an `issues/` note — **do not** silently tweak identities to make them pass.

- [ ] **Step 3: Append smoke-test section to `README.md`**

```markdown

## Smoke test on real data

    python scripts/smoke_q2_2024.py

Reads `~/Downloads/4-6-2024.xlsx`, runs ingest → plan → reconcile, and
prints per-stage counts plus any reconciliation mismatches.
```

- [ ] **Step 4: Commit**

```bash
git add scripts/smoke_q2_2024.py README.md
git commit -m "test: manual smoke-test script for Q2 2024 LAT"
```

---

## Self-Review (run before handoff)

**Spec coverage check:**

| Spec section | Covered by task |
|---|---|
| §1.1 stage contracts (ingest, plan, reconcile) | 6, 10, 12 |
| §1.2 grouping rules | 7, 8, 9 |
| §2 data model | 3 |
| §2.1/2.2 invoice + CM field mapping | 7, 8 (line amounts, DocNumber); QBO payload module deferred to Part 2 |
| §2.3 six JE legs | 9 |
| §3.1 classifier decision tree | 4 |
| §3.3 E1 (missing delivery date → skip) | 7 |
| §3.3 E6 (shop mismatch aborts) | 5 |
| §3.3 E9 (qty=0 disqualifies sale) | 4 |
| §5 Identity 1 & 2 | 11, 12 |
| §5.1 failure workflow (CSV + marker) | 12 |
| §6.1 directory layout | 1 |
| §6.2 CLI (ingest/plan/reconcile) | 13 |
| §6.3 shops.yaml | 1 |

**Deferred to Part 2 (QBO integration plan):** stages 4 (post) and 5 (verify), OAuth/HTTP client, payload builders that translate `InvoiceBatch`/`CreditMemoBatch`/`StatementJE` into QBO v3 JSON, account/customer/item upserts, ledger-based idempotency, `--dry-run`/`--commit` wiring, `run-all` and `status` CLI subcommands, `verify` identity 3, E2/E4/E7/E8/E10 handling (all QBO-posting-time concerns).

**Type consistency check:** `StatementJE.lines: list[JELine]`, `JELine.account_role` used consistently in Tasks 3, 9, 12. `InvoiceBatch.source_order_ids: list[str]` consistent in Tasks 3, 7. `Mismatch` dataclass shared between Tasks 11 and 12. No drift.

**Placeholder scan:** no TBD / fill-in / "implement similar" placeholders.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-04-23-tiktok-qbo-harness-part1-local-pipeline.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
