# TikTok LAT → QuickBooks Online Harness — Design

**Date:** 2026-04-23
**Primary goal:** Post QuickBooks Online transactions (Invoice, Credit Memo, Journal Entry) from TikTok Shop LAT settlement exports, via the Intuit QBO v3 REST API, such that the books tie to the bank account to the penny.
**Scope of v1:** one TikTok shop (`USLCPLELNU`). Design is multi-shop-ready.
**Author of record:** LAT Group Inc bookkeeping.

---

## 0. Summary

A Python CLI, `tiktok_qbo`, reads a quarterly LAT `.xlsx`, emits three kinds of QBO transactions, and proves the result reconciles against the bank statement. Stages are run sequentially; each writes inspectable JSON to `state/` so the whole pipeline is restartable and auditable.

Three QBO object types per run:

1. **Invoice** — one per delivery date. Recognizes revenue when control transfers (ASC 606). Posts `DR TikTok Clearing / CR Sales Income`.
2. **Credit Memo** — one per statement date that processed refunds/chargebacks. Posts `DR Refunds & Returns / CR TikTok Clearing`.
3. **Journal Entry** — one per TikTok Payment ID (= one bank deposit). Moves money out of Clearing into Bank, minus fees, shipping, and adjustments.

At any moment, `TikTok Clearing` account balance equals "sales delivered but not yet paid out."

---

## 1. Architecture & Data Flow

```
Inputs                    Stages                           Outputs
───────                   ───────                          ────────
lat-<shop>-<period>.xlsx  1  ingest   → state/rows.jsonl, statements, payments, reserves
                          2  plan     → state/plan-<hash>/{invoices,credit_memos,jes}/*.json
                          3  reconcile→ state/reconcile-<hash>.{csv,pass|fail}
                          4  post     → QBO; state/ledger.jsonl (append-only)
                          5  verify   → state/verify-<hash>.txt
(optional) bank-csv.csv   (3.5 bank-rec — future)
```

Each stage reads only from the previous stage's on-disk artifacts. Any stage can be re-run safely.

### 1.1 Stage contracts

| Stage | Reads | Writes | Fails when… |
|---|---|---|---|
| **ingest** | `lat-<shop>-<period>.xlsx` | `rows-<hash>.jsonl`, `statements-<hash>.jsonl`, `payments-<hash>.jsonl`, `reserves-<hash>.jsonl` | unknown `Type` value, unreadable xlsx, shop mismatch with config |
| **plan** | stage-1 artifacts | `plan-<hash>/invoices/<date>.json`, `…/credit_memos/<date>.json`, `…/journal_entries/<payment_id>.json` | an order has no delivery date and no fallback rule applies |
| **reconcile** | plan + statements + payments | `reconcile-<hash>.csv`, `…pass`/`…fail` | Identity 1 (per Payment) or Identity 2 (per Statement) off by > $0.01 |
| **post** | plan + ledger | `ledger.jsonl`, `failures.jsonl` | QBO 4xx; retries 5xx/429; halts on first unrecoverable error |
| **verify** | ledger + live QBO query | `verify-<hash>.txt` | any posted total drifts from plan |

### 1.2 Grouping rules

```
Invoice batch     : one per (shop_id, delivery_date)    ← rows classified "sale"
Credit Memo batch : one per (shop_id, statement_date)   ← rows classified "refund" or "chargeback"
Journal Entry     : one per (shop_id, payment_id)       ← rolled up from Statements sheet
                    JE.TxnDate = Payment completion date
```

JE is keyed on **Payment ID, not Statement ID** so every JE matches exactly one bank deposit. Bank reconciliation in QBO becomes a one-click affair.

---

## 2. Data Model

All dataclasses JSON-serializable, `Decimal` for money, ISO dates.

