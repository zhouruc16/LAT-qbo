# RobotX — Transaction Classification Notes (2025–2026)

_Last updated: 2026-06-23. Companion to
`docs/superpowers/specs/2026-05-29-robotx-qbo-design.md`._

## Scope / status

- **2026 (Jan–Apr)** books already posted to **production QBO** (realm for
  the live RobotX company), reconciled.
- **2025 (May–Dec)** statements newly received (June 2026) — to be posted.
- Opening balances currently booked via **Opening Balance Equity at
  12/31/2025** (EWB $338,168.19 + Chase $124,998.00). This **collides** with
  posting 2025 activity (see "Opening-balance decision" below).

## Statement inventory (2025)

| Account | Coverage | Notes |
|---|---|---|
| **East West** 86-32006972 | May–Dec 2025 | Active Oct–Dec. May = account-opening activity. Jun–Sep dormant (just fees). |
| **Chase** …0108 | Nov 29 – Dec 31 2025 only | **Nov 1–28 statement missing** — contains the $680k Singularity deposit (deferred). |

Reconciliation: **all 2025 statements tie to the cent.** Dec endings match the
booked 12/31/2025 opening (EWB $338,168.19, Chase $124,998.00).

## Parser gaps — MUST FIX before posting

1. **EWB CHECKS section**: parser captures only the first check per date,
   misses the rest (8 checks across Oct–Dec, ~$15.4k).
2. **Chase "OTHER WITHDRAWALS" section**: not parsed at all (missed $2,500 +
   $423,001).
3. **`config/robotx_checks.yaml`** maps check `0` → "Robotx Inc". For 2025
   these are **payroll checks to employees** — mislabels payroll. Payee must
   come from the scanned check image, not this static map.

## Customer

