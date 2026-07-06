"""Transaction classifier for RobotX QBO pipeline.

classify(txns) -> list[ClassifiedTxn]

Two layers:
  1. A MANUAL OVERRIDE table keyed by (account, ISO-date, abs-amount) for the
     handful of transactions that were individually adjudicated — by reading
     the cashier's check / check image, or from the owner's ("boss") notes.
     These can't be inferred from the bank description alone (e.g. a "Deposit"
     line with no payer that we matched to a US Homecoin cashier's check).
  2. RULES applied in order; first match wins. Rules cover the deterministic,
     description-driven cases (payroll, taxes, fees, robot-vendor wires, rent,
     inter-bank transfers, etc.).

Everything still unidentified falls through to "Ask My Accountant" so the books
balance while the owner resolves it.
"""
from __future__ import annotations

from decimal import Decimal

from robotx_qbo.models import ClassifiedTxn, Txn

# Robot suppliers recognised in outgoing-wire descriptions (case-insensitive).
_SUPPLIERS = ("Dobot", "Booster", "Intbot", "Pudu", "OpenLive", "YuShu",
              "Thunder", "Newton", "Keenon")


def _d2(x: Decimal) -> str:
    return f"{abs(x):.2f}"


# ── Layer 1: manual overrides ──────────────────────────────────────────────
# Key: (account, "YYYY-MM-DD", "abs amount"). Value: ClassifiedTxn kwargs
# (minus txn). Source noted in each memo.
def _sale(party: str) -> dict:
    return dict(category="sale", qbo_action="invoice",
                account_name="Sales - Robots", party=party)


_MANUAL: dict[tuple[str, str, str], dict] = {
    # Identified customer deposits → Sales (matched to US Homecoin cashier's
    # checks; the boss confirmed Homecoin is our robot customer).
    ("chase", "2025-12-10", "452000.00"): _sale("US Homecoin Group"),
    ("chase", "2025-12-23", "683000.00"): _sale("US Homecoin Group"),
    ("chase", "2026-03-23", "95000.00"): _sale("US Homecoin Group"),
    ("chase", "2026-03-25", "497550.00"): _sale("US Homecoin Group"),
    # June 2026 Homecoin robot purchase (owner-confirmed cashier's check).
    ("chase", "2026-06-02", "642005.00"): _sale("US Homecoin Group"),

    # $15,000 wire to Accc Inc (LA) — owner: payment to the accounting firm.
    ("chase", "2026-06-10", "15000.00"): dict(
        category="expense", qbo_action="expense", account_name="Professional Fees",
        party="Accc Inc", memo="Accounting firm (owner-confirmed)"),

    # New American Title wire — boss labelled it a robot purchase. Title company
    # is unusual for that, so post to COGS but FLAG for confirmation.
    ("chase", "2026-03-30", "497000.00"): dict(
        category="purchase", qbo_action="bill",
        account_name="Cost of Goods - Robots", party="New American Title Company",
        flagged=True, memo="Boss: robot purchase — CONFIRM (paid to a title company)"),

    # "Sam" pays third-party service fees with cash / a self-check (boss notes).
    ("eastwest", "2026-03-26", "2500.00"): dict(
        category="expense", qbo_action="expense", account_name="Outside Services",
        party="Sam", memo="Sam — third-party service fee"),
    ("eastwest", "2026-04-03", "5000.00"): dict(
        category="expense", qbo_action="expense", account_name="Outside Services",
        party="Sam", memo="Sam — third-party service fee"),
    ("eastwest", "2026-04-13", "9000.00"): dict(
        category="expense", qbo_action="check", account_name="Outside Services",
        party="Sam", memo="Sam — third-party service fee (self-check)"),

    # 2025 non-payroll checks read from the scanned check images.
    ("eastwest", "2025-12-19", "10000.00"): dict(
        category="expense", qbo_action="check", account_name="Professional Fees",
        party="Z & C CPAS LLP", memo="Invoice #13029"),
    ("eastwest", "2025-12-16", "353.00"): dict(
        category="expense", qbo_action="check", account_name="Travel",
        memo="Travel fee & Car Rental"),

    # Unresolved large self-check → parked.
    ("eastwest", "2025-05-19", "250000.00"): dict(
        category="unknown", qbo_action="expense", account_name="Ask My Accountant",
        memo="Check to Robotx Inc (self) $250k — PENDING boss"),
}


def _manual(t: Txn) -> ClassifiedTxn | None:
    key = (t.account, f"{t.date:%Y-%m-%d}", _d2(t.amount))
    kw = _MANUAL.get(key)
    return ClassifiedTxn(txn=t, **kw) if kw else None


# ── Layer 2: helpers ───────────────────────────────────────────────────────
def _is_interbank(d: str) -> bool:
    """A transfer between the two known RobotX banks (EWB ↔ Chase). These all
    name 'RobotX Inc' as the wire counterparty. Plain 'online transfer to
    chk ...XXXX' lines go to EXTERNAL accounts (…7880 rent, …5527/…6733
    unknown) and are NOT inter-bank."""
    dl = d.lower()
    return "robotx inc" in dl and ("wire" in dl or "fedwire" in dl)


def _matched_supplier(d: str) -> str | None:
    dl = d.lower()
    for s in _SUPPLIERS:
        if s.lower() in dl:
            return s
    return None