```python
@dataclass
class NormalizedRow:
    shop_id: str
    order_id: str
    sku_id: str
    statement_id: str
    payment_id: str                 # "" if not yet paid out
    statement_date: date | None
    order_created_date: date | None
    order_shipment_date: date | None
    order_delivery_date: date | None
    row_type: str                   # raw LAT "Type"
    classification: Literal["sale", "refund", "chargeback",
                            "reimbursement", "fee_only", "adjustment"]
    customer_payment: Decimal       # invoice line amount source
    customer_refund: Decimal        # credit memo line amount source
    gross_sales: Decimal            # audit only
    quantity: int
    product_name: str
    raw: dict[str, Any]             # all 94 columns preserved

@dataclass
class StatementRow:
    shop_id: str
    statement_id: str
    payment_id: str
    statement_date: date
    status: str
    total_settlement_amount: Decimal
    net_sales: Decimal
    shipping: Decimal
    fees: Decimal                   # usually negative
    adjustments: Decimal
    reserve_amount: Decimal
    payable_amount: Decimal

@dataclass
class PaymentRow:
    shop_id: str
    payment_id: str
    payment_amount: Decimal
    payment_initiation_date: date
    payment_completion_date: date
    bank_posted_date: date | None   # filled by bank-rec stage
    bank_account_masked: str
    status: str

@dataclass
class InvoiceBatch:
    shop_id: str
    delivery_date: date
    doc_number: str                 # "INV-PLELNU-20240430"
    lines: list[InvoiceLine]
    total_amount: Decimal
    source_order_ids: list[str]

@dataclass
class CreditMemoBatch:
    shop_id: str
    statement_date: date
    doc_number: str                 # "CM-PLELNU-20240512"
    lines: list[CreditMemoLine]
    total_amount: Decimal
    source_order_ids: list[str]

@dataclass
class StatementJE:
    shop_id: str
    payment_id: str
    doc_number: str                 # "JE-PLELNU-3459073178040177003"
    txn_date: date                  # payment_completion_date
    lines: list[JELine]             # exactly 6 canonical legs (see §4)
    statement_ids: list[str]
    bank_amount: Decimal
```

### 2.1 Field mapping: `InvoiceBatch` → QBO Invoice

| QBO field | Value |
|---|---|
| `CustomerRef.value` | "TikTok Shop PLELNU Customer" (upserted) |
| `TxnDate` | `delivery_date` |
| `DocNumber` | `INV-PLELNU-<YYYYMMDD>` |
| `PrivateNote` | `"Shop PLELNU · delivered <date> · orders: <id1>, <id2>, ..."` (truncated to 4000 chars) |
| `Line[i].DetailType` | `"SalesItemLineDetail"` |
| `Line[i].SalesItemLineDetail.ItemRef.value` | Item upserted by SKU |
| `Line[i].SalesItemLineDetail.Qty` | `quantity` |
| `Line[i].SalesItemLineDetail.UnitPrice` | `customer_payment / quantity` |
| `Line[i].Amount` | `customer_payment` |
| `TxnTaxDetail` | **omitted** — TikTok is marketplace facilitator and remits tax directly |

### 2.2 Field mapping: `CreditMemoBatch` → QBO CreditMemo

Mirror of Invoice with `TxnDate = statement_date` and `Line[i].Amount = abs(customer_refund)`. Same customer, same SKU-based items.

### 2.3 Field mapping: `StatementJE` → QBO JournalEntry

Exactly six canonical legs per JE:

| # | Posting | Amount |
|---|---|---|
| 1 | DR **Bank** *(real checking account)* | `PaymentRow.payment_amount` |
| 2 | DR **TikTok Reserve** *(Other Current Asset)* | `Σ StatementRow.reserve_amount` |
| 3 | DR **Merchant Fees** *(Expense)* | `abs(Σ StatementRow.fees)` |
| 4 | DR **Shipping Net** if amount ≥ 0, else CR | `Σ StatementRow.shipping` (abs value on the chosen side) |
| 5 | DR **Adjustments** if amount ≥ 0, else CR | `Σ StatementRow.adjustments` (abs value on the chosen side) |
| 6 | CR **TikTok Clearing — PLELNU** *(Other Current Asset)* | plug = `DR_total − CR_total_of_legs_1_through_5` |

**Invariant:** the six legs must net to zero. Leg 6 is computed as a plug from the other five so the JE balances by construction. It is *not* `Σ StatementRow.payable_amount` — that value already has fees/shipping netted and would double-count.

---

## 3. Classification & Edge-Case Policy

### 3.1 Classifier decision tree (per NormalizedRow)

```
if row_type == "Chargeback":                              → "chargeback"
elif row_type == "TikTok Shop reimbursement":             → "reimbursement"
elif row_type == "Logistics reimbursement":               → "reimbursement"
elif row_type == "Order":
    if gross_sales > 0 and gross_sales_refund == 0:       → "sale"
    elif gross_sales_refund < 0 and gross_sales <= 0.01:  → "refund"
    elif gross_sales == 0 and customer_payment == 0:      → "fee_only"
    else:                                                  → "adjustment"
else:                                                      → "adjustment"
```

