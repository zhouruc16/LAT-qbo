# 06 — Known issues and edge cases

Each item below has bitten us at least once. The pipeline now handles them,
but a future agent should know they exist before touching adjacent code.

## I1: openpyxl `read_only=True` silently iterates 0 rows on Q1

**Symptom**: `read_payments(Q1_XLSX)` returned 1 row out of 89.

**Cause**: The Q1 xlsx has extra sheets (`Statements before 2024(UTC)`,
etc.) that confuse openpyxl's read-only streaming mode.

**Fix**: `lat_xlsx.py` now uses `load_workbook(path, data_only=True)`
without `read_only=True`. Larger memory footprint but works on all files.

**Don't**: re-add `read_only=True` "for performance".

## I2: `Reserve Amount` casing

**Symptom**: All reserve values silently 0 → reconcile mismatches looked
like a 5% systematic gap between settlement and bank deposit.

**Cause**: xlsx column is `Reserve Amount` (cap A); old code read
`"Reserve amount"` (lowercase). `to_money(None)` returned `0`.

**Fix**: ingest now reads `"Reserve Amount"` exact case.

**Don't**: change column-name lookup keys to lowercase. The xlsx is the
authority for casing.

## I3: Reserve sign convention

**Symptom**: `identity_2` failed when reserve was non-zero.

**Cause**: Original code used `payable = Net + Ship + Fees + Adj −
reserve`. The xlsx convention is `payable = Net + Ship + Fees + Adj +
reserve_signed` (where reserve_signed is negative for withholdings,
positive for releases).

**Fix**: `reconcile.check_identity_2` uses `+s.reserve_amount`.
`plan.statement_je.build_statement_jes` uses `_leg("reserve",
-reserve_signed)` to flip DR/CR based on sign.

**Don't**: introduce a "magnitude-positive reserve" convention. Stay with
the xlsx's signed convention end-to-end.

## I4: HYPERWALLET pre-2024-01-16 ACHs

**Symptom**: 11 early-Jan payments showed "no bank line found".

**Cause**: Before 2024-01-16, TikTok's ACH provider was HYPERWALLET. The
bank descriptor is completely different (`HYPERWALLET SYST DES:MISC CRED
ID: INDN:LAT GROUP INC`) and contains no payout ID.

**Fix**: `bank_pdf.py` recognizes HYPERWALLET as a TikTok line; falls back
to date+amount matching with a 4-day window. Default storefront set to
`USLCPLELNU` (TikTok used HW only for that storefront).

**If you see HYPERWALLET in a different period**: the user needs to
confirm which storefront(s) HW handled in that period.

## I5: Anomalous `TikTok Shop DES:` format on 06-26-2024

**Symptom**: Statement 06-25 (Payment $26,928.89) showed unmatched.

**Cause**: That single payout used a different ACH descriptor format:
`TikTok Shop DES:2406255002 ID:USLCPLELNU` — no payout ID.

**Fix**: `bank_pdf.py` recognizes this as a TikTok line; falls back to
date+amount.

**If you see this format growing in frequency**: TikTok may be migrating
to a new ACH format. Investigate; possibly extend `bank_pdf.py` to extract
storefront + a derived ID from the DES code.

## I6: xlsx export window gap

**Symptom**: 5 H1 bank deposits ($18,402.95 total) have payouts NOT
present in either Q1 or Q2 xlsx.

**Cause**: User's xlsx exports were quarterly with strict-period date
ranges. Late-March payouts (init dates 03/30, 03/31) fall outside both
exports because Q1 ended at 03/29 and Q2 started at 04/02.

**Mitigation**: Pipeline still posts everything we have; the 5 unmatched
bank lines appear in `reconcile-unmatched-bank.csv`.

**Real fix**: User needs to re-export Q1 with end date 04/03 (or Q2 with
start date 03/28). Not yet done.

**Recommendation when adding a new period**: Always export with ±5 days
overlap on either side.

## I7: Some Q1 bank deposits in Jan are unmatched bank-side

**Cause**: Some are Dec-2023 statements paying out in early Jan 2024.
Those statements aren't in any xlsx we have.

**Effect**: `total_payment_amounts > total_payable_statements` in H1
because some payment_ids reference outside-H1 statements. This is
correct accounting, not a bug.

## I8: BofA PDF amount column floats

**Symptom**: `pdftotext -layout` puts the amount on a different y-line
than the date row, depending on horizontal text.

**Fix**: Use `pdfplumber` for word-level x/y coords. Group by y (with
3-pixel tolerance), then find the amount in the same row as the date.

**Don't**: Try to parse the BofA PDF with pure-text approaches. They
break on the amount-column drift.

## I9: PowerShell + non-ASCII character emojis in Python strings

**Symptom**: `UnicodeEncodeError: 'charmap' codec can't encode character
'←' in position 31` when running tests on Windows console.

**Fix**: Use `PYTHONIOENCODING=utf-8 python ...` for all command-line
runs. Avoid em-dashes / arrows in `print()` calls.

## I10: User pasted real Client Secret in chat

**What happened**: User pasted production OAuth credentials directly into
chat. They are now in transcript logs.

**Mitigation**: Repeatedly told user to rotate. They have not yet
confirmed rotation. The exposed credentials are for an
"IN DEVELOPMENT" app whose production keys are still gated, so blast
radius is limited until production is unlocked.

**Always remind the user**: never paste secrets in chat. Use `.env`.
The pipeline reads `.env` locally; AI sessions never see the values.

## I11: User wrote `.env` in `Key: Value` format (colon, not equals)

**Symptom**: `python-dotenv could not parse statement starting at line 1`.

**Fix**: User must use `KEY=VALUE` format, no spaces around `=`, no quotes,
no colons. See `.env.example`.

**Don't**: try to auto-fix the user's .env. Tell them what's wrong and
let them fix it (we don't want to read or echo their secrets).

## I12: Negative-net statements don't deposit

**Symptom**: Some statement dates have no corresponding bank deposit.

**Cause**: When `Total settlement amount` is negative (refunds > sales for
the day), TikTok doesn't ACH a negative; they net the deficit into the
next positive payout.

**Effect**: 1 statement → 1 payment (1:1) most days, but sometimes
0 statements → next payment, or N statements → 1 payment.

**Handled correctly**: `check_identity_1` uses `Σ Payable per Payment ID`
which naturally accommodates the netting.
