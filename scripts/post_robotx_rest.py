"""Post the remaining RobotX bank-side transactions to the QBO sandbox.

Brings the two bank registers to life so they reconcile to the statements:
  - 2 bank accounts + opening-balance journal entry (12/31/2025)
  - cash side of the already-posted sales (Invoice Payments) and purchases
    (Bill Payments)
  - 33 payroll checks, 3 owner draws, 11 taxes, 13 bank fees, rent, UPS,
    seller's permit, 5 POS expenses
  - 3 inter-account transfers (+ the 15k-to-...7880 parked to Ask My Accountant)
  - 10 unknowns -> Ask My Accountant

Idempotent: dedups each entity type by (date, amount[, account]) before posting.
Creds from .env.robotx.
"""
from __future__ import annotations
from decimal import Decimal
from pathlib import Path

from tiktok_qbo.qbo.env import load_creds
from tiktok_qbo.qbo.client import QboClient, QboError
from robotx_qbo.ingest.eastwest_pdf import parse_eastwest
from robotx_qbo.ingest.chase_pdf import parse_chase

SUPPLIERS = ("yushu", "dobot", "booster", "intbot", "pudu", "openlive", "thunder")


def d2(x) -> str:
    return f"{Decimal(str(x)):.2f}"


# ---------- account / party helpers ----------
def _find(client, entity, field, name):
    safe = name.replace("'", "\\'")
    rows = client.query(f"SELECT * FROM {entity} WHERE {field} = '{safe}'") \
                 .get("QueryResponse", {}).get(entity, [])
    return rows[0] if rows else None


def ensure_account(client, name, atype, sub=None):
    ex = _find(client, "Account", "Name", name)
    if ex:
        return ex["Id"]
    body = {"Name": name, "AccountType": atype}
    if sub:
        body["AccountSubType"] = sub
    try:
        return client.post("account", body)["Account"]["Id"]
    except QboError:
        body.pop("AccountSubType", None)          # let QBO pick a default subtype
        return client.post("account", body)["Account"]["Id"]


def ensure_vendor(client, name):
    ex = _find(client, "Vendor", "DisplayName", name)
    if ex:
        return ex["Id"]
    return client.post("vendor", {"DisplayName": name})["Vendor"]["Id"]


# ---------- dedup sets ----------
def purchase_keys(client):
    rows = client.query("SELECT TxnDate, TotalAmt, AccountRef FROM Purchase") \
                 .get("QueryResponse", {}).get("Purchase", [])
    return {(r["TxnDate"], d2(r["TotalAmt"]), r.get("AccountRef", {}).get("value")) for r in rows}


def deposit_keys(client):
    rows = client.query("SELECT TxnDate, TotalAmt FROM Deposit") \
                 .get("QueryResponse", {}).get("Deposit", [])
    return {(r["TxnDate"], d2(r["TotalAmt"])) for r in rows}


def transfer_keys(client):
    rows = client.query("SELECT TxnDate, Amount FROM Transfer") \
                 .get("QueryResponse", {}).get("Transfer", [])
    return {(r["TxnDate"], d2(r["Amount"])) for r in rows}


# ---------- builders ----------
def post_check(client, bank_id, date_s, amt, acct_id, memo, entity_id=None):
    body = {
        "PaymentType": "Check",
        "AccountRef": {"value": bank_id},
        "TxnDate": date_s,
        "TotalAmt": d2(amt),
        "PrivateNote": memo,
        "Line": [{"Amount": d2(amt), "DetailType": "AccountBasedExpenseLineDetail",
                  "AccountBasedExpenseLineDetail": {"AccountRef": {"value": acct_id}}}],
    }
    if entity_id:
        body["EntityRef"] = {"value": entity_id}
    try:
        return client.post("purchase", body)
    except QboError:
        body.pop("EntityRef", None)
        return client.post("purchase", body)


def post_deposit(client, bank_id, date_s, amt, acct_id, memo):
    body = {
        "DepositToAccountRef": {"value": bank_id},
        "TxnDate": date_s,
        "PrivateNote": memo,
        "Line": [{"Amount": d2(amt), "DetailType": "DepositLineDetail",
                  "DepositLineDetail": {"AccountRef": {"value": acct_id}}}],
    }
    return client.post("deposit", body)


def post_transfer(client, from_bank, to_bank, date_s, amt, memo):
    body = {"FromAccountRef": {"value": from_bank}, "ToAccountRef": {"value": to_bank},
            "Amount": d2(amt), "TxnDate": date_s, "PrivateNote": memo}
    return client.post("transfer", body)


