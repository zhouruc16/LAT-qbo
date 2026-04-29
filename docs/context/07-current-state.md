# 07 — Current state and next steps

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
- **Tests**: 54 pass (48 unit + 6 QBO post tests including a real-data
  integration test that builds JEs for all 176 H1 payments and verifies
  every one balances DR=CR).
- **Documentation**: README, HARNESS guide, CLAUDE.md, this context dir.
- **Repository**: Pushed to https://github.com/zhouruc16/LAT-qbo (private).

### ⏳ Blocked / waiting on user

- **`.env` setup**: User created `.env` at
  `C:\Users\zhour\OneDrive\文档\accounting\.env` but in wrong format
  (`Key: Value` with colons instead of `KEY=VALUE`). User needs to:
  1. Reformat to `KEY=VALUE` per `.env.example`
  2. Rotate the Client Secret (was exposed in chat — see file 08)
  3. Switch from production credentials to **Development** credentials
     since production is locked behind a 50-min compliance checklist
- **Sandbox QBO**: User created a QuickBooks Online Plus sandbox company.
  Realm ID will auto-fill via OAuth flow.

### 🚧 Next agent should do

When user signals "ready" (or `.env` is correctly formatted):

1. **Verify .env loads**:
   ```bash
   python -c "from tiktok_qbo.qbo.env import load_creds; print(load_creds().environment)"
   ```
   Should print `sandbox` (not error).

2. **Run OAuth**:
   ```bash
   python -m tiktok_qbo auth
   ```
   Browser opens → user authorizes against sandbox QBO → callback writes
   refresh_token + realm_id back to `.env`.

3. **Bootstrap COA dry-run**:
   ```bash
   python -m tiktok_qbo qbo-init --dry-run
   ```
   No writes; just verifies connection works.

4. **Bootstrap COA real**:
   ```bash
   python -m tiktok_qbo qbo-init
   ```
   Creates 7 accounts + 1 customer. Idempotent.

5. **Single-payment preview**:
   ```bash
   python scripts/post_h1_2024.py --dry-run --payment-id 3459076539020317035
   ```
   Builds payload but doesn't send. User reviews JSON.

6. **Single-payment real**:
   ```bash
   python scripts/post_h1_2024.py --payment-id 3459076539020317035
   ```
   Posts ONE statement (05-02-2024). User verifies in QBO sandbox UI.

7. **Full H1 dry-run**:
   ```bash
   python scripts/post_h1_2024.py --dry-run
   ```
   Validates all 176 payouts can be built without errors.

8. **Full H1 sandbox post**:
   ```bash
   python scripts/post_h1_2024.py
   ```
   Posts ~1500 invoices + 176 JEs into sandbox. ~30 min runtime.

9. **Verify in sandbox QBO**:
   - Trial Balance: `TikTok Clearing - LELNU` = $0
   - A/R aging: `TikTok Shop LELNU` = $0
   - BofA Checking: $1,714,496.90 in TikTok credits
   - `Sales - TikTok LELNU`: $1,690,241.20

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
[pending]   User: rotate Client Secret + reformat .env
[pending]   User: run OAuth (Stage F.2)
[pending]   Sandbox COA bootstrap (Stage F.3-4)
[pending]   Single-payment preview (Stage F.5-6)
[pending]   Full H1 sandbox post (Stage F.7-8)
[pending]   Verify in sandbox QBO (Stage F.9)
[pending]   User: complete Intuit production checklist
[pending]   Swap to production credentials and re-run
```