Unknown `row_type` halts ingest with a loud error. Better to fail than silently misclassify.

### 3.2 Routing per classification

| Classification | Invoice | Credit Memo | JE |
|---|---|---|---|
| `sale` | ✓ (by delivery date) | — | ✓ (its customer_payment rolls into Clearing credit) |
| `refund` | — | ✓ (by statement date) | ✓ (refund admin fee, return shipping roll into JE) |
| `chargeback` | — | ✓ (by statement date) | ✓ (chargeback fee) |
| `reimbursement` | — | — | ✓ (credits Reimbursements income or contra-Shipping) |
| `fee_only` | — | — | ✓ (only touches JE) |
| `adjustment` | — | — | ✓ (rolls into Adjustments leg) |

### 3.3 Edge cases

| # | Case | Policy |
|---|---|---|
| E1 | Order has **no delivery date** | Write to `state/deferred.jsonl`; do not invoice. Next run picks it up if LAT now has the date. |
| E2 | Order **already invoiced** in prior run (ledger hit) | Skip Invoice step for that order. Its fees/refunds still roll into the current JE. |
| E3 | Order delivered in period **before earliest ingested LAT** | Treat as pre-period adjustment: fees/refunds go to JE only, no Invoice. Log to `state/pre_period.csv` for human review. |
| E4 | Statement in file has `status = "Pending"` (no payout yet) | Invoices and Credit Memos post normally (by delivery/statement date). JE is **not** created; statement is held in `state/pending_statements.jsonl` for a future run. |
| E5 | Payment completion date in LAT differs from bank posted date (ACH lag) | v1: JE uses `payment_completion_date`. Accept 1-business-day drift as normal. v2 adds bank-rec stage that overrides with bank posted date. |
| E6 | Row belongs to a shop not in config | Ingest fails. Explicit shop config prevents accidentally cross-booking. |
| E7 | Reconciliation fails for one Payment | Run halts before posting. Operator reviews `reconcile-<hash>.csv` diff; either fixes data / mapping or manually flags the Payment for exclusion in `config/overrides.yaml`. |
| E8 | QBO Invoice / CM / JE with the same DocNumber already exists | Skip; append ledger entry `{status: "already_present"}`. Idempotent. |
| E9 | Quantity = 0 on an Order row classified `sale` | Treat as `adjustment` instead; no Invoice line. |
| E10 | Customer payment = 0 but gross_sales > 0 (100% seller-funded promo) | No Invoice line (QBO rejects $0 lines). Route the row to `state/pre_period.csv`-style review file `state/zero_revenue.csv` and let the Statement JE's seller-discount leg absorb the cost. Clearing math stays consistent because the JE's Clearing credit doesn't include this order's customer_payment (= 0). |

---

## 4. QBO Integration

### 4.1 Chart of Accounts (one-time setup)

| Account | QBO Type | Purpose |
|---|---|---|
| TikTok Clearing — PLELNU | Other Current Asset | Per-shop receivable from TikTok |
| TikTok Reserve — PLELNU | Other Current Asset | Holdback awaiting release |
| Sales of Product Income | Income | Revenue (may already exist) |
| Merchant Fees — TikTok | Expense | Referral, transaction, promo fees |
| Shipping Expense — TikTok | Expense | Platform shipping charged to seller |
| Affiliate Commissions — TikTok | Expense | Creator/affiliate payouts |
| Refunds & Returns — TikTok | Income (contra) or Expense | Reverse-revenue bucket |
| Chargeback Fees — TikTok | Expense | Disputed transaction fees |
| Reimbursements — TikTok | Income | TikTok-funded reimbursements |

Account IDs resolved at runtime by name (from `config/accounts.yaml`). Failure to find any required account aborts the run before any POST.

### 4.2 Customer & Item strategy

- **Customer:** single generic `"TikTok Shop PLELNU Customer"` upserted once. LAT provides no buyer PII.
- **Item:** one per unique `SKU ID`, upserted on first sight. `Type = Service`, `IncomeAccountRef = Sales of Product Income`. `Sku` column holds the TikTok SKU ID for round-tripping.

### 4.3 Post order within stage 4

1. Resolve all required Account IDs (query, never POST new accounts).
2. Upsert Customer.
3. Upsert every Item referenced by the plan.
4. POST Invoices (by ascending delivery_date).
5. POST Credit Memos (by ascending statement_date).
6. POST JournalEntries (by ascending txn_date).
7. Append each outcome to `ledger.jsonl`.