# ---------- classification of a single bank txn ----------
def classify_account(t):
    """Return (kind, account_name, party_or_None) for a non-sale/non-purchase txn."""
    d = t.description
    dl = d.lower()
    if t.kind == "check":
        p = t.payee or ""
        if t.check_no == "0":
            return ("unknown", "Ask My Accountant", None)
        if p == "UPS":
            return ("expense", "Freight & Shipping", "UPS")
        if p == "Hilisong CA LLC":
            return ("expense", "Rent", "Hilisong CA LLC")
        return ("payroll", "Wages & Salaries", p)            # employee
    if t.kind == "cc":
        return ("owner_draw", "Owner's Draw - Qin Zhen", "Qin Zhen")
    if any(k in d for k in ("Irs", "IRS", "Edd", "Employment Devel")):
        party = "IRS" if "Irs" in d or "IRS" in d else "EDD"
        return ("tax", "Payroll Taxes", party)
    if "CA Dept Tax" in d or "cdtfa" in dl:
        return ("tax", "Taxes & Licenses", "CDTFA")
    if "seller" in dl and "permit" in dl:
        return ("expense", "Taxes & Licenses", "CDTFA")
    if t.kind == "fee" or "service charge" in dl:
        return ("expense", "Bank Service Charges", None)
    for needle, acct in [("OUTBACK", "Meals"), ("FEDEX", "Office Supplies"),
                         ("ARCO", "Auto/Fuel"), ("AMAZON", "Office Supplies")]:
        if needle in d.upper():
            return ("expense", acct, None)
    return ("unknown", "Ask My Accountant", None)


def is_own_transfer(d):
    dl = d.lower()
    return ("online transfer" in dl) or ("robotx inc" in dl and ("wire" in dl or "fedwire" in dl))


