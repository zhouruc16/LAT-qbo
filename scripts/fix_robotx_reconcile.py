"""Fix bank reconciliation by posting any check/deposit transactions that the
earlier dedup wrongly skipped (genuinely distinct txns sharing date+amount).

Compares the EXPECTED multiset of check/deposit transactions (from the
statements) against what ACTUALLY exists in QBO, and posts the shortfall.
Idempotent: re-running posts nothing once balanced.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from tiktok_qbo.qbo.env import load_creds
from tiktok_qbo.qbo.client import QboClient, QboError
from robotx_qbo.ingest.eastwest_pdf import parse_eastwest
from robotx_qbo.ingest.chase_pdf import parse_chase

SUPPLIERS = ("yushu", "dobot", "booster", "intbot", "pudu", "openlive", "thunder")


def d2(x): return f"{Decimal(str(x)):.2f}"


def _find(client, entity, field, name):
    safe = name.replace("'", "\\'")
    rows = client.query(f"SELECT * FROM {entity} WHERE {field} = '{safe}'") \
                 .get("QueryResponse", {}).get(entity, [])
    return rows[0] if rows else None


def acct_id(client, name):
    return _find(client, "Account", "Name", name)["Id"]


def ensure_vendor(client, name):
    ex = _find(client, "Vendor", "DisplayName", name)
    return ex["Id"] if ex else client.post("vendor", {"DisplayName": name})["Vendor"]["Id"]


def classify_account(t):
    d, dl = t.description, t.description.lower()
    if t.kind == "check":
        p = t.payee or ""
        if t.check_no == "0":
            return "Ask My Accountant", None
        if p == "UPS":
            return "Freight & Shipping", "UPS"
        if p == "Hilisong CA LLC":
            return "Rent", "Hilisong CA LLC"
        return "Wages & Salaries", p
    if t.kind == "cc":
        return "Owner's Draw - Qin Zhen", "Qin Zhen"
    if any(k in d for k in ("Irs", "IRS", "Edd", "Employment Devel")):
        return "Payroll Taxes", ("IRS" if "Irs" in d or "IRS" in d else "EDD")
    if "CA Dept Tax" in d or "cdtfa" in dl:
        return "Taxes & Licenses", "CDTFA"
    if "seller" in dl and "permit" in dl:
        return "Taxes & Licenses", "CDTFA"
    if t.kind == "fee" or "service charge" in dl:
        return "Bank Service Charges", None
    for needle, a in [("OUTBACK", "Meals"), ("FEDEX", "Office Supplies"),
                     ("ARCO", "Auto/Fuel"), ("AMAZON", "Office Supplies")]:
        if needle in d.upper():
            return a, None
    return "Ask My Accountant", None


def is_own_transfer(d):
    dl = d.lower()
    return ("online transfer" in dl) or ("robotx inc" in dl and ("wire" in dl or "fedwire" in dl))


def is_purchase(t):
    dl = t.description.lower()
    return (t.amount < 0 and any(s in dl for s in SUPPLIERS)) or \
           (t.kind == "check" and (t.payee or "").startswith("Thunder"))


def main():
    client = QboClient(load_creds(Path(".env.robotx")))
    banks = {"chase": acct_id(client, "Chase Checking - 0108"),
             "eastwest": acct_id(client, "East West Checking - 6972")}

    txns = []
    for f in ["ewb-01.pdf", "ewb-02.pdf", "ewb-03.pdf", "ewb-04.pdf"]:
        txns += parse_eastwest(f"inputs/robotx/{f}")
    for f in ["chase-01.pdf", "chase-02.pdf", "chase-03.pdf", "chase-04.pdf"]:
        txns += parse_chase(f"inputs/robotx/{f}")

    # EXPECTED checks (outflow) and deposits (inflow), excluding sale/purchase/transfer
    exp_check = defaultdict(list)   # (date, amt, bank) -> [txn, ...]
    exp_dep = defaultdict(list)
    for t in txns:
        if t.amount > 0 and "algi" in t.description.lower():
            continue                                   # sale -> invoice payment
        if is_purchase(t):
            continue                                   # purchase -> bill payment
        if is_own_transfer(t.description):
            if "7880" in t.description:                # parked as a check
                bank = banks[t.account]
                exp_check[(f"{t.date:%Y-%m-%d}", d2(abs(t.amount)), bank)].append(t)
            continue                                   # real transfers handled elsewhere
        bank = banks[t.account]
        key = (f"{t.date:%Y-%m-%d}", d2(abs(t.amount)), bank)
        (exp_dep if t.amount > 0 else exp_check)[key].append(t)

    # ACTUAL counts in QBO
    act_check = Counter()
    for r in client.query("SELECT TxnDate, TotalAmt, AccountRef FROM Purchase") \
                   .get("QueryResponse", {}).get("Purchase", []):
        act_check[(r["TxnDate"], d2(r["TotalAmt"]), r.get("AccountRef", {}).get("value"))] += 1
    act_dep = Counter()
    for r in client.query("SELECT TxnDate, TotalAmt, DepositToAccountRef FROM Deposit") \
                   .get("QueryResponse", {}).get("Deposit", []):
        act_dep[(r["TxnDate"], d2(r["TotalAmt"]), r.get("DepositToAccountRef", {}).get("value"))] += 1

    posted = 0
    for key, tlist in exp_check.items():
        missing = len(tlist) - act_check.get(key, 0)
        for t in tlist[len(tlist) - missing:] if missing > 0 else []:
            acct_name, party = classify_account(t)
            entity = ensure_vendor(client, party) if party else None
            body = {"PaymentType": "Check", "AccountRef": {"value": key[2]},
                    "TxnDate": key[0], "TotalAmt": key[1],
                    "PrivateNote": f"RobotX fix | {t.payee or ''} | {t.description[:50]}",
                    "Line": [{"Amount": key[1], "DetailType": "AccountBasedExpenseLineDetail",
                              "AccountBasedExpenseLineDetail": {"AccountRef": {"value": acct_id(client, acct_name)}}}]}
            if entity:
                body["EntityRef"] = {"value": entity}
            try:
                client.post("purchase", body)
            except QboError:
                body.pop("EntityRef", None); client.post("purchase", body)
            posted += 1
            print(f"  +check {key[0]} ${key[1]} {acct_name} ({t.payee or t.description[:30]})")

    for key, tlist in exp_dep.items():
        missing = len(tlist) - act_dep.get(key, 0)
        for t in tlist[len(tlist) - missing:] if missing > 0 else []:
            acct_name, _ = classify_account(t)
            body = {"DepositToAccountRef": {"value": key[2]}, "TxnDate": key[0],
                    "PrivateNote": f"RobotX fix | {t.description[:50]}",
                    "Line": [{"Amount": key[1], "DetailType": "DepositLineDetail",
                              "DepositLineDetail": {"AccountRef": {"value": acct_id(client, acct_name)}}}]}
            client.post("deposit", body); posted += 1
            print(f"  +deposit {key[0]} ${key[1]} {acct_name}")

    print(f"\nPosted {posted} previously-skipped transaction(s).")
    print("\nFinal bank balances (target: Chase 403,349.48 / East West 5,526.52):")
    for name in ["Chase Checking - 0108", "East West Checking - 6972"]:
        r = client.query(f"SELECT CurrentBalance FROM Account WHERE Name = '{name}'") \
                  ["QueryResponse"]["Account"][0]
        print(f"  {name}: ${Decimal(str(r['CurrentBalance'])):,.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