def _classify_one(t: Txn) -> ClassifiedTxn:
    manual = _manual(t)
    if manual is not None:
        return manual

    d = t.description
    dl = d.lower()
    du = d.upper()

    # ── Rule 1: Checks ──────────────────────────────────────────────────────
    if t.kind == "check":
        payee = t.payee or ""
        if "UPS" in payee:
            return ClassifiedTxn(txn=t, category="expense", qbo_action="check",
                                 account_name="Freight & Shipping", party="UPS")
        if "Hilisong CA LLC" in payee:
            return ClassifiedTxn(txn=t, category="expense", qbo_action="check",
                                 account_name="Rent", party="Hilisong CA LLC")
        if payee.startswith("Thunder"):
            return ClassifiedTxn(txn=t, category="purchase", qbo_action="bill",
                                 account_name="Cost of Goods - Robots", party=payee)
        # Everything else (numbered or numberless) → payroll.
        return ClassifiedTxn(txn=t, category="payroll", qbo_action="check",
                             account_name="Wages & Salaries", party=payee or None)

    # ── Rule 2: Rent — transfers to the landlord account …7880 ──────────────
    if "7880" in d:
        return ClassifiedTxn(txn=t, category="expense", qbo_action="expense",
                             account_name="Rent", party="Landlord (acct …7880)",
                             memo="Rent (boss: acct …7880)")

    # ── Rule 3: Sales ───────────────────────────────────────────────────────
    if t.amount > 0 and "homecoin" in dl:
        return ClassifiedTxn(txn=t, category="sale", qbo_action="invoice",
                             account_name="Sales - Robots", party="US Homecoin Group")
    if t.amount > 0 and "algi" in dl:
        return ClassifiedTxn(txn=t, category="sale", qbo_action="invoice",
                             account_name="Sales - Robots", party="Algi Investment Inc")

    # ── Rule 4: Inter-bank transfers (EWB ↔ Chase) ──────────────────────────
    if _is_interbank(d):
        return ClassifiedTxn(txn=t, category="transfer", qbo_action="transfer",
                             account_name="(bank)", memo="Inter-account transfer")

    # ── Rule 5: Robot purchases (supplier wires) ────────────────────────────
    if t.amount < 0:
        supplier = _matched_supplier(d)
        if supplier is not None:
            flagged = False
            memo = ""
            account = "Cost of Goods - Robots"
            if "yushu" in dl:
                account = "Vendor Deposits"
                flagged = True
                memo = "Performance bond — PENDING"
            elif "openlive" in dl and abs(t.amount) == Decimal("465450.00"):
                flagged = True
                memo = "Settlement fee Import — PENDING"
            return ClassifiedTxn(txn=t, category="purchase", qbo_action="bill",
                                 account_name=account, party=supplier,
                                 flagged=flagged, memo=memo)

    # ── Rule 6: Owner draw (CC autopay) ─────────────────────────────────────
    if t.kind == "cc":
        return ClassifiedTxn(txn=t, category="owner_draw", qbo_action="check",
                             account_name="Owner's Draw - Qin Zhen", party="Qin Zhen")

    # ── Rule 7: Payroll / CA taxes ──────────────────────────────────────────
    if "irs" in dl or "usataxpymt" in dl:
        return ClassifiedTxn(txn=t, category="tax", qbo_action="expense",
                             account_name="Payroll Taxes", party="IRS")
    if "employment devel" in dl or "edd eftpmt" in dl or "edd" in dl:
        return ClassifiedTxn(txn=t, category="tax", qbo_action="expense",
                             account_name="Payroll Taxes", party="EDD")
    if "ca dept tax" in dl or "cdtfa" in dl:
        return ClassifiedTxn(txn=t, category="tax", qbo_action="expense",
                             account_name="Taxes & Licenses", party="CDTFA")

    # ── Rule 8: Seller's permit ─────────────────────────────────────────────
    if "seller" in dl and "permit" in dl:
        return ClassifiedTxn(txn=t, category="expense", qbo_action="expense",
                             account_name="Taxes & Licenses", party="CDTFA",
                             memo="Seller's permit")

    # ── Rule 9: Bank service charges / fees ─────────────────────────────────
    if t.kind == "fee" or "service charge" in dl or "maintenance fee" in dl:
        return ClassifiedTxn(txn=t, category="expense", qbo_action="expense",
                             account_name="Bank Service Charges")

    # ── Rule 10: Known merchants / debits ───────────────────────────────────
    if "shanghailongqiao" in dl or "shanghai longqiao" in dl:
        return ClassifiedTxn(txn=t, category="expense", qbo_action="expense",
                             account_name="Travel", party="Sam",
                             memo="Sam business travel")
    if "HARLAND CLARKE" in du:
        return ClassifiedTxn(txn=t, category="expense", qbo_action="expense",
                             account_name="Office Supplies", memo="Check order")
    for needle, acct in [("OUTBACK", "Meals"), ("FEDEX", "Office Supplies"),
                         ("ARCO", "Auto/Fuel"), ("AMAZON", "Office Supplies")]:
        if needle in du:
            return ClassifiedTxn(txn=t, category="expense", qbo_action="expense",
                                 account_name=acct)

    # ── Rule 11: Fallback unknown → Ask My Accountant ───────────────────────
    return ClassifiedTxn(txn=t, category="unknown",
                         qbo_action="deposit" if t.amount > 0 else "expense",
                         account_name="Ask My Accountant",
                         memo=f"Unclassified: {d[:40]}")


def classify(txns: list[Txn]) -> list[ClassifiedTxn]:
    return [_classify_one(t) for t in txns]
