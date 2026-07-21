# 07 — Current state and next steps

## LAT Group Inc — PRODUCTION QBO connected + 2026 bank books (2026-07-20)

At user's explicit direction: LAT's **production** company (realm
9341456923092840) is now connected via `.env.lat.prod` (same app keys,
OAuth-Playground flow). This is separate from the sandbox `.env` used by
the TikTok settlement pipeline — the pipeline itself has NOT been run
against production.

- Source: BofA checking CSV export Jan–Jun 2026 (395 txns; from user's
  WeChat files, path hardcoded in `scripts/post_lat_2026.py`). Verified:
  beginning $24,568.71 + net $19,092.83 = ending $43,661.54; running-
  balance chain unbroken; credits/debits match the file's own summary.
- Production company had QBO's default CoA (153 accounts) and ZERO
  transactions before posting.
- `scripts/post_lat_2026.py --commit` posted (zero errors, idempotent
  tag `LAT26:<row>:<hash>`): opening JE `LAT26-OPEN` ($24,568.71 →
  Opening balance equity), 213 Deposits, 182 Purchases. QBO
  `BofA Checking` CurrentBalance = $43,661.54 ✅.
- 43 external-transfer fees → existing "Bank fees & service charges";
  the other 352 txns parked in **Ask My Accountant** with `[group]`
  memos. Owner review sheet:
  `LAT_Unclarified_Transactions_Jan-Jun2026.xlsx` (38 counterparty
  groups; one answer per group).
- ⚠️ TikTok payouts (173×, $865,226.12) are parked in AMA as deposits.
  If/when the settlement pipeline posts proper 2026 invoices+payments to
  production, these must be reclassified to a clearing account, NOT
  income, or revenue double-counts. See 04-decisions.md D1/D7 context.
- Next: owner fills the group sheet → batch-reclassify (pattern:
  `scripts/clear_robotxai_ama.py`).

## Robotxai (RobotX AI Inc.) — third QBO company (2026-07-17)

Same Intuit app keys as LAT/RobotX; own realm + refresh token in
`.env.robotxai.prod` (production realm 9341457535561531, connected via the
OAuth-Playground flow using `finish_robotx_prod.py --env .env.robotxai.prod`;
this company's QBO admin is a DIFFERENT Intuit login — that's fine, OAuth
apps are per-app, authorization is per-company-admin).

- Source: 9 Chase business-checking `…3205` PDFs, Oct 2025–Jun 2026, in
  `inputs/robotxai/` (gitignored). All 9 tie beginning+net=ending to the
  cent and chain month-to-month ($0.00 open → $190,406.26 end Jun 2026).
- These statements have a `*start*fees section` block that RobotX's never
  had — `chase_pdf.py` now parses it (kind="fee"); 2 tests in
  `tests/robotx/test_chase_fees_section.py`.
- `scripts/post_robotxai_2026.py` posted all 38 txns to production
  (2026-07-17, zero errors, idempotent tag `RXAI:<txn_id>`). QBO
  `Chase Checking - 3205` CurrentBalance = $190,406.26 ✅.
- 2026-07-20: owner explained 27 of 29 AMA items;
  `scripts/clear_robotxai_ama.py --commit` re-pointed them (bank balance
  unchanged):
  - ALL money in → **Shareholder Investment** (Equity, $2,798,954.52)
  - transfers to `…6591` → Research & Development ($1,495,250)
  - transfers to `…3782` → Listing Expenses ($1,100,190)
  - checks 4994-4997 → Employee Expense Reimbursement ($4,837.72)
  - 2× Corp E Corp $450 → Taxes & Licenses (corporate tax filing fees)
  - Zelle to Benny Xu $703.63 → Travel (business trip)
