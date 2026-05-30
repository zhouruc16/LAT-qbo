"""Transaction classifier for RobotX QBO pipeline.

classify(txns) -> list[ClassifiedTxn]

Rules are applied in order; first match wins.
"""
from __future__ import annotations

from decimal import Decimal

from robotx_qbo.models import ClassifiedTxn, Txn

# Supplier names used in wire-transfer descriptions (case-insensitive match)
_SUPPLIERS = ("Dobot", "Booster", "Intbot", "Pudu", "OpenLive", "YuShu", "Thunder")


def _is_own_transfer(d: str) -> bool:
    dl = d.lower()
    return ("online transfer" in dl) or (
        "robotx inc" in dl and ("wire" in dl or "fedwire" in dl)
    )


def _matched_supplier(d: str) -> str | None:
    """Return the first supplier name found in d (case-insensitive), or None."""
    dl = d.lower()
    for s in _SUPPLIERS:
        if s.lower() in dl:
            return s
    return None


def _classify_one(t: Txn) -> ClassifiedTxn:
    d = t.description
    dl = d.lower()

    # ── Rule 1: Checks ──────────────────────────────────────────────────────
    if t.kind == "check":
        if t.check_no == "0":
            return ClassifiedTxn(
                txn=t,
                category="unknown",
                qbo_action="deposit",
                account_name="Ask My Accountant",
                memo="Check to Robotx Inc (self) — PENDING",
            )
        payee = t.payee or ""
        if "UPS" in payee:
            return ClassifiedTxn(
                txn=t,
                category="expense",
                qbo_action="check",
                account_name="Freight & Shipping",
                party="UPS",
            )
        if "Hilisong CA LLC" in payee:
            return ClassifiedTxn(
                txn=t,
                category="expense",
                qbo_action="check",
                account_name="Rent",
                party="Hilisong CA LLC",
            )
        if payee.startswith("Thunder"):
            return ClassifiedTxn(
                txn=t,
                category="purchase",
                qbo_action="bill",
                account_name="Cost of Goods - Robots",
                party=payee,
            )
        # All other checks → payroll
        return ClassifiedTxn(
            txn=t,
            category="payroll",
            qbo_action="check",
            account_name="Wages & Salaries",
            party=payee or None,
        )

    # ── Rule 2: Sales (Algi inflows) ────────────────────────────────────────
    if t.amount > 0 and "algi" in dl:
        return ClassifiedTxn(
            txn=t,
            category="sale",
            qbo_action="invoice",
            account_name="Sales - Robots",
            party="Algi Investment Inc",
        )

    # ── Rule 3: Own-account transfers ───────────────────────────────────────
    if _is_own_transfer(d):
        return ClassifiedTxn(
            txn=t,
            category="transfer",
            qbo_action="transfer",
            account_name="(bank)",
            memo="Inter-account transfer",
        )

    # ── Rule 4: Robot purchases (supplier wires) ────────────────────────────
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
            return ClassifiedTxn(
                txn=t,
                category="purchase",
                qbo_action="bill",
                account_name=account,
                party=supplier,
                flagged=flagged,
                memo=memo,
            )

    # ── Rule 5: Owner draw (CC autopay) ─────────────────────────────────────
    if t.kind == "cc":
        return ClassifiedTxn(
            txn=t,
            category="owner_draw",
            qbo_action="check",
            account_name="Owner's Draw - Qin Zhen",
            party="Qin Zhen",
        )

    # ── Rule 6: Payroll taxes / CA taxes ────────────────────────────────────
    if "irs" in dl or "usataxpymt" in dl:
        return ClassifiedTxn(
            txn=t,
            category="tax",
            qbo_action="expense",
            account_name="Payroll Taxes",
            party="IRS",
        )
    if "employment devel" in dl or "edd eftpmt" in dl:
        return ClassifiedTxn(
            txn=t,
            category="tax",
            qbo_action="expense",
            account_name="Payroll Taxes",
            party="EDD",
        )
    if "ca dept tax" in dl or "cdtfa" in dl:
        return ClassifiedTxn(
            txn=t,
            category="tax",
            qbo_action="expense",
            account_name="Taxes & Licenses",
            party="CDTFA",
        )

    # ── Rule 7: Seller's permit ──────────────────────────────────────────────
    if "seller" in dl and "permit" in dl:
        return ClassifiedTxn(
            txn=t,
            category="expense",
            qbo_action="expense",
            account_name="Taxes & Licenses",
            party="CDTFA",
            memo="Seller's permit",
        )

    # ── Rule 8: Bank service charges / fees ─────────────────────────────────
    if t.kind == "fee" or "service charge" in dl:
        return ClassifiedTxn(
            txn=t,
            category="expense",
            qbo_action="expense",
            account_name="Bank Service Charges",
        )

    # ── Rule 9: Known POS merchants ─────────────────────────────────────────
    u = d.upper()
    if "OUTBACK" in u:
        return ClassifiedTxn(
            txn=t,
            category="expense",
            qbo_action="expense",
            account_name="Meals",
        )
    if "FEDEX" in u:
        return ClassifiedTxn(
            txn=t,
            category="expense",
            qbo_action="expense",
            account_name="Office Supplies",
        )
    if "ARCO" in u:
        return ClassifiedTxn(
            txn=t,
            category="expense",
            qbo_action="expense",
            account_name="Auto/Fuel",
        )
    if "AMAZON" in u:
        return ClassifiedTxn(
            txn=t,
            category="expense",
            qbo_action="expense",
            account_name="Office Supplies",
        )

    # ── Rule 10: Fallback unknown ────────────────────────────────────────────
    return ClassifiedTxn(
        txn=t,
        category="unknown",
        qbo_action="deposit" if t.amount > 0 else "expense",
        account_name="Ask My Accountant",
        memo=f"Unclassified: {d[:40]}",
    )


def classify(txns: list[Txn]) -> list[ClassifiedTxn]:
    return [_classify_one(t) for t in txns]
