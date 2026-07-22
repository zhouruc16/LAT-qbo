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

## I13: JE clearing leg used sale-row sum instead of statement.net_sales

**Symptom**: 109 of 176 H1 JEs (62%) imbalanced by exactly the refund-row
total for that statement. QBO would have rejected each at post time.
Discovered during sandbox-first dry-run review (2026-04-29).

**Cause**: `post_h1` summed `Σ sale-row Net_sales` per delivery group via
the return value of `post_statement_invoices` and passed that to
`build_je_body` as the JE's CR Clearing amount. But sale rows were filtered
upstream via `classification == 'sale'`, excluding refund rows whose
negative Net_sales already net into `statement.net_sales`. The xlsx-side
`Statements.Net_sales` is authoritative and includes refunds.

**Fix**: `post_h1` now passes `Σ statement.net_sales` to `build_je_body`.
The math identity `Bank + |Fees| + |Reserve_w| = Σ net + Ship_pos + Adj_pos +
Reserve_released` then holds for every payment.

**Why the existing test missed it**: `test_je_balances_for_all_h1_payments`
called `build_je_body(...)` directly with `money_sum(s.net_sales for s in
group)` — i.e. the correct value, never the buggy sale-row-sum value that
production used. New test `test_post_h1_production_path_every_je_balances_for_real_h1`
runs the actual `post_h1` against a recording client and asserts every JE
balances; this is the real regression guard going forward.

## I14: Sale rows without `order_delivery_date` skipped from invoicing

**Symptom**: Trial-balance test surfaced a $1,528.85 residue across H1 due
to 26 sale rows that legitimately have non-zero `Net sales` but no
`Order delivery date` populated. Original code filtered them out with
`if r.order_delivery_date is None: continue`, so they contributed to
`statement.net_sales` but no invoice was created for their value.

**Fix**: `post_h1` falls back to `r.statement_date` when delivery_date is
null. These rows roll into a statement-dated invoice, which still nets
correctly into A/R and gets cleared by the Receive Payment.

**Don't**: drop rows just because delivery_date is null — they may carry
real revenue.

## I15: `adjustment`-classified rows with non-zero Net_sales

**Symptom**: 2 H1 rows classified as `adjustment` (TikTok-side platform
adjustments) had Net_sales of +$55 and +$68 totaling +$123. Original code
ignored them entirely (only invoiced `classification == 'sale'`), so they
contributed to `statement.net_sales` without being invoiced.

**Fix**: `post_h1` now buckets rows by **sign of Net_sales**, not
classification. Any row with positive Net_sales → invoice; any row with
negative Net_sales → CreditMemo. Adjustments naturally land in the right
bucket regardless of their classification label.

**General principle**: when a row carries a non-zero `Net_sales` value, it
contributes to `statement.net_sales` and must be reflected somewhere in
the QBO posting. Sign-based bucketing is the robust default.

## I16: `python -m tiktok_qbo` lacked a `__main__.py`

**Symptom**: `python -m tiktok_qbo auth` (as documented in this guide)
failed with `'tiktok_qbo' is a package and cannot be directly executed`.
Discovered during the first sandbox OAuth attempt.

**Fix**: added `src/tiktok_qbo/__main__.py` that delegates to
`tiktok_qbo.cli.main`. Both `python -m tiktok_qbo <cmd>` and
`python -m tiktok_qbo.cli <cmd>` now work.

**Editable install requirement**: 3 CLI tests (`tests/test_cli.py`) spawn
`python -m tiktok_qbo.cli` as a subprocess; they fail without `pip install -e .`
because the subprocess doesn't inherit the parent's `PYTHONPATH=src`. After
editable install they all pass.

## I21: Receive Payment must be per-PAYMENT, not per-STATEMENT

**Symptom**: Stress-testing the 1 H1 multi-statement payment
(`3459043172707176811`, 4 statements bundled) revealed that 2 of those
statements were refund-only (negative net_sales). Per-statement Receive
Payments tried to post `TotalAmt = -454.30` and `-225.92`, both rejected
by QBO with `code 2240` (per I20).

**Cause**: Original design emitted one RP per statement. For statements
that contain only refund rows (no positive net_sales), the RP body had
`TotalAmt = 0 - cm_total = negative`. QBO rejects negative TotalAmt.

**Why this only surfaced on the 4-stmt payment**: TikTok rolls
negative-net days into the next positive payout (I12). For the typical
1-stmt-per-payment case (175 of 176 H1 payments), each statement's net
is positive on its own and there's no problem. The 1 multi-stmt payment
in H1 is the only case that bundles negative-net stmts with a positive
net stmt.

**Fix**: Refactored to one Receive Payment per **Payment ID** (not per
statement). The RP applies to all invoices + CMs across all bundled
statements with `TotalAmt = Σ statement.net_sales`, which is positive
for every payment in H1 (verified by
`test_post_h1_no_payment_has_negative_total_amount_in_h1`). DocNumber
pattern changed from `PAY-<stmt_last8>` to `PAY-<payment_last12>`.