### 4.4 Idempotency — three layers

1. **Deterministic DocNumbers** — `INV-<shop>-<YYYYMMDD>`, `CM-<shop>-<YYYYMMDD>`, `JE-<shop>-<PaymentID-last-12>`.
2. **`Request-Id` header** — UUIDv5 derived from the DocNumber; QBO treats repeats within its dedup window as the original.
3. **Local ledger** — `ledger.jsonl` scanned before each POST; skip if already present.

### 4.5 OAuth, rate limits, retries

- Refresh token + realm_id stored in `.env`. Access token refreshed on first call or on any 401.
- Retry 429/5xx with exponential backoff (5 attempts, 2s base, 60s cap, jittered).
- Rate-pace at ≤ 8 writes/sec (QBO limit is 500/min).
- For backfills > 1000 writes, use the Batch endpoint (up to 30 ops/req). Not needed for steady-state quarterly runs.

---

## 5. Reconciliation Identities

Three layers, enforced in stage 3 before any POST.

### Identity 1 — per Payment (must balance ±$0.01)

```
PaymentRow.payment_amount
 == Σ StatementRow.payable_amount [where payment_id == this payment]
 == Σ NormalizedRow.customer_payment [orders in those statements]
    − abs(Σ fees) − Σ shipping − Σ adjustments − reserve_amount
```

### Identity 2 — per Statement (must balance ±$0.01)

```
StatementRow.payable_amount
 == net_sales + shipping + fees + adjustments − reserve_amount
```

### Identity 3 — cross-month sanity (not enforced; reported)

```
Σ Bank deposits (month)  ≠  Σ Invoices by delivery date (month) − Σ Fees (month)
```

Reported in `verify-<hash>.txt` as an informational line. The Clearing account balance answers it automatically.

### 5.1 Failure workflow

- Any Identity 1 or 2 failure → stage 3 writes `reconcile-<hash>.fail` and aborts.
- Operator opens `reconcile-<hash>.csv` (columns: payment_id, statement_id, expected, computed, diff, offending_order_ids).
- Fix options: correct mapping bug, add override to `config/overrides.yaml`, or exclude a bad statement and re-run.

---

## 6. Project Layout, CLI, Configuration, Testing

### 6.1 Directory structure

```
tiktok_qbo/
├── pyproject.toml
├── README.md
├── .env.example
├── config/
│   ├── shops.yaml              # { PLELNU: { display_name, customer_name, bank_account_masked, clearing_account } }
│   ├── accounts.yaml           # map of role → QBO account name
│   └── overrides.yaml          # statement_id / payment_id exclusions
├── src/tiktok_qbo/
│   ├── __init__.py
│   ├── cli.py                  # argparse: ingest, plan, reconcile, post, verify, run-all
│   ├── config.py
│   ├── money.py                # Decimal helpers, 2dp quantize, sign guards
│   ├── dates.py                # parse LAT date strings, business-day add
│   ├── ingest/
│   │   ├── lat_xlsx.py         # multi-sheet reader
│   │   └── classify.py
│   ├── plan/
│   │   ├── invoices.py         # sale rows → InvoiceBatch per delivery_date
│   │   ├── credit_memos.py     # refund/chargeback → CreditMemoBatch per statement_date
│   │   └── statement_je.py     # Statements+Payments → StatementJE per payment_id
│   ├── qbo/
│   │   ├── client.py           # OAuth, retry, request-id, rate-limit
│   │   ├── upserts.py          # customer, item, account lookups
│   │   └── payloads.py         # Batch → QBO JSON
│   ├── reconcile.py
│   ├── ledger.py
│   ├── verify.py
│   └── logging_conf.py
├── state/                      # gitignored
│   ├── rows-*.jsonl
│   ├── statements-*.jsonl
│   ├── payments-*.jsonl
│   ├── reserves-*.jsonl
│   ├── plan-<hash>/
│   ├── reconcile-*.csv
│   ├── ledger.jsonl
│   ├── failures.jsonl
│   ├── deferred.jsonl
│   ├── pending_statements.jsonl
│   ├── pre_period.csv
│   └── verify-*.txt
└── tests/
    ├── fixtures/               # small xlsx + expected JSON
    ├── test_ingest_classify.py
    ├── test_plan_grouping.py
    ├── test_plan_payloads.py
    ├── test_reconcile_identities.py
    ├── test_qbo_client.py      # mocked HTTP
    └── test_e2e_sandbox.py     # opt-in, hits QBO sandbox
```

