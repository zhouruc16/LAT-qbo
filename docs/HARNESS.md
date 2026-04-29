# Pipeline harness — operational guide

This document walks through running the pipeline end-to-end on real data,
including how to add a new storefront or a new period.

## Prerequisites

- Python 3.11+
- Intuit Developer account with a QBO app (sandbox or production keys)
- A QBO company (sandbox auto-created, or your real production company)
- `pip install -e ".[dev]"`

## Inputs

For each storefront × period:

1. **TikTok xlsx export** (per quarter): from Seller Center → Finance →
   Statements → Export. Sheets used: `Order details`, `Statements`,
   `Payments`, `Reserve details`.
2. **BofA PDF statements** (monthly): from Bank of America online banking →
   Statements & documents → eStmt_YYYY-MM-DD.pdf. The pipeline parses all
   storefronts but filters by storefront code (e.g., `USLCPLELNU`).

Export the xlsx with **a wider date window than the period you actually
care about**. Late-month statements often pay out in the next month, so a
strict-period export creates "edge" records that don't reconcile.
Recommended: ±5 days on either side.

## Workflow (LELNU H1 2024 example)

### Phase 1: ingest + reconcile (no QBO needed)

```bash
python scripts/reconcile_h1_2024.py
```

This loads `1-3-2024.xlsx`, `4-6-2024.xlsx`, all 7 BofA PDFs from
`~/Downloads`, filters to LELNU storefront, runs both internal identities,
joins to bank lines, and writes:

- `state_h1/reconcile-summary.csv` — per-Payment-ID matched amounts
- `state_h1/reconcile-unmatched-bank.csv` — unmatched bank lines
- `state_h1/reconcile-h1-summary.txt` — headline numbers

If `Identity_1 mismatches` or `Identity_2 mismatches` is non-zero, **stop
and investigate before proceeding**. The xlsx data is corrupt or your
filter is wrong.

If `payments matched` < total payments, look at the unmatched payments —
they're either pre-period (different bank account, like HYPERWALLET) or
the bank PDFs don't cover the relevant date.

### Phase 2: QBO authorization

```bash
python -m tiktok_qbo auth
```

Browser opens. Log into the QBO company you want to write to, click
Authorize. The callback writes refresh token and realm ID into `.env`.

The refresh token is good for 100 days (Intuit rotates it on every API
call). If it expires, just re-run `auth`.

### Phase 3: bootstrap COA

```bash
# Preview accounts that will be created (no writes):
python -m tiktok_qbo qbo-init --dry-run

# Create them:
python -m tiktok_qbo qbo-init
```

The bootstrap is idempotent — looks up each account/customer by name and
only creates if missing.

You'll see output like:
```
[exists] BofA Checking (Id=35)
[created] TikTok Clearing - LELNU (Id=82)
[created] Sales - TikTok LELNU (Id=83)
...
```

### Phase 4: dry-run posting

```bash
python scripts/post_h1_2024.py --dry-run
```

Builds invoice + JE payloads for all 176 LELNU payments without sending
any. Output shows what would be created/skipped. Inspect the
`stats.errors` list — should be empty.

### Phase 5: single-payment preview

```bash
python scripts/post_h1_2024.py --payment-id 3459076539020317035
```

This actually posts ONE statement to QBO. Open QBO and verify:

- Invoices appear under Customer "TikTok Shop LELNU"
- Each invoice DocNumber matches `INV-LELNU-<stmt>-<delivery>`
- The corresponding journal entry exists with DocNumber `JE-LELNU-…`
- The JE balances (DR = CR)
- BofA Checking received the deposit amount

If anything looks wrong, **delete the test invoices and JE in QBO before
running the full post** — otherwise the idempotency check will skip them.

### Phase 6: full H1 post

```bash
python scripts/post_h1_2024.py
```

Posts ~1500 invoices + 176 JEs across H1 2024. Takes ~30 minutes (rate
limited by QBO API). Idempotent — safe to re-run if interrupted.

### Phase 7: verify in QBO

After completion, in QBO:

- Trial balance: `TikTok Clearing - LELNU` should be **$0** (all cleared)
- A/R aging by customer: `TikTok Shop LELNU` should be **$0**
- BofA Checking should equal **$1,714,496.90** in TikTok credits for H1
- `Sales - TikTok LELNU` should equal **$1,690,241.20** (Net sales total)

## Adding a new storefront (YEWHX example)

1. Export YEWHX xlsx from Seller Center for same period.
2. Update `scripts/post_h1_2024.py`:
   - Change `STOREFRONT = "USLCPYEWHX"`
   - Update `Q1_XLSX` / `Q2_XLSX` paths to YEWHX exports
3. Update `src/tiktok_qbo/qbo/coa.py`:
   - Add YEWHX-suffixed accounts (`Sales - TikTok YEWHX`, etc.)
   - Or rename to use one set per storefront if you want fully separate
     ledgers
4. Re-run phases 1–7 with new storefront.

## Adding a new period (Q3 2024)

1. Export Q3 xlsx (`7-9-2024.xlsx`).
2. Download August + September BofA PDFs.
3. Copy `scripts/post_h1_2024.py` → `scripts/post_q3_2024.py` with updated
   constants and date range.
4. Run the same 7 phases.

The COA bootstrap is idempotent — won't re-create accounts. Idempotency
keys (`INV-LELNU-...`, `JE-LELNU-...`) are unique per statement so Q3
won't collide with H1.

## Troubleshooting

### `python-dotenv could not parse statement`

Your `.env` is in the wrong format. Required: `KEY=VALUE` per line, no
spaces around `=`, no quotes, no colons. See `.env.example`.

### `QBO 401 on query`

Refresh token expired. Run `python -m tiktok_qbo auth` again.

### `QBO 400: Duplicate Document Number`

Idempotency check missed an existing record (different DocNumber pattern,
or got created with the same DocNumber outside the pipeline). Inspect
the existing record in QBO and decide whether to delete or change the
pipeline's DocNumber pattern.

### `payments matched: 164/176`

Some payments didn't reconcile to bank. Check `reconcile-unmatched-bank.csv`
and the `Unmatched payments` section in the summary. Common causes:
- Different bank account in early period (HYPERWALLET — already handled)
- xlsx export window doesn't cover the actual payout dates
- Bank PDF for the payout month is missing

### Identity_2 mismatches > 0

The xlsx Statements sheet's rollup formula doesn't tie out. This is rare;
likely indicates a corrupt or modified xlsx. Re-download from Seller
Center.

### `No Bank account found in QBO`

You need to manually create a Bank-type account in QBO first (Settings →
Chart of Accounts → New → Bank). Name it something containing "BofA" or
"9247" so the bootstrap finds it.

## File layout

```
.env                         # Your local credentials (gitignored)
.env.example                 # Template
src/tiktok_qbo/              # Pipeline code
scripts/                     # Per-period drivers
tests/                       # 54 tests
state_h1/                    # H1 2024 reconciliation outputs (gitignored)
state_q1/, state_q2/         # Per-quarter ingest state (gitignored)
docs/HARNESS.md              # This file
docs/superpowers/            # Original design docs
README.md                    # Top-level overview
CLAUDE.md                    # Notes for AI sessions
```
