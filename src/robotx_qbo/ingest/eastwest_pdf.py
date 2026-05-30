"""East West Bank PDF statement parser.

parse_eastwest(pdf_path) -> list[Txn]

Layout (all pages joined):
  - STARTING DATE: <Month DD, YYYY>   — extract year
  - CREDITS section: date + amount lines (inflows)
  - CHECKS section on page 1 (summary only — IGNORED; use page-3 detail)
  - DEBITS section: date + amount lines (outflows, possibly multi-line)
  - Page-3 check detail: MM/DD/YYYY <num> $<amount>
  - DAILY BALANCES / OVERDRAFT — ignored
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pdfplumber
import yaml

from robotx_qbo.models import Txn

# ── regexes ──────────────────────────────────────────────────────────────────
_AMT_PAT = r"([\d,]+\.\d{2})"

_START_RE = re.compile(r"STARTING DATE:\s*[A-Za-z]+ \d{1,2}, (\d{4})")

# Page-3 check detail:  MM/DD/YYYY  <num>  $<amount>
_CHECK_DETAIL_RE = re.compile(
    r"(\d{2})/(\d{2})/(\d{4})\s+(\d+)\s+\$" + _AMT_PAT
)

# A line that begins with a MM-DD date
_DATE_LINE_RE = re.compile(r"^(\d{2})-(\d{2})\s*(.*)")

# An amount-only line (optional leading/trailing whitespace)
_AMOUNT_ONLY_RE = re.compile(r"^" + _AMT_PAT + r"$")

# Lines to skip that are page-break boilerplate (section continuations)
# East West Irvine-branch statement boilerplate — update if the branch/template changes
_SKIP_RE = re.compile(
    r"^(?:ACCOUNT STATEMENT|Page \d+ of \d+|STARTING DATE:|ENDING DATE:|"
    r"15333 Culver Drive|Irvine CA|86-32006972|ROBOTX? ?TX? ?INC|"
    r"OVERDRAFT/RETURN ITEM FEES|Total for|this period|year-to-date|"
    r"Total Overdraft|Total Returned|CheckingAccount|StatementDate|"
    r"Date Transaction Description|Number Date (?:Transaction|Amount)|"
    r"Total days|Direct inquiries|949-733-8828|Standard Business|"
    r"Account number|Enclosures|Low balance|Average balance|"
    r"Beginning balance|Ending balance)",
    re.IGNORECASE,
)

# Section-start sentinels
_CREDITS_HDR = re.compile(r"^CREDITS\b")
_CHECKS_HDR = re.compile(r"^CHECKS\b")
_DEBITS_HDR = re.compile(r"^DEBITS\b")
_STOP_HDR = re.compile(r"^(?:DAILY BALANCES|OVERDRAFT)")


def _load_checks_cfg() -> dict[str, dict]:
    cfg_path = Path("config/robotx_checks.yaml")
    return yaml.safe_load(cfg_path.read_text(encoding="utf-8"))["checks"]


def _money(s: str) -> Decimal:
    return Decimal(s.replace(",", ""))


def _txn_kind(desc: str) -> str:
    u = desc.upper()
    if "SERVICE CHARGE" in u:
        return "fee"
    if "WIRE" in u or "BBP USD INTL" in u:
        return "wire"
    if "POS PURCHASE" in u or "MERCHANT PURCHASE" in u or "PREAUTH" in u or "PRE-AUTH" in u:
        return "pos"
    if "WITHDRAWAL" in u:
        return "withdrawal"
    return "debit"


def _credit_kind(desc: str) -> str:
    u = desc.upper()
    if "WIRE" in u:
        return "wire"
    if "MOBILE CHECK" in u:
        return "deposit"
    if "PRE-AUTH CREDIT" in u or "PREAUTH CREDIT" in u:
        return "credit"
    return "credit"


def parse_eastwest(pdf_path: str | Path) -> list[Txn]:
    cfg = _load_checks_cfg()
    src = Path(pdf_path).name

    with pdfplumber.open(pdf_path) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)

    # Extract statement year
    m = _START_RE.search(text)
    if not m:
        raise ValueError(f"Cannot find STARTING DATE in {pdf_path}")
    year = int(m.group(1))

    txns: list[Txn] = []

    # ── 1. Checks from page-3 detail (de-dup by check number) ────────────────
    seen_checks: set[str] = set()
    for cm in _CHECK_DETAIL_RE.finditer(text):
        mm, dd, yyyy, num, amt = cm.groups()
        if num in seen_checks:
            continue
        seen_checks.add(num)
        meta = cfg.get(num, {})
        txns.append(
            Txn(
                account="eastwest",
                date=date(int(yyyy), int(mm), int(dd)),
                amount=-_money(amt),
                kind="check",
                description=f"Check {num}",
                check_no=num,
                payee=meta.get("payee"),
                source=src,
            )
        )

    # ── 2. Credits and Debits from section scanning ───────────────────────────
    lines = text.splitlines()
    section: str | None = None  # "credits" | "debits" | None

    # We buffer a pending transaction: (mm, dd, first_desc_fragment, amount_or_None)
    # A transaction is "committed" when we see the next date-line or section-end.

    pending_date: tuple[int, int] | None = None
    pending_desc_parts: list[str] = []
    pending_amount: Decimal | None = None

    def commit_pending(is_credit: bool) -> None:
        nonlocal pending_date, pending_desc_parts, pending_amount
        if pending_date is None:
            return
        if pending_amount is not None:
            mm, dd = pending_date
            desc = " ".join(pending_desc_parts).strip() or ("Credit" if is_credit else "Debit")
            if is_credit:
                txns.append(
                    Txn(
                        account="eastwest",
                        date=date(year, mm, dd),
                        amount=pending_amount,
                        kind=_credit_kind(desc),
                        description=desc,
                        source=src,
                    )
                )
            else:
                txns.append(
                    Txn(
                        account="eastwest",
                        date=date(year, mm, dd),
                        amount=-pending_amount,
                        kind=_txn_kind(desc),
                        description=desc,
                        source=src,
                    )
                )
        # else: no amount found — discard (header/label line mistakenly matched)
        pending_date = None
        pending_desc_parts = []
        pending_amount = None

    for line in lines:
        u = line.strip()

        # ── section transitions ───────────────────────────────────────────
        if _STOP_HDR.match(u):
            commit_pending(section == "credits")
            section = None
            continue

        if _CREDITS_HDR.match(u):
            commit_pending(section == "credits")
            section = "credits"
            continue

        if _CHECKS_HDR.match(u):
            commit_pending(section == "credits")
            section = None  # stop credits, skip check summary
            continue

        if _DEBITS_HDR.match(u):
            commit_pending(section == "debits")
            section = "debits"
            continue

        if section is None:
            continue

        # ── skip boilerplate / header rows ───────────────────────────────
        if not u:
            continue
        if _SKIP_RE.match(u):
            continue

        # ── parse within active section ───────────────────────────────────
        dm = _DATE_LINE_RE.match(u)
        if dm:
            # New transaction starting
            commit_pending(section == "credits")
            mm_s, dd_s, rest = dm.groups()
            pending_date = (int(mm_s), int(dd_s))
            pending_desc_parts = []
            pending_amount = None

            rest = rest.strip()
            if rest:
                # Check if amount is inline at end of rest
                am = _AMOUNT_ONLY_RE.match(rest)
                if am:
                    # Entire rest is just an amount (no description on this line)
                    pending_amount = _money(am.group(1))
                else:
                    # Try amount at end of rest
                    amt_end = re.search(r"\s+" + _AMT_PAT + r"$", rest)
                    if amt_end:
                        desc_part = rest[: amt_end.start()].strip()
                        pending_amount = _money(amt_end.group(1))
                        if desc_part:
                            pending_desc_parts.append(desc_part)
                    else:
                        # No amount on this line yet
                        pending_desc_parts.append(rest)
            # else: just a bare date line (e.g. "03-11"), amount and desc to follow
            continue

        # Not a date line — continuation
        if pending_date is None:
            # Before any date line or after commit — skip
            continue

        # Check if this continuation line is a bare amount
        am = _AMOUNT_ONLY_RE.match(u)
        if am:
            if pending_amount is None:
                pending_amount = _money(am.group(1))
            # else: already have amount, this is a trailing ref line — ignore
            continue

        # Otherwise it's a description continuation or label line
        # Skip label-only lines like "BBP USD INTL WIRE", "OUTGOING WIRE", etc.
        # These are all-caps short labels with no digits
        if u.isupper() and not re.search(r"\d", u):
            continue

        # If we already have an amount, this is post-amount continuation (ref line) — skip
        if pending_amount is not None:
            continue

        # Otherwise, append to description
        pending_desc_parts.append(u)

    # Commit any trailing pending transaction
    commit_pending(section == "credits")

    return txns
