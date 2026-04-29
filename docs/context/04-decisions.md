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

## D10: Repository = private GitHub `LAT-qbo`

**Decision**: Code committed and pushed to
`https://github.com/zhouruc16/LAT-qbo` (private).

**Excluded from git**:
- `.env` (credentials)
- `state*/` (intermediate JSONL + reconciliation CSVs)
- `inputs/` (raw xlsx/pdf inputs)
- `__pycache__`, `.pytest_cache`, `dist/`, `build/`
