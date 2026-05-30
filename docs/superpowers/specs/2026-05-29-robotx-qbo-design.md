# RobotX Inc — QBO Books Design (Jan–Apr 2026)

**Date:** 2026-05-29
**Status:** Approved (design); pending spec review → implementation plan
**Company:** ROBOTX INC, 17901 Von Karman Ave, Irvine CA
**Scope:** Build full-accrual books in a **new QBO sandbox** from bank statements only.

---

## 1. Overview

RobotX is a **robotics import/trading business**: it wires money to Chinese
robot makers (Unitree/YuShu, Dobot, Booster, Pudu, Intbot, OpenLive, Thunder)
and receives large wires from customers/investors (Algi Investment). It pays
payroll (checks), federal/state taxes, rent, and an owner credit card.

We have **only bank statements** — no sales invoices, POs, or supplier bills.
The user chose **full-accrual books (option 3)**: sales → Invoices, purchases →
Bills with attached PDF, everything else booked to the correct account.

### Data sources (8 statements, 2 accounts, 98 transactions)

| Account | Bank | Acct # | Statements |
|---|---|---|---|
| **A** | JPMorgan Chase Platinum Business Checking | …29061**0108** | Jan/Feb/Mar/Apr 2026 |
| **B** | East West Bank Standard Business Checking | **86-32006972** | Jan/Feb/Mar/Apr 2026 |

| Account | Jan | Feb | Mar | Apr | Total |
|---|---|---|---|---|---|
| Chase …0108 | 0 | 4 | 6 | 9 | **19** |
| EWB 86-3200 | 15 | 15 | 27 | 22 | **79** |
| **Both** | 15 | 19 | 33 | 31 | **98** |

Balances roll forward cleanly (each month's ending = next month's beginning).
- Chase: 124,998.00 → 124,998.00 → 290,261.43 → 381,342.73 → **403,349.48**
- EWB: 338,168.19 → 248,479.63 → 41,106.28 → 41,336.73 → **5,526.52**

### Sandbox

- Realm: **9341457131230508** ("Sandbox Company US c209")
- Credentials in `.env.robotx` (gitignored), reusing the existing sandbox app
  keys. Connected + verified (CompanyInfo + refresh token both work).

---

## 2. Chart of Accounts

**Banks (2):** `Chase Checking …0108`, `East West Checking …6972`
**Income:** `Sales – Robots`
**COGS:** `Cost of Goods – Robots`, `Freight & Shipping`
**Expenses:** `Wages & Salaries`, `Payroll Taxes`, `Rent`, `Bank Service
Charges`, `Taxes & Licenses`, `Meals`, `Office Supplies`, `Auto/Fuel`
**Equity:** `Owner's Draw – Qin Zhen`, `Opening Balance Equity`
**Other current asset:** `Vendor Deposits`, `Ask My Accountant` (parking)

---

## 3. Opening balances

Book beginning balances **as of 12/31/2025** via Opening Balance Equity:
Chase **$124,998.00** + East West **$338,168.19** = **$463,166.19**.

---

## 4. Classification rules

### 4.1 Sales → Invoices (4) — needs invoice
Customer **Algi Investment Inc**. Each: **Invoice** + **Received Payment** on
the wire date, deposited to the receiving bank.

| Date | Acct | Amount | Memo |
|---|---|---|---|
| 03/11 | EWB | $37,500.00 | robotics purchase deposit |
| 03/23 | EWB | $58,500.00 | robot purchase |
| 03/25 | EWB | $491,863.05 | robot solution 2nd payment |
| 04/01 | Chase | $30,833.09 | Robot Purchase |

### 4.2 Purchases → Bills + attached PDF (8) — needs invoice
Each: **Bill** to vendor + **Bill Payment** from bank + **attached generated PDF**.

