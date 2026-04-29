# CLAUDE.md — read this first

You're working on **`tiktok_qbo`**: a pipeline that posts TikTok Shop LAT
settlement data into QuickBooks Online.

## Read these BEFORE doing anything else

The full project context lives in **`docs/context/`**. Read them in order on
your first turn of any new conversation:

1. [`docs/context/00-INDEX.md`](docs/context/00-INDEX.md) — table of contents + quick orientation
2. [`docs/context/01-business-context.md`](docs/context/01-business-context.md) — what LAT is, why this exists, IPO trajectory
3. [`docs/context/02-data-sources.md`](docs/context/02-data-sources.md) — xlsx 6-sheet structure + 3 BofA ACH formats
4. [`docs/context/03-reconciliation-identities.md`](docs/context/03-reconciliation-identities.md) — the math (verified to the cent)
5. [`docs/context/04-decisions.md`](docs/context/04-decisions.md) — decisions already made + rationale
6. [`docs/context/05-pipeline-architecture.md`](docs/context/05-pipeline-architecture.md) — module map + commands
7. [`docs/context/06-known-issues.md`](docs/context/06-known-issues.md) — edge cases that bit us
8. [`docs/context/07-current-state.md`](docs/context/07-current-state.md) — what's done, what's next
9. [`docs/context/08-security.md`](docs/context/08-security.md) — credentials policy

Total reading time: ~15 minutes. **Don't skip.** Each file exists because
something in it tripped a previous agent up.

A second copy of these docs lives at the parent project root
(`C:\Users\zhour\OneDrive\文档\accounting\docs\context\`) for cross-worktree
access. The two copies should be kept in sync — when you update one,
update both.

## TL;DR if you only have 30 seconds

This is a **working production pipeline**. Don't refactor structure
without reason.

- 176/176 H1 2024 LELNU TikTok payments reconcile to BofA bank to the cent
- Run `python scripts/reconcile_h1_2024.py` to verify (no QBO needed)
- Run `pytest tests/ -q` — 54 tests, all green
- Production QBO posting blocked on user `.env` setup + sandbox OAuth flow
  — see `docs/context/07-current-state.md` for the next concrete step
- Repo: https://github.com/zhouruc16/LAT-qbo (private)

## Hard rules (the "don'ts" — full reasons in `docs/context/06-known-issues.md`)

- **Never paste secrets in chat.** Credentials live in `.env` only.
- **Never use `read_only=True`** on `openpyxl.load_workbook` — silently
  iterates 0 rows on Q1 file.
- **Never read `Reserve amount`** (lowercase) — xlsx column is
  `Reserve Amount` (cap A). Was a real bug.
- **Never post sales tax to QBO.** TikTok is a marketplace facilitator;
  Net method is correct. See `04-decisions.md` D1.
- **Never date-match payments primary.** Use Payment ID. Date window is
  fallback only (HYPERWALLET / TikTok-alt format).
- **Never sandbox-skip to production** without user explicit consent +
  production keys actually being unlocked. See `04-decisions.md` D7.
- **Never commit `.env`** or `state*/`. Both are gitignored.

## Useful commands

```bash
# Reconciliation only — works without QBO credentials
python scripts/reconcile_h1_2024.py

# Run all 54 tests
pytest tests/ -q

# Single-statement QBO preview (after sandbox auth)
python scripts/post_h1_2024.py --dry-run --payment-id 3459076539020317035

# Full H1 dry-run
python scripts/post_h1_2024.py --dry-run

# Full H1 production post
python scripts/post_h1_2024.py
```

## When you make new findings

Update `docs/context/` immediately — not "later". A stale context doc is
worse than no context doc, because the next agent will trust it.

- Decision changed → update `04-decisions.md`
- New edge case discovered → add to `06-known-issues.md`
- Stage advanced or unblocked → update `07-current-state.md`
- New data source / format → update `02-data-sources.md`

Then **mirror the change to both copies** (worktree `docs/context/` and
parent `accounting/docs/context/`).
