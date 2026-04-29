# 01 — Business context

## Who

**LAT Group Inc.** — California-based seller (3802 Avocado St, Irvine, CA 92606).
Sells primarily fragrance products (Versace, Burberry, Armani, etc.) on
TikTok Shop. The user (`tech@usrobotx.com`) is operating the books.

Banking: Bank of America business checking, account ending **9247**.

## Why this exists

LAT's books are behind. They have 6+ months of TikTok payouts that haven't
been posted to QuickBooks. Manual entry would be ~2,000 invoices and ~180
journal entries — not realistic.

The pipeline ingests TikTok's settlement xlsx exports + BofA PDF statements,
reconciles them, and posts everything to QBO via the Intuit Accounting API.

## IPO trajectory

LAT is **on an IPO track**. This affects accounting decisions:
- Books must be **GAAP-compliant** (ASC 606 revenue recognition).
- Future audit by a CPA firm. Documentation matters.
- Revenue method: **Net** for marketplace-facilitator sales tax (TikTok
  remits tax to states; LAT is the agent, not principal — see file 04).
- We chose **Path A** (multi-line revenue) so the P&L can break down
  customer-paid revenue vs TikTok-paid platform subsidies — auditors will
  ask this.

⚠️ The user has stated IPO intent but has **not yet engaged a CPA firm**.
Until they do, accounting policy decisions made here are tentative — a
real CPA can override them. Important decisions are documented in file 04
so any future override is mechanical (replay the pipeline with different
parameters, no business logic change).

## Storefronts

LAT operates three TikTok Shop storefronts visible in BofA bank
descriptors:

- **USLCPLELNU** — "LELNU" — primary storefront, what we've reconciled
- **USLCPYEWHX** — "YEWHX" — secondary; xlsx not yet exported
- **USLCCUEL4Y** — "CUEL4Y" — tertiary; xlsx not yet exported
- (Briefly seen `USLCKAEWVA` — only 2 lines, $1,814.68 total, can ignore)

The bank statement contains all storefronts mixed together. We filter by
storefront code in the ACH descriptor.

**Current scope: LELNU only.** YEWHX and CUEL4Y will be layered on later
by re-running the pipeline with their xlsx files.

## TikTok's role

TikTok Shop acts as the **marketplace facilitator** under US state laws:
- Buyer pays TikTok (price + shipping + sales tax)
- TikTok deducts fees (referral, transaction, affiliate, ad fees)
- TikTok holds a rolling reserve (some % held back, released later)
- TikTok remits sales tax directly to state DORs (LAT does NOT remit)
- TikTok ACHs the net to LAT's BofA account

So TikTok is both LAT's customer (pays for goods on buyer's behalf) and
LAT's tax agent. The xlsx Statements sheet shows the breakdown; the bank
shows only the net deposit.

Pre-2024-01-16, TikTok used **HYPERWALLET** as their ACH provider.
Bank descriptor format is different (no payout ID); we match by
date+amount fallback. After 2024-01-16, TikTok switched to direct ACH.

## Period in scope

**H1 2024 (Jan 1 – Jun 30)** is the verified, reconciled period.

H2 2024, 2025, etc. will follow the same pattern — the pipeline is
period-agnostic. Add a new driver script per period (template:
`scripts/post_h1_2024.py`).

## QuickBooks state

- **Production QBO**: doesn't exist yet. The user has an Intuit Developer
  account but production credentials are gated behind a compliance
  checklist (50+ minutes of forms) that they haven't completed.
- **Sandbox QBO**: created (QuickBooks Online Plus, US). This is where
  we'll do the dress rehearsal.
- **Chart of Accounts**: empty fresh sandbox. The pipeline will
  auto-bootstrap 7 accounts + 1 customer record on first run.

The user wants to **go production directly**, but production keys aren't
unlocked. Sandbox-first is the practical path. Same code, swap credentials
later.
