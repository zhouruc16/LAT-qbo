# Notes for future Claude sessions

This is a working production pipeline. Don't refactor structure without reason.

## Mental model (read first)

The TikTok xlsx export has 6 sheets; only 4 matter:

1. `Order details` — per-SKU rows (~thousands per quarter). Used to split
   each statement into per-delivery-date invoices.
2. `Statements` — one row per statement (daily). Has the rollup formula:
   `Net sales + Shipping + Fees + Adjustments = Total settlement amount`,
   plus `Reserve Amount` and `Payable amount`. **This is the
   reconciliation source of truth.**
3. `Payments` — one row per payout. Joins to `Statements` by `Payment ID`.
4. `Reserve details` — only populated after 2024-09-25; ignore for H1 2024.

The TikTok-internal identities (always hold):

```
Net sales + Shipping + Fees + Adjustments  =  Total settlement amount
Total settlement amount + Reserve Amount    =  Payable amount
Σ Payable amount per Payment ID            =  Payments.Payment amount
```

`Reserve Amount` is **signed**: negative = withheld this cycle, positive =
released. Don't flip the sign at ingest — preserve the xlsx convention.

## Bank PDF parsing (BofA business checking)

Three ACH descriptor formats encountered:

1. `TikTok Inc DES:PAYMENT ID:000000XXXX...payout ID NNNNNN NNNNNNNNNNNNNNN`
   — standard format, `payout_id = prefix + suffix` joined → equals
   `Statements.Payment ID`.
2. `TikTok Shop DES:YYMMDDHHHH ID:USLCPLELNU` — anomalous; one-off seen on
   06-26-2024. No payout ID; match by amount + date.
3. `HYPERWALLET SYST DES:MISC CRED ID: INDN:LAT GROUP INC` — TikTok's
   pre-2024-01-16 ACH provider. No payout ID; match by amount + date with
   default storefront `USLCPLELNU`.

Use `pdfplumber` for word-level coordinates; the right-column amount stream
floats relative to the date column in `pdftotext -layout` output.

## Don't do

- Don't use `read_only=True` on `openpyxl.load_workbook` for the Q1 xlsx
  — it silently iterates 0 rows due to extra-sheets metadata in the file.
- Don't try to derive `Net sales` from raw Order-detail columns at the
  statement aggregate level — it's already correct in the per-row data.
  Just `df.groupby('Statement ID')['Net sales'].sum()`.
- Don't post sales tax to QBO. TikTok is a marketplace facilitator;
  `Sales tax payment` and `Sales tax refund` columns are pass-through and
  don't touch LAT's books (Net method).
- Don't use date-based matching as the primary key. Use Payment ID. Date
  windows are for the fallback path only (3-day window for HYPERWALLET).
- Don't generate JEs with a "balancing plug" CR Clearing leg. Use Σ Net
  sales as the explicit clearing amount; the JE balances naturally
  because `Net + Shipping + Fees + Adj + Reserve = Payable`.

## QBO posting

Idempotency keys (all DocNumbers):
- Invoices: `INV-LELNU-<stmt_last8>-<delivery_yymmdd>`
- Journal entries: `JE-LELNU-<payment_id_last12>`

Before any POST, the code queries by DocNumber and skips if found. Safe to
re-run as many times as needed.

The QBO API uses `Decimal` amounts as JSON numbers (cast to `float`); 2-decimal
precision is implicit in QBO's currency handling. Don't try to pass
`Decimal` objects directly — `requests.json` won't serialize them.

## Reserve sign in JEs (signed convention)

```
reserve_amount < 0  (withheld this cycle):  DR TikTok Reserve  |reserve|
reserve_amount > 0  (released this cycle):  CR TikTok Reserve   reserve
```

The `_leg(role, amount)` helper handles sign automatically: pass
`-reserve_amount` and let it flip to DR/CR based on result sign.

## Common edge cases

- **Negative statements** (refunds > sales for the day): `Total settlement
  amount` is negative. TikTok rolls into the next positive payout — no
  ACH on that day. The xlsx already nets this correctly via the next
  payment's payable amount.
- **5 in-H1 unmatched bank lines**: late-March payouts whose statement is
  outside both Q1 and Q2 xlsx export windows. Solution: ask user to
  re-export with widened date window.
- **Pre-2024-01-16 HYPERWALLET**: TikTok used a different ACH provider.
  Bank descriptor is completely different but funds are the same. The
  fallback matcher handles this transparently.

## Useful commands

```bash
# Reconciliation only — works without QBO credentials
python scripts/reconcile_h1_2024.py

# Run all tests including the H1 integration test
pytest tests/ -q

# Single-statement preview (after QBO setup)
python scripts/post_h1_2024.py --dry-run --payment-id 3459076539020317035
```
