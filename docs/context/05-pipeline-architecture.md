# 05 — Pipeline architecture

## Module map

```
src/tiktok_qbo/
├── ingest/
│   ├── lat_xlsx.py        Read xlsx sheets → NormalizedRow / StatementRow / PaymentRow / ReserveRow
│   ├── bank_pdf.py        Parse BofA PDFs (3 ACH formats) → BankLine using pdfplumber
│   ├── bank_reconcile.py  Join Payments ↔ BankLines (Payment ID primary, date+amount fallback)
│   ├── classify.py        Classify Order detail rows: sale / refund / chargeback / fee_only / etc.
│   └── pipeline.py        run_ingest(): xlsx → JSONL state files
├── plan/
│   ├── invoices.py        group_invoices(): rows → InvoiceBatch per (statement, delivery_date)
│   ├── credit_memos.py    group_credit_memos(): refund handling
│   ├── statement_je.py    build_statement_jes(): per-Payment-ID JEs (signed reserve aware)
│   └── pipeline.py        run_plan(): JSONL → per-entity JSON files
├── qbo/
│   ├── env.py             load_creds(): multi-path .env loader, BOM-tolerant
│   ├── auth.py            run_auth_flow(): OAuth localhost callback
│   ├── client.py          QboClient: REST wrapper, auto refresh-token rotation, dry-run mode
│   ├── coa.py             bootstrap_coa(): idempotent account + customer creation
│   └── post.py            post_h1(): build & post invoices + JEs
├── reconcile.py           check_identity_1, check_identity_2; ReconcileResult; run_reconcile
├── models.py              Pydantic models: Normalized/Statement/Payment/Reserve/Invoice/JE rows
├── money.py               to_money(), money_sum(), close_enough()
├── dates.py               parse_lat_date()
├── config.py              shop config (small, mostly unused now)
└── cli.py                 ingest / plan / reconcile / auth / qbo-init / post commands
```

## Data flow

```
Q1 xlsx ──┐
Q2 xlsx ──┼──> ingest.lat_xlsx ──> rows-<hash>.jsonl
          │                       statements-<hash>.jsonl
          │                       payments-<hash>.jsonl
          │                       reserves-<hash>.jsonl
BofA PDFs ┴──> ingest.bank_pdf  ──> BankLine list
                                          │
                                          ▼
                          ingest.bank_reconcile (joins by Payment ID)
                                          │
                                          ▼
                          reconcile (identity checks 1 + 2)
                                          │
                                          ▼
                          plan.invoices  ──> per-delivery-date InvoiceBatch
                          plan.statement_je  ──> per-Payment StatementJE
                                          │
                                          ▼
                          qbo.post (build payloads, post via Intuit API)
                                          │
                                          ▼
                                   QBO company:
                                   - Invoices (one per delivery-date group)
                                   - Journal Entries (one per Payment ID)
```

## Key types

`NormalizedRow` — one per Order details row.
`StatementRow` — one per Statements row. Has signed `reserve_amount`.
`PaymentRow` — one per Payments row.
`BankLine` — one per parsed bank deposit line. `payout_id` may be empty
for HYPERWALLET / TikTok-alt formats.
`InvoiceBatch` — one per (statement, delivery_date). Lines per SKU.
`StatementJE` — one per Payment ID. Lines: bank, reserve, fees, shipping,
adjustments, clearing.

## Driver scripts

```
scripts/
├── reconcile_h1_2024.py   # Phase 1: ingest + reconcile, no QBO touched
├── post_h1_2024.py        # Phase 4-6: dry-run / single-payment / full post
└── smoke_q2_2024.py       # Quick Q2 sanity check
```

## Commands