**Side benefit**: One RP per actual TikTok payout matches the real-world
data flow more naturally and reduces total entity count.

**Migration note for sandbox**: any `PAY-<stmt_last8>` documents from
before this refactor become orphans; manually delete in QBO before
re-running the same period (or wait for production where everything
starts fresh).

## I20: QBO Payment.Line.Amount must be non-negative

**Symptom**: First real-mode Payment POST returned 400 with
`code 2240, ValidationFault: "Number out of range. Min:0 Max:999,999,999.
Supplied value:-44.94"`. Discovered during Stage F.6 retry after I19 fix.

**Cause**: A common (incorrect) reading of QBO docs suggests that applying
a CreditMemo to a Payment uses a negative `Line.Amount`. Wrong: QBO
infers the application direction from `LinkedTxn.TxnType` and rejects
any negative `Line.Amount`. Both Invoice-link and CreditMemo-link lines
must have **positive** Amount values.

**Math**: `Payment.TotalAmt = Σ Invoice-Line.Amount − Σ CreditMemo-Line.Amount`.
QBO does the subtraction internally based on TxnType.

**Fix**: `build_receive_payment_body` now emits positive Line.Amount for
both link types. Test
`test_post_h1_receive_payment_links_all_invoices_and_credit_memos` asserts
all Line.Amount values are non-negative AND
`TotalAmt == Σ inv_amounts − Σ cm_amounts`.

**Don't**: pass negative Line.Amount values for any Payment link, even
if you think you're "reducing" a receipt — the API rejects it.

## I19: QBO DocNumber max length is 21 chars

**Symptom**: First real-mode invoice POST returned 400 with
`code 2050, ValidationFault: "Min:0 Max:21 supported. Supplied length:25"`.
Discovered during Stage F.6 (single-statement real post) on 2026-04-29.

**Cause**: Original DocNumber format was
`INV-LELNU-<stmt_last8>-<delivery_yymmdd>` (25 chars) and
`CM-LELNU-<stmt_last8>-<delivery_yymmdd>` (24 chars), both over the
limit. The 21-char limit applies to all QBO entity types (Invoice,
CreditMemo, Payment, SalesReceipt, JournalEntry, Bill, …).

**Fix**: Storefront identifier dropped from DocNumber. New format:
`INV-<stmt_last8>-<delivery_yymmdd>` etc. Storefront context is preserved
in the customer name (`TikTok Shop LELNU`), account names
(`Sales - TikTok LELNU`), and the PrivateNote on every entity.
`test_all_doc_numbers_fit_qbo_21_char_limit` guards against regression.

**Don't**: re-add storefront codes to DocNumber when adding new
storefronts (YEWHX, CUEL4Y). Use ClassRef or a separate Customer per
storefront instead — keeps DocNumber inside the limit.

## I18: Late-period boundary statements paid out after period-end

**Symptom**: Running `post_h1` on full H1 surfaces 1+ entries in
`stats.errors` of the form `"no Payment row for payment_id=...; skipping"`.

**Cause**: `scripts/post_h1_2024.py` filters statements to those with
`statement_date <= 2024-06-30` and payments to those with
`payment_initiation_date <= 2024-06-30`. A statement dated 2024-06-29
that pays out on 2024-07-01 has its statement included but its payment
filtered out — so `post_h1` can't find the payment row and skips the
statement entirely.

**Mitigation**: post_h1 reports these in `stats.errors` (visible in the
driver-script output). The statements themselves don't get posted to
QBO until the user re-runs in the next period.

**Real fix**: extend the period filter to either include statements only
when their payment is also in scope, or extend the payment filter by a
few days past period-end (similar to how the bank PDF parser already
includes July statements for late-June payouts).

## I17: QBO entity response keys aren't always title-case-of-path

**Symptom**: dry-run `qbo-init` and the Recording test client both
returned the wrong response key (`Creditmemo`, `Journalentry`) when the
real QBO API returns `CreditMemo`, `JournalEntry`. Caused dry-run to
report 0 IDs captured, breaking Receive Payment LinkedTxn.

**Fix**: `tiktok_qbo.qbo.client._qbo_entity_key()` maps URL paths to QBO's
CamelCase response keys. Single-word entities (`invoice`, `customer`,
`payment`, `account`) fall through to title-casing.

## load_creds() with two companies in one process (fixed 2026-07-21)

`load_env()` called `load_dotenv(explicit_path, override=False)`. Because
`override=False` never replaces vars already in `os.environ`, the SECOND
`load_creds(Path(".env.other"))` in the same Python process silently kept
the FIRST company's realm + refresh token — queries went to the wrong
company with no error. Found while pulling RobotX and RobotX AI P&Ls in
one script (both returned RobotX's numbers). Fixed: explicit path now
loads with `override=True`. If you ever see two companies returning
identical data from one process, suspect this class of bug first.
