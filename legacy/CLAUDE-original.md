# CLAUDE.md — Orientation for Agents

You are working on the **TikTok LAT → QuickBooks Online (QBO)** local pipeline. This file tells you what the project is, where things live, and how to drive work without re-discovering it from scratch every session.

## What this project does

A Python CLI that ingests TikTok Shop LAT settlement `.xlsx` files, plans QBO postings (Invoices, CreditMemos, JournalEntries) against a TikTok Clearing account, reconciles them against bank statement deposits, and emits QBO-ready JSON. Stage 4 (actually pushing to QBO) is intentionally out of scope for the current plan.

Pipeline stages:

1. **ingest** — read `.xlsx`, normalize to typed dataclasses, hash for replay
2. **plan** — build invoices grouped by `order_delivery_date`, statement journal entries with a six-leg shape, credit memos for refunds
3. **reconcile** — verify three identities (per-Payment, per-Statement, cross-month) against bank deposits
4. *(out of scope)* QBO posting

Run:
```
tiktok_qbo ingest    inputs/lat-PLELNU-2024Q2.xlsx
tiktok_qbo plan      --hash <H>
tiktok_qbo reconcile --hash <H>
python scripts/smoke_q2_2024.py    # smoke test on real Q2 2024 data
```

Outputs land in `state/`.

## Where to look first

| You need to know… | Read this |
|---|---|
| The accounting model and architecture | `docs/superpowers/specs/2026-04-23-tiktok-qbo-harness-design.md` |
| The 14-task implementation plan (Part 1, complete) | `docs/superpowers/plans/2026-04-23-tiktok-qbo-harness-part1-local-pipeline.md` |
| Open questions awaiting answers | `docs/context/questions.md` |
| Design choices already locked in | `docs/context/decisions.md` |
| Factual observations about the real data | `docs/context/findings.md` |
| Concrete unfinished action items | `docs/context/todos.md` |
| Source layout | `src/tiktok_qbo/` |
| Tests | `tests/` (46 unit tests, all passing as of last commit) |

**Always read the four `docs/context/*.md` files at the start of a session.** They are the project's working memory across sessions — they encode what's been asked, decided, found, and queued. If you skip them you will repeat work.

## The context-files skill

There is a project-local skill at `.claude/skills/tiktok-qbo-context/SKILL.md` that maintains those four files. Invoke it (or trigger it via phrases like "checkpoint context", "update context files", "log this") whenever a meaningful chunk of work has just happened — a batch of clarifying questions answered, a design choice made, a finding about real data, a todo surfaced. The skill scans the conversation, dedupes semantically, flips `[open]→[resolved]/[done]` in place, supersedes outdated findings, and writes the four files back.

Don't hand-edit the context files unless you're fixing a typo or restructuring. Let the skill do it; that's why it exists.

## How to drive work

### Starting a new piece of work

1. Read the spec section relevant to what's being asked.
2. Read the four `docs/context/*.md` files.
3. If the request is creative ("build X", "add Y", "redesign Z"), invoke `superpowers:brainstorming` and follow the spec → plan → implementation flow.
4. If a plan already exists for the work, invoke `superpowers:subagent-driven-development` and dispatch a fresh implementer subagent per task.
5. If the request is a bug or unexpected behavior, invoke `superpowers:systematic-debugging`.
6. If you're about to claim work is complete, invoke `superpowers:verification-before-completion`.

### Mid-work

- For each task you implement, write the failing test first (`superpowers:test-driven-development`).
- Commit at the end of each green test cycle. Commits are cheap; tangled WIP isn't.
- If you discover something the user should know about (a real-data edge case, a model contradiction, a question you can't answer), record it via the context skill before moving on.

### Finishing

- Run `pytest` — all tests must pass.
- Run the smoke script (`python scripts/smoke_q2_2024.py`) if your change touches ingest/plan/reconcile.
- Invoke `superpowers:requesting-code-review` for substantial work.
- Then `superpowers:finishing-a-development-branch`.

## Conventions you should match

- **Money is `Decimal`**, never `float`. Use `tiktok_qbo.money.to_money` for rounding.
- **Dates are `datetime.date`**, parsed via `tiktok_qbo.dates`. The string `"/"` and empty string both mean "missing"; the parsers handle that.
- **Pydantic v2 dataclasses** for all typed records.
- **Hash inputs** for replay; everything in `state/` is keyed by input hash.
- **Identities have names and meanings** — see spec §3 (Identity 1 / 2 / 3). Don't invent new ones without writing them up.
- **Six-leg statement JE**, with the clearing leg computed as the **balancing plug** (`DR_total − CR_total of legs 1–5`). It is *not* `Σ payable`. Spec §2.3 explains why; the test suite enforces it.

## What's currently open (high-level)

The detailed list lives in `docs/context/`. As of this writing, the live questions cluster around:

- Reconciliation Identity 2 may need to be reformulated row-level around `customer_payment − total_settlement_amount = expense` rather than the statement-aggregate formula.
- Invoice grouping by `order_delivery_date` is already implemented; the user's intuition matches the code.
- The new settlement export (`Untitled spreadsheet (1).xlsx`) has 115 columns and a different shape than prior LAT files; it's almost entirely one statement-date.
- QuickBooks expense routing for shipping / sales tax / fees needs explicit mapping per category, posted via the Statement JE (not on the customer Invoice).

When you start, the four context files will tell you which of these are still open and which have been resolved since this orientation was written.

## Things not to do

- Don't modify `docs/superpowers/specs/...` design docs as a side effect of an implementation task. The spec is a contract; if a task's work implies a spec change, surface it explicitly and let the user decide.
- Don't run `tiktok_qbo` commands against real data without saving outputs to `state/`. Replayability is the whole point.
- Don't start work on `main` without explicit user consent. Use a worktree (`superpowers:using-git-worktrees`) for non-trivial changes.
- Don't skip the context-files read at session start. Five minutes of reading saves hours of rediscovery.