```bash
# Phase 1: reconciliation only (no QBO)
python scripts/reconcile_h1_2024.py

# Phase 2: OAuth (one-time, opens browser)
python -m tiktok_qbo auth

# Phase 3: bootstrap COA + customer
python -m tiktok_qbo qbo-init --dry-run    # preview
python -m tiktok_qbo qbo-init              # actually create

# Phase 4: dry-run posting
python scripts/post_h1_2024.py --dry-run

# Phase 5: single-payment preview (real post, one statement)
python scripts/post_h1_2024.py --payment-id 3459076539020317035

# Phase 6: full H1 production post
python scripts/post_h1_2024.py
```

## Per-statement posting flow (final)

For each Statement (joined to Payment via Payment ID), `post_h1` emits
**four** entity types so that A/R and Clearing both reach $0:

```
PER (statement, delivery_date) WITH POSITIVE Net_sales rows:
  Invoice  → DR A/R, CR Sales              (amount = Σ row.Net_sales for group)

PER (statement, delivery_date) WITH NEGATIVE Net_sales rows:
  CreditMemo → DR Sales, CR A/R            (amount = |Σ row.Net_sales for group|)

PER Payment (covers all statements bundled into the same Payment ID):
  ReceivePayment with LinkedTxn[invoices+CMs from all bundled stmts]
    DepositToAccountRef = Clearing
    TotalAmt = Σ statement.net_sales       → DR Clearing, CR A/R
    (single positive-total RP even when bundle includes refund-only stmts)

PER Payment (i.e. per Payment ID):
  JournalEntry
    DR BofA Checking                payment_amount
    DR Marketplace Fees             |Σ Fees|
    DR TikTok Reserve               |Reserve| if reserve_signed < 0 (withheld)
    DR Shipping Expense             |Shipping| if Shipping < 0 (rare)
    DR TikTok Adjustments           |Adj| if Adj < 0
       CR TikTok Clearing            Σ statement.net_sales        ← uses xlsx
       CR Shipping Income            Shipping if > 0
       CR TikTok Adjustments         Adj if > 0
       CR TikTok Reserve             Reserve if reserve_signed > 0 (released)
```

**Bucketing rule**: rows are bucketed by **sign of Net_sales** (not
classification), with `bucket_date = order_delivery_date or statement_date`.
This catches `adjustment`-classified rows that carry positive revenue, and
sale rows that lack a delivery date (~26 in H1 totaling $1,528.85).

**Math identity (per JE)**:
`Bank + |Fees| + |Reserve_w| + |Ship_neg| + |Adj_neg| =
 Σ stmt.net_sales + Ship_pos + Adj_pos + Reserve_released`
follows from `Σ payable = Σ (net + ship + fees + adj + reserve_signed)`
and `payment_amount = Σ payable`.

**Trial balance after a full posting cycle**:
- `BofA Checking` = +Σ payment_amount
- `Sales - TikTok LELNU` = -Σ statement.net_sales
- `Marketplace Fees - TikTok` = +|Σ Fees|
- `TikTok Reserve - LELNU` = +Σ Reserve withholdings - Σ Reserve releases
- `Shipping Income - TikTok` = -Σ Shipping (sign net per statement)
- `TikTok Adjustments` = -Σ Adjustments
- `TikTok Clearing - LELNU` = $0 (DR=CR)
- A/R for "TikTok Shop LELNU" = $0 (Receive Payments mark invoices Paid)

DocNumber idempotency keys (per D12 in 04-decisions; 21-char QBO limit):
- Invoice: `INV-<stmt_last8>-<delivery_yymmdd>`     (≤19 chars)
- CreditMemo: `CM-<stmt_last8>-<delivery_yymmdd>`   (≤18 chars)
- Receive Payment: `PAY-<payment_last12>`           (≤16 chars; one per Payment ID)
- JournalEntry: `JE-<payment_last12>`               (≤15 chars)

## Tests

```bash
pytest tests/ -q                                    # 54 tests, all green
pytest tests/test_qbo_post.py -v                    # JE balance verification
```

`tests/test_qbo_post.py::test_je_balances_for_all_h1_payments` is the
real-data integration test: loads both Q1 + Q2 xlsx, builds JEs for all
176 H1 LELNU payments, asserts every one balances.
