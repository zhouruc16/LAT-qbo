# 04 — Decisions made (with rationale)

These are the accounting and engineering decisions baked into the pipeline.
Don't reverse them without an explicit reason. Each entry shows the
alternatives considered.

## D1: Net method for sales tax

**Decision**: Don't book customer-paid sales tax on LAT's books at all.

**Rationale**:
- TikTok is a marketplace facilitator in every relevant US state.
- Under ASC 606 principal-vs-agent analysis, LAT is the agent (not the
  principal) for the tax portion. LAT never has control of the funds.
- GAAP recommends Net for facilitator scenarios.
- Big-4 audit firms typically prefer Net for IPO-track companies.

**Alternative**: Gross method (book Sales Tax Payable, clear via deposit).
Adds a phantom liability that bounces through zero per payout. More moving
parts, more posting-error surface, no audit benefit.

**Override path**: If a CPA later requires Gross, the change is mechanical:
add a `Sales Tax Payable` line on the Bank Deposit JE, and increase invoice
amount to include `Sales tax payment`. No rebuild needed.

## D2: Path A — multi-line revenue (not lump-sum)

**Decision**: Invoice at `Net sales` (customer revenue only). Bank Deposit
JE has separate CR lines for shipping income, adjustments, and reserve
movements.

**Rationale**:
- IPO auditors want revenue broken down by source (customer-paid vs
  TikTok-paid platform subsidies).
- Cleaner P&L analytics.
- Works with the existing 4-component identity (`Net + Ship + Fees + Adj`).

**Alternative**: Path B — invoice at `Total settlement amount`, single
"Sales" line. Simpler but bundles platform subsidies into "sales", which
distorts revenue analysis.

## D3: One Customer record per storefront

**Decision**: Single QBO Customer `TikTok Shop LELNU` for all H1 invoices.

**Rationale**:
- We don't have buyer-level PII in the xlsx anyway.
- TikTok is the real payer; the end-buyer is anonymous from LAT's perspective.
- Avoids polluting QBO with thousands of synthetic customer records.
- Future YEWHX / CUEL4Y will get their own customer (parallel structure).

**Alternative**: One customer per end-buyer. Rejected for performance,
privacy, and lack of source data.

## D4: Invoice grouping by delivery date

**Decision**: One invoice per `(Statement ID, Order delivery date)`.
Multiple invoices per statement when orders span multiple delivery days.

**Rationale**:
- Revenue recognition (ASC 606) = when goods are delivered, not when
  TikTok settles cash. Using delivery date is GAAP-aligned.
- A single statement of 800 orders typically has 1–9 distinct delivery
  dates, so usually 1–9 invoices per statement.

**Alternative**: One invoice per statement (ignore delivery date variance).
Rejected because revenue would land on the wrong day.

**Alternative**: One invoice per order (1500+ for H1). Rejected — too noisy,
no analytical benefit over delivery-date grouping.

## D5: Match key = Payment ID, not date

**Decision**: Reconcile Payments ↔ bank by Payment ID (xlsx
`Payment ID` == bank `payout ID prefix+suffix joined`). Date used only as
a fallback for HYPERWALLET / anomalous formats.

**Rationale**:
- Date matching is fragile when multiple payouts hit the same day.
- Payment ID is unique and exact.
- HYPERWALLET fallback uses (amount, date_window=4) — sufficient because
  HYPERWALLET deposits are small and rarely collide.

## D6: Reserve as signed quantity (preserve xlsx convention)

**Decision**: Store `Reserve Amount` as-is from the xlsx. Negative =
withheld, positive = released.

**Rationale**:
- The xlsx is authoritative.
- Sign-flipping at ingest would create two conventions (xlsx vs internal)
  and risk drift.
- The JE generator uses `_leg("reserve", -reserve_signed)` so the helper
  flips DR/CR based on the resulting sign — which Just Works for both
  withhold and release.