| Date | Acct | Vendor | Amount | Treatment |
|---|---|---|---|---|
| 01/29 | EWB | Shenzhen Dobot Corp | $2,849.00 | COGS – Robots |
| 01/30 | EWB | Booster Robotics | $7,049.00 | COGS – Robots |
| 02/20 | EWB | Intbot Inc | $25,000.00 | COGS – Robots |
| 02/27 | EWB | Pudu Robotics | $2,600.00 | COGS – Robots |
| 03/24 | EWB | OpenLive Technology | $91,200.00 | COGS – Robots |
| 03/20 | EWB | Thunder Inter Robotics Group (check #200) | $1,210.00 | COGS – Robots |
| 01/05 | EWB | YuShu Technology (Unitree) | $70,000.00 | **Vendor Deposits (asset)** — ⚠️ PENDING (see §6) |
| 03/27 | EWB | OpenLive Technology | $465,450.00 | **COGS – Robots** — ⚠️ PENDING (see §6) |

Robots booked to **COGS, not tracked inventory** (no qty/SKU data). Inventory
is a later upgrade if item counts are supplied.

### 4.3 Payroll → 33 checks (all East West)
Book each as **Wages & Salaries**, payee = employee (set up as Vendors for
per-person reporting). No QBO Payroll module in scope.

Employees (6): **Po Jen Yang, Kayisaier Feinila, Xiaoyu Li, Rucheng Zhou,
QingYang Wang, Richard Tong** (spelled "Foong" on check #184 — likely same).

### 4.4 Non-payroll checks (besides robot purchase #200)
| # | Payee | Amount | Account |
|---|---|---|---|
| 191 | UPS | $57.56 | Freight & Shipping |
| 192 | UPS | $437.83 | Freight & Shipping |
| 201 | Hilisong CA LLC | $6,600.00 | Rent |
| 0 | Robotx Inc (self) | $9,000.00 | **Ask My Accountant** — unknown (see §6) |

### 4.5 Owner's draw → 3
Chase credit-card autopay to **Qin Zhen (owner)** → **Owner's Draw**.
($1,189.81 / $628.79 / $2,605.21)

### 4.6 Transfers → matched pairs (no P&L)
| Date | Direction | Amount |
|---|---|---|
| 02/20 | EWB → Chase | $170,000.00 |
| 04/27 | EWB → Chase | $18,000.00 |
| 04/28 | Chase → EWB | $5,500.00 |
| 04/01 | Chase → …7880 (external acct, not in data) | $15,000.00 → Ask My Accountant |

### 4.7 Taxes → 11
IRS USATAXPYMT + EDD EFTPMT → **Payroll Taxes**. CDTFA + seller's permit →
**Taxes & Licenses**.

### 4.8 Other expenses
Bank charges → `Bank Service Charges` · Outback → `Meals` · FedEx → `Office
Supplies` · Arco → `Auto/Fuel` · Amazon → `Office Supplies`.

---

## 5. Code architecture

New **`robotx_qbo`** package, reusing the generic `tiktok_qbo.qbo` client
(auth/client/post/env — company-agnostic, creds from `.env.robotx`).

- **Parsers:** `ingest/chase_pdf.py`, `ingest/eastwest_pdf.py` (pdfplumber;
  East West check payees come from the page-3/4 scanned check images at native
  1200×550 res via pypdfium2).
- **Classifier:** rules in §4, producing a normalized transaction list.
- **PDF generator:** reportlab, one bill PDF per purchase.
- **Poster:** idempotent (DocNumber / PrivateNote keys, like the TikTok
  pipeline), **dry-run first**, then post to sandbox.

---

## 6. Unknowns → `Ask My Accountant` (the boss list)

10 transactions cannot be determined from the statements and park in
**`Ask My Accountant`**. #11 and #12 are **posted with best-guess treatment
(see §4.2) but remain on this list, tagged "PENDING BOSS CONFIRMATION"** in
their memo/PrivateNote.

| # | Date | Acct | Description | Amount | Question | Posted? |
|---|---|---|---|---|---|---|
| 1 | 03/23 | Chase | "Deposit" (no payer) | $95,000.00 | Who/what? | Ask My Accountant |
| 2 | 03/25 | Chase | "Deposit" (no payer) | $497,550.00 | Who/what? | Ask My Accountant |
| 3 | 03/30 | Chase | Wire → New American Title Co | $497,000.00 | Real-estate/escrow? | Ask My Accountant |
| 4 | 02/27 | EWB | COSCO Shipping credit | $2,500.00 | Refund/income? | Ask My Accountant |
| 5 | 03/26 | EWB | Teller cash withdrawal | $2,500.00 | Purpose? | Ask My Accountant |
| 6 | 04/03 | EWB | Teller cash withdrawal | $5,000.00 | Purpose? | Ask My Accountant |
| 7 | 04/28 | EWB | Mobile check deposit | $4,294.50 | Who/what? | Ask My Accountant |
| 8 | 04/17 | EWB | POS Shanghai Longqiao | $109.52 | Business/personal? | Ask My Accountant |
| 9 | 04/20 | EWB | POS Shanghai Longqiao | $269.36 | Business/personal? | Ask My Accountant |
| 10 | 04/13 | EWB | Check #0 → "Robotx Inc" (self) | $9,000.00 | Transfer/cash/owner? | Ask My Accountant |
| 11 | 01/05 | EWB | Wire → YuShu Technology | $70,000.00 | Refundable bond or purchase? | **Posted → Vendor Deposits, flagged** |
| 12 | 03/27 | EWB | Wire → OpenLive "Settlement fee Import" | $465,450.00 | Robot purchase or import settlement? | **Posted → COGS Bill, flagged** |

---

## 7. Out of scope (for now)

- Perpetual/tracked inventory (needs item counts/SKUs).
- QBO Payroll module (payroll booked as plain expense).
- Production QBO company (sandbox trial only).
- May 2026+ statements.
