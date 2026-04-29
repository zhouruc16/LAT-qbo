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

## Per-payout JE structure (final)

For each Payment ID:

```
DR BofA Checking                 Payable amount
DR Marketplace Fees              |Σ Fees|
DR TikTok Reserve                |Reserve| if reserve_signed < 0 (withheld)
DR Shipping Expense              |Shipping| if Shipping < 0 (rare)
DR TikTok Adjustments            |Adj| if Adj < 0
   CR TikTok Clearing             Σ Net sales (clears the invoices)
   CR Shipping Income             Shipping if > 0
   CR TikTok Adjustments          Adj if > 0
   CR TikTok Reserve              Reserve if reserve_signed > 0 (released)
```

Math: `Net + Shipping + Fees + Adj + Reserve = Payable` rearranges so DR
total = CR total for every JE.

## Tests

```bash
pytest tests/ -q                                    # 54 tests, all green
pytest tests/test_qbo_post.py -v                    # JE balance verification
```

`tests/test_qbo_post.py::test_je_balances_for_all_h1_payments` is the
real-data integration test: loads both Q1 + Q2 xlsx, builds JEs for all
176 H1 LELNU payments, asserts every one balances.
