# tiktok_qbo

Local pipeline for posting TikTok Shop LAT settlement data into QuickBooks Online.

Reads TikTok's per-storefront `.xlsx` exports + Bank of America PDF statements,
reconciles them by `Payment ID`, and posts invoices + journal entries into a
fresh QBO company via the Intuit Accounting API.

## Status

- **Storefront**: USLCPLELNU (LELNU) — single-storefront for now; YEWHX / CUEL4Y
  can be layered on by re-running with their xlsx + the same bank PDFs.
- **Verified period**: H1 2024 (Jan 1 – Jun 30) — 176/176 LELNU payments
  reconcile to the cent against BofA deposits.
- **Sales tax method**: Net (TikTok is a marketplace facilitator; tax does not
  touch LAT's books).
- **Customer model**: One `TikTok Shop LELNU` customer record; one invoice per
  `(Statement ID, Order delivery date)` group at `Net sales` amount.

## Identities verified end-to-end

```
Order details rows (per SKU):
  Σ Total settlement amount  =  Statements.Total settlement amount

Statements (per statement):
  Net sales + Shipping + Fees + Adjustments  =  Total settlement amount
  Total settlement amount + Reserve Amount    =  Payable amount

Payments (per payout, joined by Payment ID):
  Σ Payable amount per Payment ID  =  Payments.Payment amount

Bank PDF (joined by `payout ID` token):
  Bank deposit amount  =  Payment amount
```

Reserve is signed: **negative = withheld this cycle (TikTok holds);
positive = released this cycle (TikTok paid back held funds)**.

## Project layout

```
src/tiktok_qbo/
  ingest/
    lat_xlsx.py          Read Order details / Statements / Payments / Reserve sheets
    bank_pdf.py          Parse BofA PDFs (3 ACH formats: TikTok-old, TikTok-alt, HYPERWALLET)
    bank_reconcile.py    Join Payments ↔ bank lines (by Payment ID; date+amount fallback)
    pipeline.py          Combined ingest -> JSONL state files
  plan/
    invoices.py          Group rows by (statement_id, delivery_date)
    credit_memos.py      Refund handling
    statement_je.py      Bank-deposit JE per Payment ID (signed-reserve aware)
    pipeline.py          Read JSONL -> emit per-payment plan files
  qbo/
    env.py               Load .env from CWD/parents (multi-key, BOM-tolerant)
    auth.py              OAuth 2.0 localhost-callback flow
    client.py            REST client with auto refresh-token rotation
    coa.py               Idempotent chart-of-accounts + customer bootstrap
    post.py              Build & post invoices / journal entries (idempotent via DocNumber)
  reconcile.py           identity_1, identity_2 checks
  cli.py                 ingest / plan / reconcile / auth / qbo-init / post

scripts/
  reconcile_h1_2024.py   End-to-end H1 reconciliation report (no QBO needed)
  post_h1_2024.py        End-to-end H1 posting driver (QBO required)
  smoke_q2_2024.py       Quick smoke test on Q2 xlsx

tests/
  54 tests, all green. Includes a real-data integration test loading both
  Q1 and Q2 xlsx and verifying all 176 H1 JEs balance DR=CR.
```

## Install

```bash
pip install -e ".[dev]"
```

Dependencies: `openpyxl`, `pdfplumber`, `pydantic`, `requests`, `python-dotenv`,
`pyyaml`. Test extras: `pytest`, `pytest-cov`.

## Configure (.env)

Create a `.env` file in the project root (or any parent dir):

```
INTUIT_CLIENT_ID=<from Intuit Developer -> Keys and credentials>
INTUIT_CLIENT_SECRET=<from same place>
INTUIT_REDIRECT_URI=http://localhost:8080/callback
INTUIT_REALM_ID=
INTUIT_REFRESH_TOKEN=
INTUIT_ENVIRONMENT=sandbox
```

`INTUIT_REALM_ID` and `INTUIT_REFRESH_TOKEN` get auto-filled by the OAuth flow.
Use `INTUIT_ENVIRONMENT=production` once production keys are unlocked.

## End-to-end usage

### 1. Verify reconciliation only (no QBO touched)

```bash
python scripts/reconcile_h1_2024.py
```

Outputs to `state_h1/`:
- `reconcile-summary.csv` — one row per Payment ID
- `reconcile-unmatched-bank.csv` — lines we couldn't match
- `reconcile-h1-summary.txt` — headline totals + identity-check status

### 2. Authorize against QBO (one-time)

```bash
python -m tiktok_qbo auth
```

Opens browser → you log in to QBO → approve → callback captures the code →
writes refresh token + realm ID back to `.env`.

### 3. Bootstrap chart of accounts + customer

```bash
python -m tiktok_qbo qbo-init --dry-run    # preview only
python -m tiktok_qbo qbo-init              # actually create
```

Creates (idempotent — re-runnable):

```
TikTok Clearing - LELNU       Other Current Asset
TikTok Reserve - LELNU        Other Current Asset
Sales - TikTok LELNU          Income
Shipping Income - TikTok      Income
TikTok Adjustments            Other Income
Marketplace Fees - TikTok     Expense
Shipping Expense - TikTok     Expense
Customer: TikTok Shop LELNU   Customer record
```

The BofA Checking bank account is looked up from existing QBO accounts.

### 4. Preview and post

```bash
# Preview a single payment (no API write):
python scripts/post_h1_2024.py --dry-run --payment-id 3459076539020317035

# Single payment, real post:
python scripts/post_h1_2024.py --payment-id 3459076539020317035

# Full H1 dry-run:
python scripts/post_h1_2024.py --dry-run

# Full H1 production:
python scripts/post_h1_2024.py
```

Idempotency: each invoice gets `DocNumber = INV-LELNU-<stmt8>-<yymmdd>`,
each JE `JE-LELNU-<payment_id_last12>`. Re-runs skip existing entries.

## Per-payout journal entry structure

For each Payment ID (one bank deposit), the JE posted to QBO is:

```
DR BofA Checking                 Payable amount
DR Marketplace Fees              |Σ Fees|
DR TikTok Reserve                |Reserve| if reserve < 0 (withheld)
DR Shipping Expense              |Shipping| if Shipping < 0 (rare)
DR TikTok Adjustments            |Adj| if Adj < 0
   CR TikTok Clearing             Σ Net sales (clears the invoices)
   CR Shipping Income             Shipping if > 0
   CR TikTok Adjustments          Adj if > 0
   CR TikTok Reserve              Reserve if reserve > 0 (released)
```

Math: `Net + Shipping + Fees + Adj + Reserve = Payable` rearranges to
`DR_total = CR_total` for every JE. Verified in
`tests/test_qbo_post.py::test_je_balances_for_all_h1_payments` against all
176 H1 LELNU payments.

## Reconciliation report (H1 2024 LELNU)

```
Order rows:                       50,540
Statements:                          179
Payments:                            176

Total settlement amount:    $   1,690,241.20
Total reserve (signed):     $       1,080.70   (slight net release H1)
Total payable (statements): $   1,691,321.90
Total payment amounts:      $   1,714,496.90
Total matched bank deposits:$   1,714,496.90
  (payment vs bank diff:               $0.00)

Identity_2 mismatches: 0
Identity_1 mismatches: 0

Bank reconciliation: 176/176 matched, 0 amount mismatches
Unmatched bank lines: 36 (all expected: 31 post-H1 + 5 export-window gap)
```

## Known data caveats

- **5 in-H1 unmatched bank lines** ($18,402.95) — late-March 2024 payouts
  whose statements aren't in either Q1 or Q2 xlsx export. To fix, re-export
  Q1 with end date moved to 2024-04-03 from TikTok Seller Center.
- **HYPERWALLET SYST** lines (early Jan, before TikTok switched to
  direct-ACH) have no payout ID in the bank description; matched by
  date+amount fallback.
- **Multi-storefront**: this pipeline runs one storefront at a time. Bank
  PDFs contain all storefronts; we filter by `USLCPLELNU` for the LELNU run.

## Tests

```bash
pytest tests/ -q
```

54 tests, all green.

## Security

- `.env` is gitignored. Never commit credentials.
- Refresh tokens are written to `.env` only; never logged or printed.
- The OAuth flow uses CSRF-protected `state` parameter on every request.