- 2026-07-20 round 2: **Ask My Accountant is now $0 / empty.**
  - $5,281.69 CC `…7856` payment = paying RobotX Inc's card →
    **Due from RobotX Inc.** (Other Current Asset, intercompany receivable).
    Mirrored in RobotX Inc's books 2026-07-20: JE `RX-CC-EXTPAY-0102`
    credit re-pointed Owner's Withdrawal → **Due to RobotX AI Inc.**
    (Other Current Liability) via `scripts/fix_robotx_cc_intercompany.py`.
    The two companies' intercompany accounts now mirror at $5,281.69.
  - Check 4993 $1,170.22 → Employee Expense Reimbursement (owner).

## Status as of last session

### ✅ Completed

- **Phase 1: Ingest** — Both Q1 and Q2 xlsx parse cleanly (50,540 order
  rows, 179 statements, 176 payments).
- **Phase 1: Bank PDF parser** — All 7 BofA PDFs (Jan–Jul 2024) parsed.
  Three ACH formats handled.
- **Phase 1: Reconciliation** — 176/176 H1 LELNU payments matched to
  bank lines, $0.00 amount diff. All identity checks pass.
- **Phase 2: QBO API code** — OAuth flow, REST client with token refresh,
  COA bootstrap, invoice + JE posting code. All written, tested.
- **Tests**: 84 pass (63 Path B baseline + 9 items bootstrap + 8 per-SKU
  invoice/CM line shape + 4 misc). Includes
  `test_post_h1_trial_balance_nets_to_zero_per_statement` that runs the
  full H1 posting flow against a recording client and asserts the resulting
  trial balance nets to zero (Clearing=$0, A/R=$0,
  Sales=-Σ statement.net_sales). New
  `test_post_h1_with_item_refs_preserves_trial_balance` guards that
  switching to per-SKU lines doesn't change account totals.
- **Inventory model** (D13): Drop-shipping → Non-Inventory items per SKU.
  Bootstrap in `qbo/items.py` reads Master Table_updated.xlsx and creates
  one Item per SKU (plus a "TikTok Platform Adjustment" sentinel for
  no-SKU revenue rows). Invoices and credit memos now have one line per
  SKU with `ItemRef + Qty + UnitPrice`. Same totals, same accounts, same
  trial balance.
- **Documentation**: README, HARNESS guide, CLAUDE.md, this context dir.
- **Repository**: Pushed to https://github.com/zhouruc16/LAT-qbo (private).

### ⏳ Blocked / waiting on user

(All previous blockers resolved as of session 2 on 2026-04-29:
`.env` reformatted, Client Secret rotated again to Development tab,
OAuth completed, sandbox COA bootstrapped, Path B refactor of post.py
completed and 63/63 tests pass. Stage F.5 onward is ready.)

### 🚧 Next agent should do

Stages F.1-F.4 already completed in session 2:
- ✅ `.env` parses cleanly via `load_creds()` (`environment=sandbox`)
- ✅ OAuth completed; realm_id `9341456983696946` written to `.env`
- ✅ `qbo-init --dry-run` and real `qbo-init` both completed; 7 accounts
  + 1 customer created in sandbox QBO. Account IDs:
  ```
  TikTok Clearing - LELNU         Id=1150040000
  TikTok Reserve - LELNU          Id=1150040001
  Sales - TikTok LELNU            Id=1150040002
  Shipping Income - TikTok        Id=1150040003
  TikTok Adjustments              Id=1150040004
  Marketplace Fees - TikTok       Id=1150040005
  Shipping Expense - TikTok       Id=1150040006
  Customer "TikTok Shop LELNU"    Id=58
  Bank "Checking" (sandbox default) Id=35  ← used as BofA stand-in for sandbox
  ```

Remaining steps (resume here):

5. **Single-payment preview** (now includes item bootstrap step):
   ```bash
   python scripts/post_h1_2024.py --dry-run --payment-id 3459076539020317035
   ```
   Builds Items + invoice/CM/Payment/JE payloads but doesn't POST. User
   reviews JSON. The new `[3.5/4] Bootstrapping Non-Inventory items` step
   will query QBO for existing Items by Sku; in dry-run mode it returns
   synthetic IDs for missing SKUs so the invoice build can proceed.
   Pass `--no-items` to skip itemization and keep the legacy
   single-summary-line shape.