**US Homecoin Group** = customer (buys robots). All Homecoin inflows (wires +
cashier's-check deposits) → **Sales**.

### Cashier's checks matched (all remitter = US Homecoin Group unless noted)

| Amount | Check date | Matches |
|---|---|---|
| $452,000 | 12/10/2025 | Chase deposit 12-10-2025 ✓ |
| $683,000 | 12/23/2025 | Chase deposit 12-23-2025 ✓ |
| $95,000 | 03/23/2026 | Chase deposit 03-23-2026 ✓ |
| $642,005 | 06/02/2026 | future (out of scope) |
| **$680,000** | 11/08/2025 | **Singularity Nuclear Energy Inc.** — no matching line (Chase Nov statement missing); deferred |

## Payroll (EWB checks — read from scanned check images)

Employees seen: **Po Jen Yang, Kayisaier Feinila, Xiaoyu Li, Binglin Xu,
Kelimu Miyamu** (and a "Selina M."). All → **Wages & Salaries**, payee =
employee.

Non-payroll checks found in the same section:
- **#163** $10,000 (12/18) → **Z & C CPAS LLP**, memo "Invoice #13029" →
  **Accounting / Professional Fees**.
- **#161** $353.00 (12/16), memo "Travel fee & Car Rental" → **Travel / Auto**.

## Boss explanations (resolves the entire 2026 unclear list)

| # | Date | Amount | Boss note (translated) | Books as |
|---|---|---|---|---|
| 1 | 03/23 IN | $95,000 | customer buying robots | **Sales** (Homecoin) |
| 2 | 03/25 IN | $497,550 | customer buying robots | **Sales** |
| 3 | 03/30 OUT | $497,000 | robot purchase | **COGS – Robots** ⚠️ confirm (wire was to *New American Title Co*, a title/escrow company) |
| 4 | 03/26 OUT | $2,500 | Sam paid third-party service fee | **Outside/Professional Services** |
| 5 | 04/03 OUT | $5,000 | Sam, third-party service fee | **Outside/Professional Services** |
| 6 | 04/13 OUT | $9,000 | Sam, third-party service fee | **Outside/Professional Services** |
| 7 | 03/31 OUT | $15,000 | 3 months rent | **Rent** (account …7880 = landlord) |
| 8 | 04/17 OUT | $109.52 | Sam business-trip reimbursement | **Travel** |
| 9 | 04/20 OUT | $269.36 | Sam business-trip reimbursement | **Travel** |

Facts derived:
- **"Sam"** handles cash → pays third-party service fees + travel. Cash
  withdrawals and "Robotx Inc" self-checks = business expense via Sam, **not**
  owner draws.
- **Account …7880 = landlord** (rent, ~$5,000/month).

## Classification rules (derived — apply to both years)

- Cash withdrawals / "Robotx Inc" self-checks → **Outside/Professional
  Services** (via Sam) — confirm per instance for large amounts.
- Transfers to **…7880** → **Rent**.
- POS "Shanghai Longqiao" → **Travel** (Sam).
- Outgoing wires to robot vendors (Newton Robotics, KEENON, Pudu, Dobot,
  Booster, Intbot, OpenLive, Thunder, YuShu/Unitree) → **COGS – Robots**.
- EDD EFTPMT / IRS USATAXPYMT → **Payroll Taxes**.
- Service Charge / Maintenance Fee → **Bank Service Charges**.
- Harland Clarke (check order) → **Office Supplies**.

## STILL UNRESOLVED — park in "Ask My Accountant" until boss answers

| Date | Amount | Item | Question |
|---|---|---|---|
| Dec 2025 | **$1,250,280** | EWB → East West acct **…6733** (3 transfers) | RobotX's own account? |
| Dec 2025 | **$626,501** | Chase → acct **…5527** (2 transfers) | Own account? |
| 12-30-2025 | **$423,001** | Chase "Withdrawal" (no description) | Where did it go? |
| 05-19-2025 | $250,000 | EWB check → "Robotx Inc" (self) | Purpose? |
| May 2025 | net $0 | EWB → Pershing LLC $250k, then reversed | What was it? |
| various 2025 | ~$1.65M | EWB deposits May $251k / Oct $50k / Nov $478,280 / Dec $150k+$472,060+$300k | likely customer sales — get cashier's checks |
| 11-08-2025 | $680,000 | Singularity Nuclear Energy deposit | need Chase Nov statement |

**Three questions that clear ~$2.3M:** is account **…6733** ours? is account
**…5527** ours? where did the **$423,001** withdrawal go?

---

## Implementation status (2026-06-23)

**Phase 1 — parsers — DONE.** `eastwest_pdf.py` now reads the complete CHECKS
summary table (no more de-dup-by-number drops); `chase_pdf.py` parses the
"Other Withdrawals" section. Every 2025 + 2026 statement reconciles to the
cent. Regression tests added.

**Phase 2 — classifier — DONE.** `classify.py` rewritten with a manual-override
table (identified Homecoin deposits → Sales; New American Title → COGS flagged;
Sam service fees; CPA / travel checks; May self-check parked) plus rules
(Homecoin/Algi sales, robot-vendor wires incl. Newton/Keenon → COGS, …7880 →
Rent, inter-bank EWB↔Chase transfers, payroll, taxes, fees, POS Shanghai →
Travel, Harland Clarke → Office Supplies). Decision: **correct both years.**

- 2025: 60 txns classify; total nets **$415,666.19** = EWB activity
  ($338,168.19) + Chase activity ($77,498.00). 16 items parked in "Ask My
  Accountant" (exactly the known-unresolved set).
- 2026: re-classified per boss notes (sale 6, purchase 9, transfer 6, tax 11,
  expense 28, payroll 33, owner_draw 3, unknown 2). Only COSCO $2,500 and the
  Pho Ha Noi $4,294.50 mobile check remain unidentified.
- New COA accounts needed at post time: **Outside Services**, **Travel**,
  **Professional Fees**.
- `load_2025()` added to `ingest/load.py`. 10/10 robotx tests pass.

**Phase 3 — sandbox — DONE & VERIFIED.** `scripts/post_robotx_2025.py` posted
all 60 2025 txns to the sandbox (deleted the combined `OPEN-BAL` JE, posted a
Chase $47,500 opening JE). `scripts/correct_robotx_2026.py` re-pointed the 9
boss-identified 2026 items out of "Ask My Accountant". Result: banks reconcile
to the cent (Chase $403,349.48 / EWB $5,526.52), 2025 P&L matches the
classification exactly, only the 18 genuinely-unresolved items remain parked.

**Phase 4 — production — DONE & VERIFIED (2026-06-24).** Key discovery: prod
already held the 2025 **Chase** activity, entered separately and classified
differently (the 3 big deposits → **Common Stock**, withdrawals → Owner's
Withdrawal / Uncategorized Expense). Per owner instruction *"leave existing
entries, add only what's missing"*:
- Deleted only the one `RX-OPEN-EWB` $338,168.19 placeholder; did NOT post a
  Chase opening JE (Chase already opened in prod).
- Added the **53 East West** 2025 transactions; **skipped the 7 Chase** ones
  already present (`SKIP_EXISTING` matches on bank+date+amount+direction).
- Left all Chase + 2026 entries untouched.
- Created accounts: Travel, Outside Services, Professional Fees. Prod account
  names differ from sandbox (mapped via `ACCT_ALIAS`: Sales of Product Income,
  Cost of Goods Sold, Bank Service Charge, Owner's Withdrawal).
- Banks reconcile exactly (Chase $403,349.48 / EWB $5,526.52). Re-run is a
  no-op (idempotent; pagination bug in the query helper fixed — QBO caps
  queries at 100 rows).

**Known inconsistency (accepted by owner, revisit later):** the Chase Homecoin
deposits ($452k + $683k) sit in **Common Stock** while the East West Homecoin
wires ($251k + $448k) are booked as **Sales**. Same customer, split between
equity and revenue. Left as-is per instruction; flag for the accountant.
Production still has the $680k Singularity deposit and a $632,500 withdrawal
(Nov 2025 Chase) that we have no statement for — already in Common Stock /
Owner's Withdrawal.

---

## May + June 2026 Chase …0108 (received 2026-07-02) — POSTED 2026-07-02

**Posted to production** via `scripts/post_robotx_2026_may_jun.py --commit`:
1 Homecoin sale (invoice+payment $642,005), 23 checks, 1 deposit. Chase
(BOA-0108) balance now **$748,500.63** = Jun-30 statement ending, to the cent.
Idempotent (re-run is a no-op). 7 unidentified items sit in Ask My Accountant
pending the boss — re-point them later with a small correction script.


New statements bridge the gap after the booked April-30 balance:
**Apr 30 $403,349.48 → May 29 $391,447.19 → Jun 30 $748,500.63**, continuous to
the cent. Inputs: `inputs/robotx_2025/chase-2026-05.pdf` and `chase-2026-06.pdf`.

**Parser fix:** `chase_pdf.py` now parses the **CHECKS PAID** section (was
silently dropped). June had 9 checks ($15,511.62), one column-merged into the
section `*end*` marker. Regression test `test_chase_2026_06_checks_paid_captured`.

**May (5 txns):** owner-draw CC $7,540.44, IRS $2,246.78, EDD $342.80, payroll
check 5262 $1,672.27, and **$100 Interactive Brokers** → parked.

**June (20 txns):** taxes (IRS/EDD), owner-draw CC $5,646, 9 payroll checks, and:

| Item | Amount | Booking |
|---|---|---|
| Deposit 06/02 | +$642,005 | **Sales** (US Homecoin — owner: buying robots) |
| Wire 06/10 → Accc Inc | −$15,000 | **Professional Fees** (owner: accounting firm) |

**Parked in Ask My Accountant (owner asking boss):**
- Deposit 06/26 **+$410,900** (no payer)
- Wire 06/02 **−$219,194** → China Merchants Bank, Shenzhen
- Wires 06/25 **−$24,000 + −$1,000** → Linkhome Realty Group, Irvine
- Wire 06/26 **−$33,801.34** → forex (Foreign Cur Bus Acct)
- **−$379,109** withdrawal (no description)
- May **−$100** Interactive Brokers

Payroll checks default to Wages & Salaries; payees not on statement (need images).
