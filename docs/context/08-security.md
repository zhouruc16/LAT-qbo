# 08 — Security

## Credentials

The pipeline reads credentials from a `.env` file at the project root or
any parent directory. Required keys:

```
INTUIT_CLIENT_ID=         # from Intuit Developer → Keys and credentials
INTUIT_CLIENT_SECRET=     # same place
INTUIT_REDIRECT_URI=http://localhost:8080/callback
INTUIT_REALM_ID=          # auto-filled by `tiktok_qbo auth`
INTUIT_REFRESH_TOKEN=     # auto-filled by `tiktok_qbo auth`
INTUIT_ENVIRONMENT=sandbox    # or production
```

Format must be `KEY=VALUE` per line. No spaces around `=`. No quotes.
No colons. See `.env.example` in the repo.

## What never goes in chat

- Client Secret
- Refresh Token
- Access Token
- Realm ID (less sensitive, but still — let it auto-fill from OAuth)

The pipeline reads `.env` **locally**; AI sessions never see the values.
If a user pastes a secret in chat, treat it as compromised and instruct
them to rotate immediately at
https://developer.intuit.com → My Hub → app → Keys and credentials →
Regenerate.

## What's gitignored

```
.env             # the only credential file
.env.local
.env.*.local
state/           # JSONL ingest output (no secrets, but contains business data)
state_*/         # H1, Q1, Q2 reconciliation outputs
inputs/          # raw xlsx/pdf if user puts them there
```

`.env.example` IS committed (it's just a template with empty values).

## Credential rotation events

- **2026-04-29 (session 1)**: User pasted real Client Secret in chat
  during Intuit Developer setup. Repeatedly asked to rotate. Status of
  rotation **unconfirmed at session 1 boundary**. The exposed credentials
  were for an "IN DEVELOPMENT" app, so blast radius was limited (no
  production access until checklist is completed) but the same secret
  would also work for the future production app.
- **2026-04-29 (session 2)**: User pasted the (already-rotated)
  Development-tab Client ID + Client Secret in chat as a screenshot/copy
  while answering "is this from the Development tab". Asked to rotate
  again immediately and to update `.env` without echoing the value.
  Rotation confirmed: **see live status in `07-current-state.md`**.
  Lesson: when asking the user to confirm WHICH credential set they're
  using, ask only for "Development tab? Y/N" — never invite them to
  paste the credentials themselves.

## OAuth flow safety

`src/tiktok_qbo/qbo/auth.py`:
- Uses CSRF-protected `state` parameter (random 16-byte token, verified on
  callback)
- Listens on `127.0.0.1` only (not `0.0.0.0`)
- Closes the listener after one request
- Writes refresh token only to `.env` (never logs, never prints)
- 5-minute timeout on the auth flow

Future improvements considered but not implemented:
- PKCE (Intuit doesn't require it for confidential clients)
- Revoke endpoint on user request

## Refresh token lifetime

Intuit refresh tokens are valid for **100 days** from last use. Any API
call rotates the refresh token (the response includes a new one).

The pipeline does not currently auto-write the rotated refresh token back
to `.env`. If a sequence of API calls produces a new refresh token, it's
held in memory but not persisted. **TODO**: persist on rotation. For now,
if the pipeline runs more than 100 days apart, re-run `tiktok_qbo auth`.

## Privacy

The pipeline processes:
- **Customer payment** amounts (no buyer names — TikTok doesn't expose them)
- **Order details** (SKU, quantity, product names — no PII)
- **Bank account** masked to last 4 digits in xlsx Payments column
- **LAT's QBO data** (full access via API)

No PII enters the system. Buyer-level data is aggregated at the delivery-
date level for invoice generation; individual buyers are never identified.

## Repository visibility

`https://github.com/zhouruc16/LAT-qbo` — **private**. Verify in repo
Settings → Manage access. If it ever becomes public:
1. Rotate Client Secret immediately (it's never been committed but
   defense in depth)
2. Treat the repo content as exposed (file paths, business names, etc.
   are visible)

## Audit trail

- Every QBO post logs to `stats.errors` if it fails. Inspect after each
  run.
- Idempotency keys (`INV-LELNU-...`, `JE-LELNU-...`) make it possible to
  trace any QBO transaction back to its source data.
- The reconciliation CSVs (`state_h1/reconcile-summary.csv`) are the
  audit-trail artifacts. Keep them.