6. **Single-payment real**:
   ```bash
   python scripts/post_h1_2024.py --payment-id 3459076539020317035
   ```
   Posts ONE statement (05-02-2024). User verifies in QBO sandbox UI.

7. **Full H1 dry-run**:
   ```bash
   python scripts/post_h1_2024.py --dry-run
   ```
   Validates all 176 payouts can be built without errors. Expected
   counts after Path B refactor + D13 inventory: ~300 Items (one per
   unique SKU + sentinel), ~1700 invoices, ~80 credit memos (one per
   refund delivery date), ~176 receive payments (one per Payment ID),
   ~176 journal entries.

8. **Full H1 sandbox post**:
   ```bash
   python scripts/post_h1_2024.py
   ```
   Posts everything into sandbox. ~45 min runtime (~2100 entities total
   under Path B vs ~1700 originally; the additional CMs and Payments
   are what make A/R and Clearing reach $0).

9. **Verify in sandbox QBO**:
   - Trial Balance: `TikTok Clearing - LELNU` = **$0** (verified by
     `test_post_h1_trial_balance_nets_to_zero_per_statement`)
   - A/R aging for `TikTok Shop LELNU` = **$0** (Receive Payments mark
     all invoices and CMs as Paid/Applied)
   - `Checking` (sandbox default) += $1,714,496.90 in JE entries
   - `Sales - TikTok LELNU` = **-$1,674,435.07** (Σ statement.net_sales
     for statements whose payment falls in H1; differs from previous
     $1,690,241.20 estimate because boundary statements paying out in
     July are excluded)
   - Customer "TikTok Shop LELNU" Open Balance = **$0**

10. **If sandbox looks right** → user completes Intuit production
    compliance checklist → swap to production credentials → re-run
    everything. Same code; only `.env` changes.

### 🔮 Known follow-ups (not blocking)

- **5 missing late-March payments** ($18,402.95) — re-export Q1 xlsx
  with end date 2024-04-03.
- **YEWHX storefront** — once LELNU works, repeat for YEWHX. Update
  `STOREFRONT` constant in driver script and `coa.py` account names.
- **CUEL4Y storefront** — same.
- **Q3 2024 onwards** — copy `post_h1_2024.py` → `post_q3_2024.py`,
  update date range and xlsx paths.

## Critical reminders

1. **Never paste secrets in chat.** The pipeline reads `.env` locally;
   AI never needs to see values.
2. **Sandbox first, production second.** The user has asked for
   production-direct multiple times. Each time, push back: production
   keys aren't even available yet (Intuit gates them).
3. **Identity checks must pass before any QBO post.** If
   `reconcile_h1_2024.py` shows mismatches, **stop**. Don't post bad
   data.
4. **Idempotency**: every QBO post checks for existing DocNumber. Safe
   to re-run.

## TODO list snapshot (for handoff)

```
[completed] Stage A-E: pipeline code (ingest, reconcile, QBO posting)
[completed] Push to GitHub (LAT-qbo private repo)
[completed] Persist context to docs/context/
[completed] User: rotate Client Secret + reformat .env (twice — see file 08)
[completed] Stage F.2: sandbox OAuth flow + .env auto-write
[completed] Stage F.3-4: COA bootstrap (7 accounts + 1 customer in sandbox)
[completed] Path B refactor: Invoice + CreditMemo + Payment + JE posting
            with full trial-balance test on H1 real data; 63/63 tests pass
[pending]   Stage F.5: single-statement dry-run + JSON payload review
[pending]   Stage F.6: single-statement real post + sandbox UI verification
[pending]   Stage F.7: full H1 dry-run (validate all payments build cleanly)
[pending]   Stage F.8: full H1 sandbox post (~2100 entities, ~45 min)
[pending]   Stage F.9: trial-balance verification in sandbox QBO UI
[pending]   User: complete Intuit production checklist
[pending]   Stage G: swap to production credentials and re-run F.5-F.9
```
