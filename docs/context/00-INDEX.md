# tiktok_qbo project context — index

This directory persists everything the AI sessions have learned while building
the LAT Group → TikTok → QuickBooks Online pipeline. **Read these in order**
on the first session of any new conversation. They are written to be
information-dense — not chatty. Total reading time: ~15 minutes.

## Files

| # | File | Purpose |
|---|---|---|
| 00 | [00-INDEX.md](./00-INDEX.md) | This file. |
| 01 | [01-business-context.md](./01-business-context.md) | What LAT Group is, why this exists, who reads the books. **Read this first.** |
| 02 | [02-data-sources.md](./02-data-sources.md) | The TikTok xlsx export's 6 sheets and the BofA PDF's 3 ACH descriptor formats. |
| 03 | [03-reconciliation-identities.md](./03-reconciliation-identities.md) | The math that ties order rows → statements → payments → bank. Verified to the cent. |
| 04 | [04-decisions.md](./04-decisions.md) | Decisions already made, with reasons. Don't re-litigate without cause. |
| 05 | [05-pipeline-architecture.md](./05-pipeline-architecture.md) | What each module does and how to run end-to-end. |
| 06 | [06-known-issues.md](./06-known-issues.md) | Edge cases that already bit us; how each is handled. |
| 07 | [07-current-state.md](./07-current-state.md) | What's done, what's blocking, what's next. **Read this last.** |
| 08 | [08-security.md](./08-security.md) | Credentials, rotation, gitignore, what NEVER goes in chat. |

## Quick orientation for a new agent

1. **Where the code lives**: `C:\Users\zhour\OneDrive\文档\accounting\` (project root)
   - The active git worktree under `.claude/worktrees/<name>/` is where you make changes
   - Pushed remote: `https://github.com/zhouruc16/LAT-qbo` (private)
2. **Where the data lives**: `C:\Users\zhour\Downloads\`
   - `1-3-2024.xlsx`, `4-6-2024.xlsx` — Q1, Q2 LELNU TikTok exports
   - `eStmt_2024-MM-DD.pdf` — BofA business checking statements (Jan–Jul)
3. **Where credentials live**: `C:\Users\zhour\OneDrive\文档\accounting\.env`
   - Format must be `KEY=VALUE` per line — see `.env.example` in the repo
   - **Never paste values in chat. Never commit.**
4. **Headline result**: 176/176 H1 2024 LELNU payments reconcile to BofA bank
   to the cent. Pipeline can post all H1 to QBO with one command. Currently
   blocked on user authenticating sandbox QBO OAuth.

## When to update these docs

**Update them as soon as a fact changes** — not "later". A stale context doc
is worse than no context doc, because the next agent will trust it.

Specifically: update file 04 when a decision changes, file 06 when a new
edge case is discovered, file 07 every time a stage is completed or
unblocked.