def main():
    creds = load_creds(Path(".env.robotx"))
    client = QboClient(creds)
    print(f"Connected: {creds.environment} realm={creds.realm_id}\n")

    # ---- accounts ----
    print("Ensuring accounts...")
    chase = ensure_account(client, "Chase Checking - 0108", "Bank", "Checking")
    ewb = ensure_account(client, "East West Checking - 6972", "Bank", "Checking")
    banks = {"chase": chase, "eastwest": ewb}
    acct = {
        "Wages & Salaries": ensure_account(client, "Wages & Salaries", "Expense", "PayrollExpenses"),
        "Payroll Taxes": ensure_account(client, "Payroll Taxes", "Expense", "PayrollExpenses"),
        "Taxes & Licenses": ensure_account(client, "Taxes & Licenses", "Expense", "TaxesPaid"),
        "Bank Service Charges": ensure_account(client, "Bank Service Charges", "Expense", "BankCharges"),
        "Rent": ensure_account(client, "Rent", "Expense", "RentOrLeaseOfBuildings"),
        "Freight & Shipping": ensure_account(client, "Freight & Shipping", "Expense", "ShippingFreightDelivery"),
        "Meals": ensure_account(client, "Meals", "Expense", "EntertainmentMeals"),
        "Office Supplies": ensure_account(client, "Office Supplies", "Expense", "OfficeGeneralAdministrativeExpenses"),
        "Auto/Fuel": ensure_account(client, "Auto/Fuel", "Expense", "Auto"),
        "Owner's Draw - Qin Zhen": ensure_account(client, "Owner's Draw - Qin Zhen", "Equity", "OwnersEquity"),
        "Ask My Accountant": ensure_account(client, "Ask My Accountant", "Other Current Asset", "OtherCurrentAssets"),
    }
    obe = ensure_account(client, "Opening Balance Equity", "Equity", "OpeningBalanceEquity")
    print("  done.\n")

    # ---- load statements ----
    txns = []
    for f in ["ewb-01.pdf", "ewb-02.pdf", "ewb-03.pdf", "ewb-04.pdf"]:
        txns += parse_eastwest(f"inputs/robotx/{f}")
    for f in ["chase-01.pdf", "chase-02.pdf", "chase-03.pdf", "chase-04.pdf"]:
        txns += parse_chase(f"inputs/robotx/{f}")

    stats = {"opening": 0, "inv_payment": 0, "bill_payment": 0, "check": 0,
             "deposit": 0, "transfer": 0, "errors": 0, "skipped": 0}

    # ---- opening balance JE (idempotent via DocNumber) ----
    existing_je = client.query("SELECT Id FROM JournalEntry WHERE DocNumber = 'OPEN-BAL'") \
                        .get("QueryResponse", {}).get("JournalEntry", [])
    if not existing_je:
        je = {
            "DocNumber": "OPEN-BAL", "TxnDate": "2025-12-31",
            "PrivateNote": "RobotX opening balances 12/31/2025",
            "Line": [
                {"Amount": "124998.00", "DetailType": "JournalEntryLineDetail",
                 "JournalEntryLineDetail": {"PostingType": "Debit", "AccountRef": {"value": chase}}},
                {"Amount": "338168.19", "DetailType": "JournalEntryLineDetail",
                 "JournalEntryLineDetail": {"PostingType": "Debit", "AccountRef": {"value": ewb}}},
                {"Amount": "463166.19", "DetailType": "JournalEntryLineDetail",
                 "JournalEntryLineDetail": {"PostingType": "Credit", "AccountRef": {"value": obe}}},
            ],
        }
        client.post("journalentry", je); stats["opening"] += 1
        print("Opening-balance JE posted ($463,166.19).")
    else:
        print("Opening-balance JE already present.")

    pk = purchase_keys(client)
    dk = deposit_keys(client)
    tk = transfer_keys(client)
    posted_transfers = set(tk)

    print("\nPosting bank transactions...")
    for t in txns:
        date_s = f"{t.date:%Y-%m-%d}"
        amt = abs(t.amount)
        bank = banks[t.account]
        dl = t.description.lower()
        try:
            # --- sales: pay the matching invoice (cash in) ---
            if t.amount > 0 and "algi" in dl:
                inv = client.query(
                    f"SELECT Id, TotalAmt, TxnDate FROM Invoice WHERE TxnDate = '{date_s}'"
                ).get("QueryResponse", {}).get("Invoice", [])
                inv = [i for i in inv if d2(i["TotalAmt"]) == d2(amt)]
                if not inv:
                    print(f"  [warn] no invoice to pay for {date_s} ${amt}"); stats["skipped"] += 1; continue
                pay = {"TxnDate": date_s, "TotalAmt": d2(amt),
                       "CustomerRef": inv[0].get("CustomerRef") or {"value": _find(client, "Customer", "DisplayName", "Algi Investment Inc")["Id"]},
                       "DepositToAccountRef": {"value": bank},
                       "Line": [{"Amount": d2(amt), "LinkedTxn": [{"TxnId": inv[0]["Id"], "TxnType": "Invoice"}]}]}
                client.post("payment", pay); stats["inv_payment"] += 1
                continue

            # --- purchases: pay the matching bill (cash out) ---
            is_supplier_wire = t.amount < 0 and any(s in dl for s in SUPPLIERS)
            is_thunder_check = t.kind == "check" and (t.payee or "").startswith("Thunder")
            if is_supplier_wire or is_thunder_check:
                bill = client.query(
                    f"SELECT Id, TotalAmt, VendorRef FROM Bill WHERE TxnDate = '{date_s}'"
                ).get("QueryResponse", {}).get("Bill", [])
                bill = [b for b in bill if d2(b["TotalAmt"]) == d2(amt)]
                if not bill:
                    print(f"  [warn] no bill to pay for {date_s} ${amt}"); stats["skipped"] += 1; continue
                bp = {"TxnDate": date_s, "TotalAmt": d2(amt),
                      "VendorRef": bill[0]["VendorRef"], "PayType": "Check",
                      "CheckPayment": {"BankAccountRef": {"value": bank}},
                      "Line": [{"Amount": d2(amt), "LinkedTxn": [{"TxnId": bill[0]["Id"], "TxnType": "Bill"}]}]}
                client.post("billpayment", bp); stats["bill_payment"] += 1
                continue

            # --- transfers (own accounts) ---
            if is_own_transfer(t.description):
                if "7880" in t.description:        # far side unknown -> park
                    if (date_s, d2(amt), bank) in pk:
                        stats["skipped"] += 1; continue
                    post_check(client, bank, date_s, amt, acct["Ask My Accountant"],
                               f"RobotX transfer to acct ...7880 [PENDING] | {t.description[:60]}")
                    pk.add((date_s, d2(amt), bank)); stats["check"] += 1; continue
                if (date_s, d2(amt)) in posted_transfers:
                    stats["skipped"] += 1; continue
                if t.amount < 0:
                    frm, to = bank, banks["chase" if t.account == "eastwest" else "eastwest"]
                else:
                    to, frm = bank, banks["chase" if t.account == "eastwest" else "eastwest"]
                post_transfer(client, frm, to, date_s, amt, f"RobotX transfer | {t.description[:60]}")
                posted_transfers.add((date_s, d2(amt))); stats["transfer"] += 1
                continue

            # --- everything else: classify to an account ---
            kind, acct_name, party = classify_account(t)
            entity_id = ensure_vendor(client, party) if party else None
            memo = f"RobotX {kind} | {t.payee or ''} | {t.description[:60]}"
            if t.amount > 0:
                if (date_s, d2(amt)) in dk:
                    stats["skipped"] += 1; continue
                post_deposit(client, bank, date_s, amt, acct[acct_name], memo)
                dk.add((date_s, d2(amt))); stats["deposit"] += 1
            else:
                if (date_s, d2(amt), bank) in pk:
                    stats["skipped"] += 1; continue
                post_check(client, bank, date_s, amt, acct[acct_name], memo, entity_id)
                pk.add((date_s, d2(amt), bank)); stats["check"] += 1
        except QboError as e:
            stats["errors"] += 1
            print(f"  ERROR {date_s} ${amt} {t.description[:35]} -> {str(e)[:140]}")

    print("\nStats:", stats)

    # ---- final bank balances ----
    print("\nFinal bank balances (target: Chase 403,349.48 / East West 5,526.52):")
    for name in ["Chase Checking - 0108", "East West Checking - 6972"]:
        r = client.query(f"SELECT CurrentBalance FROM Account WHERE Name = '{name}'") \
                  ["QueryResponse"]["Account"][0]
        print(f"  {name}: ${Decimal(str(r['CurrentBalance'])):,.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