### 6.2 CLI

```
tiktok_qbo ingest    <file.xlsx>                   # stage 1
tiktok_qbo plan      --hash <H>                    # stage 2
tiktok_qbo reconcile --hash <H>                    # stage 3
tiktok_qbo post      --hash <H> (--dry-run | --commit) --env {sandbox|production}
tiktok_qbo verify    --hash <H>
tiktok_qbo run-all   <file.xlsx> (--dry-run | --commit) --env {sandbox|production}
tiktok_qbo status                                  # ledger summary + open clearing balance
```

`<H>` is the SHA-256 of the input file's sorted rows; stable across re-runs of the same data.

### 6.3 Configuration — `config/shops.yaml`

```yaml
shops:
  PLELNU:
    display_name: "TikTok Shop PLELNU"
    customer_name: "TikTok Shop PLELNU Customer"
    clearing_account: "TikTok Clearing — PLELNU"
    reserve_account:  "TikTok Reserve — PLELNU"
    bank_account_masked: "********9247"
```

Adding a shop = a new key. No code change.

### 6.4 Test strategy

| Layer | What it covers | Where |
|---|---|---|
| Unit | classifier branches, date math, money arithmetic, idempotency-key derivation, grouping logic | `tests/test_*.py` with fixture rows |
| Contract | small fixture xlsx (a handful of rows covering every classification) → expected plan JSON byte-for-byte | `tests/fixtures/` + golden files |
| Reconciliation | synthetic statements whose identities must hold / must fail | `test_reconcile_identities.py` |
| HTTP | QBO client with mocked responses for 200/401/429/5xx | `test_qbo_client.py` |
| End-to-end | real QBO sandbox round-trip on a tiny fixture; opt-in via env var | `test_e2e_sandbox.py` |

### 6.5 Operational workflow (per quarter)

1. Export LAT for shop PLELNU covering the quarter.
2. Place at `inputs/lat-PLELNU-<period>.xlsx`.
3. `tiktok_qbo run-all inputs/lat-PLELNU-<period>.xlsx --dry-run --env sandbox`.
4. Inspect `state/plan-<hash>/` JSON. Spot-check a few Invoices, one Credit Memo, one JE.
5. If OK: `tiktok_qbo run-all ... --commit --env sandbox`.
6. In QBO sandbox: view `TikTok Clearing` account ledger — should look like saw-tooth (Invoices push up, JEs pull down).
7. When satisfied: same command with `--env production`.
8. `tiktok_qbo status` prints ledger summary and open Clearing balance.

---

## 7. Out of Scope for v1 (explicit)

- Multi-shop in a single run (design-ready; not built).
- Automatic bank reconciliation with ACH-date correction (stage 3.5 placeholder).
- Reserve-release tracking (Reserve details sheet is ingested but not yet posted).
- TikTok API pull instead of xlsx export.
- Currencies other than USD.
- Sales tax routing for non-marketplace-facilitator states.

Each can be added later without reshaping the core pipeline.

---

## 8. References

- Intuit — Basic invoicing implementation: https://developer.intuit.com/app/developer/qbo/docs/develop/basic-implementations/basic-invoicing-implementation
- Intuit — Invoice object: https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/invoice
- Intuit — Journal Entry object: https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/journalentry
- Intuit — Credit Memo object: https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/creditmemo
- Intuit — OAuth 2.0: https://developer.intuit.com/app/developer/qbo/docs/develop/authentication-and-authorization/oauth-2.0
- ASC 606 Revenue Recognition (FASB)

---

## 9. Acceptance Criteria

A v1 release is done when, on the Q2 2024 LAT file for PLELNU:

1. Ingest classifies every row; no "unknown Type" aborts.
2. Plan produces daily Invoices, daily Credit Memos, and one JE per unique Payment ID.
3. Reconcile passes Identity 1 and Identity 2 for every Payment and Statement.
4. Post (sandbox) creates the expected counts. Second run creates zero duplicates.
5. Verify confirms every posted Invoice / CM / JE total matches the plan.
6. `TikTok Clearing — PLELNU` balance after verify equals the sum of Invoices whose Payment has not yet landed in a JE (i.e., "in-flight" sales).