**This was a real bug**: original `lat_xlsx.py` read `"Reserve amount"`
(lowercase) which silently returned None → 0. And original
`reconcile.identity_2` had `-s.reserve_amount` which would only tie when
reserve was magnitude-positive (an outdated assumption). Both fixed in
commit `99107dc`.

## D7: Sandbox first, production second

**Decision**: Build and verify in QBO Sandbox before touching production.

**Rationale**:
- The user's production keys are gated behind a 50-minute compliance
  checklist; not available immediately.
- Sandbox is free, instant, identical API surface.
- Posting 1500+ invoices into wrong accounts is hard to undo.
- Same code; swap credentials in `.env` for production.

**User initially asked**: "Just go production directly." We pushed back
because production keys weren't actually available, and the user agreed
to sandbox-first.

## D8: One xlsx-export per storefront, separate ingest runs

**Decision**: The pipeline runs one storefront at a time. Bank PDFs
contain all storefronts; we filter by storefront code per run.

**Rationale**:
- TikTok exports are per-storefront in Seller Center.
- Single-run-per-storefront keeps the pipeline simpler and idempotent.
- Per-storefront QBO accounts (`Sales - TikTok LELNU`,
  `Sales - TikTok YEWHX`, ...) keep books cleanly separable.

## D9: Idempotency via DocNumber lookup

**Decision**: Every QBO posting checks for existing DocNumber first;
skips if found.

**Idempotency keys**:
- Invoices: `INV-LELNU-<stmt_last8>-<delivery_yymmdd>`
- Journal entries: `JE-LELNU-<payment_id_last12>`

**Rationale**:
- Posts are safe to re-run after partial failures (network, rate limits).
- Re-running on the same period is a no-op.

## D11: Posting model = Invoice + CreditMemo + Payment + JE per statement

**Decision**: For each statement, post **four** entity types to QBO so that
both A/R and the Clearing account reach $0:

1. **Invoice** per (statement, delivery_date) for rows with positive Net_sales
   — DR A/R, CR Sales.
2. **CreditMemo** per (statement, delivery_date) for rows with negative Net_sales
   — DR Sales, CR A/R.
3. **Receive Payment** per **PAYMENT** (not per statement) for
   `TotalAmt = Σ statement.net_sales` across all bundled statements,
   `DepositToAccountRef = Clearing`, with `Line.LinkedTxn` referencing
   every Invoice (positive amount) and CreditMemo (also positive amount —
   QBO infers direction from `LinkedTxn.TxnType`) across all bundled
   statements — DR Clearing, CR A/R. **Why per-payment**: the 1 H1 case of
   a multi-stmt bundle (Known Issue I12 — TikTok rolls negative-net days
   into the next positive payout) would, under per-stmt RPs, produce
   negative-TotalAmt Payments which QBO rejects (I20). One RP per
   physical payout matches reality.
4. **JournalEntry** per Payment (xlsx-side) — DR Bank/Fees/Reserve, CR
   Clearing/Shipping/Adjustments. The CR Clearing leg uses `Σ statement.net_sales`,
   not `Σ sale-row Net_sales`.

After all four post: A/R = $0, Clearing = $0, books fully balance.

**Rationale**:
- Path A (D2) requires per-row revenue tracking for IPO analytics; this is
  the smallest set of entities that achieves correct GAAP posting for that.
- Receive Payment with linked Invoices/CMs marks invoices "Paid" in QBO UI
  (so the A/R aging report reads $0) — a simpler "JE-clears-A/R" approach
  would leave invoices visually open.
- Sign-of-Net_sales bucketing (rather than `classification == 'sale'`) is
  robust to non-sale rows that legitimately contribute revenue
  (e.g. `adjustment` rows TikTok issues for platform corrections).

**Alternative rejected**: a single per-payment JE handling everything
(no Invoice/CM/Payment), simpler but produces no per-customer A/R audit
trail and breaks IPO-grade analytics.

**Override path**: If the user later wants per-buyer customer records,
extend the Invoice grouping; everything else remains the same.

## D12: Idempotency DocNumber patterns

Each entity has a deterministic DocNumber so re-running `post_h1` on the
same period is a no-op:

| Entity | DocNumber | Length | Notes |
|---|---|---|---|
| Invoice | `INV-<stmt_last8>-<delivery_yymmdd>` | ≤19 | one per delivery date |
| CreditMemo | `CM-<stmt_last8>-<delivery_yymmdd>` | ≤18 | one per refund delivery date |
| Receive Payment | `PAY-<payment_last12>` | ≤16 | one per Payment ID (covers all bundled stmts) |
| JournalEntry | `JE-<payment_last12>` | ≤15 | one per Payment ID |

**21-char QBO limit**: QBO rejects DocNumbers longer than 21 chars
(`code 2050, ValidationFault`). Storefront identifier was originally
`-LELNU-` between the entity prefix and the stmt/payment tail (so
`INV-LELNU-<stmt8>-<yymmdd>` = 25 chars, over). It's now omitted because
the customer name + account names + PrivateNote already carry storefront
context. `test_all_doc_numbers_fit_qbo_21_char_limit` guards against
regression. See [`06-known-issues.md`](./06-known-issues.md) I19.

`post_h1` queries each by DocNumber before creating; a hit increments the
corresponding `*_skipped` counter on `PostStats`. Tests cover all four
idempotency paths.

## D13: Drop-shipping inventory model = QBO Non-Inventory items per SKU

**Decision**: LAT does not hold inventory (drop-shipping). Each SKU in the
Master Table becomes a QBO **Non-Inventory** Item; invoices and credit
memos use one line per SKU referencing those Items via `ItemRef`.

**Why Non-Inventory** (not Inventory or Service):
- **Inventory** items track `QtyOnHand` and require an Inventory Asset
  account. To stay at $0 ending inventory we'd need fake purchase receipts
  for every sale — mechanical, fragile, and contradicts the actual cash
  flow (no LAT-held stock).
- **Service** is what the pipeline used before (`ItemAccountRef` only, no
  Item record). Works, but gives QBO no product detail and shows everything
  as "Services" in the Products & Services list.
- **Non-Inventory** has Name + SKU + IncomeAccountRef but no quantity
  tracking. Inventory is structurally always $0 — the right shape for
  drop-shipping. Itemized lines give per-SKU revenue analytics.

**COGS**: Not booked per sale. COGS lands in the normal accrual workflow
when the supplier invoices LAT (DR COGS / CR A/P). The Master Table
`Price` column is unit cost reference data for the inventory report only;
the pipeline never reads it.

**Sentinel item**: `TikTok Platform Adjustment` (also Non-Inventory) is
created once and absorbs invoice/CM revenue from rows lacking a real SKU
(TikTok platform-adjustment rows that legitimately contribute to Net sales).
Without it, a no-SKU row would break invoice balancing.

**Trial balance impact**: None. Per-line totals still sum to `Σ Net sales`
per (statement, delivery_date), so A/R, Clearing, Sales, and the bank-side
JE are unchanged. The
`test_post_h1_with_item_refs_preserves_trial_balance` test guards this.

**Bootstrap source**: SKU name resolution order:
1. Master Table `Product name` (authoritative).
2. Most-voted `Product name` from order rows (fallback when SKU is
   missing from Master Table — auto-creates with that name).
3. `SKU <id>` as last resort.

**Idempotency**: `bootstrap_items` queries QBO `Item WHERE Sku = ...`
before creating; re-runs are no-ops. Name collisions disambiguated by
appending ` (<sku last8>)` to the second occurrence.

**Override path**: `python scripts/post_h1_2024.py --no-items` keeps the
legacy single-summary-line shape, which is what already-posted sandbox
invoices use.

## D10: Repository = private GitHub `LAT-qbo`

**Decision**: Code committed and pushed to
`https://github.com/zhouruc16/LAT-qbo` (private).

**Excluded from git**:
- `.env` (credentials)
- `state*/` (intermediate JSONL + reconciliation CSVs)
- `inputs/` (raw xlsx/pdf inputs)
- `__pycache__`, `.pytest_cache`, `dist/`, `build/`
