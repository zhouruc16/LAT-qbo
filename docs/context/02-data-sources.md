# 02 — Data sources

## TikTok xlsx export — 6 sheets

Downloaded from TikTok Seller Center → Finance → Statements → Export.
One xlsx per quarter per storefront (the user exported by date range).

Files we have:
- `~/Downloads/1-3-2024.xlsx` — Q1 LELNU (Jan 1 – Mar 29 by stmt_date)
- `~/Downloads/4-6-2024.xlsx` — Q2 LELNU (Apr 1 – Jun 29)
- `~/Downloads/4-6-2024(1).xlsx` — re-export, identical 87 payments

### Sheet 1: Order details

Per-SKU rows. Wide schema — **115 columns**.

Key columns we use:
- `Statement date`, `Statement ID`, `Payment ID` — joins to other sheets
- `Order/adjustment ID`, `SKU ID`, `Product name`, `Quantity`
- `Order created date`, `Order shipment date`, **`Order delivery date`**
  (delivery date is what we group invoices by)
- `Type` — `Order`, `TikTok Shop reimbursement`, etc.
- **`Net sales`** — per-row net revenue (gross − refunds − seller discounts).
  Aggregating per-row is mathematically identical to `Statements.Net sales`.
- `Customer payment` — gross customer-paid amount (incl. shipping + tax)
- `Customer refund` — negative
- 60+ fee/discount/tax columns we don't need at row level

The Q1 file also has `Order details before 2024(UTC)`, `Statements before
2024(UTC)`, `Reports before 2024(UTC)` sheets — those are 2023 spillover
data. Ignore them.

### Sheet 2: Statements

**One row per daily statement.** This is the reconciliation source of truth.

Schema:
```
Statement date | Statement ID | Payment ID | Status |
Total settlement amount | Net sales | Shipping | Fees | Adjustments |
Reserve Amount | Payable amount
```

The xlsx's column header is `Reserve Amount` (capital A, capital A).
Don't read it as `Reserve amount` lowercase — that was a real bug we fixed.

`Reserve Amount` is **signed**:
- Negative = TikTok withheld this cycle (reserve asset goes up on LAT's books)
- Positive = TikTok released this cycle (asset goes down)

### Sheet 3: Payments

**One row per payout.** Schema:
```
Payment initiation date | Payment ID | Payment amount |
Payment completion date | Status | Bank account | Notes
```

`Notes` looks like `TikTok Shop-USLCPLELNU, payout ID 3459076539020317035`.
The storefront code is between `Shop-` and the comma. The payout ID
equals the `Payment ID` column.

`Payment completion date` is what TikTok says about when the ACH was
finalized on their side. Actual bank-credit date can be 0–3 calendar days
later (weekend/holiday). The pipeline uses Payment completion date for
JE.txn_date but reports actual bank date in the reconcile CSV.

### Sheet 4: Reserve details

Says: "Only reserve details after 2024/09/25 are displayed."

For H1 2024, this sheet is **empty**. We track reserve via the
`Reserve Amount` column on the Statements sheet instead.

### Sheet 5: Reports

Aggregate summary; informational only.

### Sheet 6: Fees explanation

TikTok's data dictionary; informational only.

---

## BofA business checking PDF

Format: `eStmt_YYYY-MM-DD.pdf`. Statement period is calendar month.

### Three ACH descriptor formats encountered

#### Format A — Standard TikTok (post 2024-01-16)
```
04/01/24  TikTok Inc DES:PAYMENT ID:000000702590890 INDN:Lat Group Inc  CO
          ID:9872667522 CCD PMT INFO:TikTok Shop-USLCPLELNU, payout ID 345903 0770353213803
                                                                                    10,151.32
```

- Date in MM/DD/YY at column x≈36
- TikTok PMT ID: `0000007XXXXXXXXX` (different from xlsx Payment ID)
- Storefront: between `Shop-` and `,`
- **Payout ID**: prefix `345903` + space + suffix `0770353213803`
  → joined = `3459030770353213803` = the xlsx Payment ID
- Amount: right-column at x≈525-540, same y as date row

#### Format B — Anomalous TikTok (one-off seen on 06-26-2024)
```
06/26/24  TikTok Shop DES:2406255002 ID:USLCPLELNU, INDN:Lat Group Inc CO ID:1473892853
          CCD PMT INFO:USLCPLELNU,
```

- DES code is `YYMMDD` + 4-digit suffix, no payout ID
- Storefront IS in `ID:USLCPLELNU` part
- Match by amount + date (fallback path)

#### Format C — HYPERWALLET (pre-2024-01-16)
```
01/02/24  HYPERWALLET SYST DES:MISC CRED ID: INDN:LAT GROUP INC  CO ID:1463389051 IAT
                                                                                    465.06
```

- TikTok's previous ACH provider
- No payout ID, no storefront in description
- Default storefront to LELNU (TikTok used HW only for that storefront in early-Jan)
- Match by amount + date (fallback path)

### Parser approach

`pdfplumber` for word-level x/y coordinates. The right-aligned Amount
column floats vertically relative to date column when extracted via
`pdftotext -layout`, so coordinate-aware parsing is required.

See `src/tiktok_qbo/ingest/bank_pdf.py`.

### Files we have

`~/Downloads/eStmt_2024-{01-31, 02-29, 03-29, 04-30, 05-31, 06-28, 07-31}.pdf`

July is included because end-of-June statements often pay out in early
July (1-day ACH lag + weekend skip).
