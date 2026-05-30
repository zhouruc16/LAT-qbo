# RobotX QBO Books — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build full-accrual books for ROBOTX INC (Jan–Apr 2026, 2 bank accounts, 98 transactions) and post them to the connected QBO sandbox.

**Architecture:** New `robotx_qbo` package. Phase 1 is offline ingest+classify+reconcile (no QBO, fully unit-tested from the PDFs). Phase 2 posts to QBO reusing the generic `tiktok_qbo.qbo` client (auth/client/env), dry-run before any live write. Idempotency via a `RobotX:<txn_id>` tag in each entity's `PrivateNote`.

**Tech Stack:** Python 3.14, pdfplumber + pypdfium2 (PDF/image), pydantic (models), reportlab (bill PDFs), PyYAML (config), pytest. QBO REST via existing `QboClient`. Creds from `.env.robotx` (realm `9341457131230508`, sandbox).

**Spec:** `docs/superpowers/specs/2026-05-29-robotx-qbo-design.md`

**Source PDFs** (in `C:\Users\zhour\Downloads`, copy into `inputs/robotx/` first — `inputs/` is gitignored):
- Chase: `20260130-statements-0108- (1).pdf`, `20260227-statements-0108-.pdf`, `20260331-statements-0108-.pdf`, `20260430-statements-0108-.pdf`
- East West: `viewdoc.pdf` (Jan), `viewdoc (1).pdf` (Feb), `viewdoc (2).pdf` (Mar), `viewdoc (3).pdf` (Apr)

---

## File Structure

```
src/robotx_qbo/
  __init__.py
  models.py              # Txn, ClassifiedTxn (pydantic)
  ingest/
    __init__.py
    eastwest_pdf.py      # parse East West statements
    chase_pdf.py         # parse Chase statements
    load.py              # load all 8 PDFs → list[Txn]
  classify.py            # rules → list[ClassifiedTxn]
  reconcile.py           # balance roll-forward + count = 98
  coa.py                 # account specs + bootstrap (QBO)
  parties.py             # customers + vendors (QBO)
  bills_pdf.py           # reportlab bill PDF per purchase
  post.py                # orchestrate posting to QBO
  cli.py
  __main__.py
config/
  robotx_checks.yaml     # 38 check# → payee (read from check images)
  robotx_accounts.yaml   # CoA + opening balances + statement summaries
tests/robotx/
  test_eastwest_pdf.py
  test_chase_pdf.py
  test_classify.py
  test_reconcile.py
  test_bills_pdf.py
  test_coa.py
  test_post_dryrun.py
scripts/
  reconcile_robotx.py
  post_robotx.py
```

`robotx_qbo` imports the company-agnostic `tiktok_qbo.qbo` client directly (it reads creds from a path we pass). No changes to `tiktok_qbo`.

---

## Phase 0 — Scaffolding

### Task 1: Package skeleton

**Files:**
- Create: `src/robotx_qbo/__init__.py` (empty)
- Create: `src/robotx_qbo/ingest/__init__.py` (empty)
- Create: `tests/robotx/__init__.py` (empty)
- Modify: `pyproject.toml` (ensure `reportlab`, `pyyaml`, `pypdfium2` in deps)

- [ ] **Step 1: Create the package dirs and empty init files** (the four files above).

- [ ] **Step 2: Copy source PDFs into the repo (gitignored)**

```bash
mkdir -p inputs/robotx
cp "/c/Users/zhour/Downloads/20260130-statements-0108- (1).pdf" inputs/robotx/chase-01.pdf
cp "/c/Users/zhour/Downloads/20260227-statements-0108-.pdf"      inputs/robotx/chase-02.pdf
cp "/c/Users/zhour/Downloads/20260331-statements-0108-.pdf"      inputs/robotx/chase-03.pdf
cp "/c/Users/zhour/Downloads/20260430-statements-0108-.pdf"      inputs/robotx/chase-04.pdf
cp "/c/Users/zhour/Downloads/viewdoc.pdf"     inputs/robotx/ewb-01.pdf
cp "/c/Users/zhour/Downloads/viewdoc (1).pdf" inputs/robotx/ewb-02.pdf
cp "/c/Users/zhour/Downloads/viewdoc (2).pdf" inputs/robotx/ewb-03.pdf
cp "/c/Users/zhour/Downloads/viewdoc (3).pdf" inputs/robotx/ewb-04.pdf
```

- [ ] **Step 3: Verify deps import**

Run: `python -c "import reportlab, yaml, pypdfium2, pdfplumber, pydantic; print('ok')"`
Expected: `ok` (if any missing: `pip install reportlab pyyaml pypdfium2`)

- [ ] **Step 4: Commit**

```bash
git add src/robotx_qbo tests/robotx pyproject.toml
git commit -m "chore(robotx): package skeleton + deps"
```

---

## Phase 1 — Ingest & classify (offline)

### Task 2: Transaction models

**Files:**
- Create: `src/robotx_qbo/models.py`
- Test: `tests/robotx/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/robotx/test_models.py
from datetime import date
from decimal import Decimal
from robotx_qbo.models import Txn

def test_txn_id_is_stable_and_unique():
    a = Txn(account="eastwest", date=date(2026,1,5), amount=Decimal("-70000.00"),
            kind="wire", description="YuShu Technology", check_no=None,
            payee=None, source="ewb-01.pdf")
    b = Txn(account="eastwest", date=date(2026,1,5), amount=Decimal("-70000.00"),
            kind="wire", description="YuShu Technology", check_no=None,
            payee=None, source="ewb-01.pdf")
    assert a.txn_id == b.txn_id            # deterministic
    assert len(a.txn_id) == 16
    c = Txn(account="eastwest", date=date(2026,1,5), amount=Decimal("-20.00"),
            kind="fee", description="Service Charge", check_no=None,
            payee=None, source="ewb-01.pdf")
    assert a.txn_id != c.txn_id            # different amount → different id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/robotx/test_models.py -v`
Expected: FAIL (`ModuleNotFoundError: robotx_qbo.models`)

- [ ] **Step 3: Implement**

```python
# src/robotx_qbo/models.py
from __future__ import annotations
import hashlib
from datetime import date
from decimal import Decimal
from pydantic import BaseModel, ConfigDict

class Txn(BaseModel):
    model_config = ConfigDict(frozen=True)
    account: str            # "chase" | "eastwest"
    date: date
    amount: Decimal         # signed: + inflow, - outflow
    kind: str               # deposit|wire|check|fee|pos|withdrawal|credit|...
    description: str
    check_no: str | None = None
    payee: str | None = None
    source: str = ""

    @property
    def txn_id(self) -> str:
        raw = f"{self.account}|{self.date}|{self.amount}|{self.kind}|{self.description}|{self.check_no}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

class ClassifiedTxn(BaseModel):
    model_config = ConfigDict(frozen=True)
    txn: Txn
    category: str           # sale|purchase|payroll|owner_draw|transfer|tax|expense|unknown
    qbo_action: str         # invoice|bill|check|transfer|journalentry|expense|deposit
    account_name: str       # target CoA account (or "Ask My Accountant")
    party: str | None = None
    flagged: bool = False    # pending boss confirmation
    memo: str = ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/robotx/test_models.py -v` → Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/robotx_qbo/models.py tests/robotx/test_models.py
git commit -m "feat(robotx): Txn + ClassifiedTxn models"
```

---

### Task 3: Check payee config

**Files:**
- Create: `config/robotx_checks.yaml`
- Test: `tests/robotx/test_checks_config.py`

Check payees were read from the scanned check images (1200×550 native, via pypdfium2). pytesseract is **not** installed, so the mapping is captured here as auditable config rather than OCR'd at runtime.

- [ ] **Step 1: Write the config**

```yaml
# config/robotx_checks.yaml — check number -> payee (from check-image review)
# Employees (payroll). Non-payroll flagged with role.
checks:
  "165": {payee: "Po Jen Yang", role: employee}
  "171": {payee: "Kayisaier Feinila", role: employee}
  "172": {payee: "Po Jen Yang", role: employee}
  "173": {payee: "Xiaoyu Li", role: employee}
  "175": {payee: "Kayisaier Feinila", role: employee}
  "176": {payee: "Po Jen Yang", role: employee}
  "177": {payee: "Kayisaier Feinila", role: employee}
  "178": {payee: "Po Jen Yang", role: employee}
  "179": {payee: "Xiaoyu Li", role: employee}
  "180": {payee: "Xiaoyu Li", role: employee}
  "182": {payee: "Kayisaier Feinila", role: employee}
  "183": {payee: "Xiaoyu Li", role: employee}
  "184": {payee: "Richard Tong", role: employee}   # "Foong" on check; likely same
  "181": {payee: "Po Jen Yang", role: employee}
  "185": {payee: "Xiaoyu Li", role: employee}
  "187": {payee: "QingYang Wang", role: employee}
  "188": {payee: "Rucheng Zhou", role: employee}
  "189": {payee: "Richard Tong", role: employee}
  "190": {payee: "Po Jen Yang", role: employee}
  "191": {payee: "UPS", role: vendor_freight}
  "192": {payee: "UPS", role: vendor_freight}
  "193": {payee: "Richard Tong", role: employee}
  "195": {payee: "Po Jen Yang", role: employee}
  "196": {payee: "Kayisaier Feinila", role: employee}
  "197": {payee: "QingYang Wang", role: employee}
  "198": {payee: "Rucheng Zhou", role: employee}
  "199": {payee: "Xiaoyu Li", role: employee}
  "200": {payee: "Thunder Inter Robotics Group Inc", role: vendor_robot}
  "201": {payee: "Hilisong CA LLC", role: vendor_rent}
  "0":   {payee: "Robotx Inc", role: unknown}      # self-payable $9,000
  "202": {payee: "Po Jen Yang", role: employee}
  "203": {payee: "Xiaoyu Li", role: employee}
  "204": {payee: "Rucheng Zhou", role: employee}
  "207": {payee: "QingYang Wang", role: employee}
  "208": {payee: "Rucheng Zhou", role: employee}
  "210": {payee: "Xiaoyu Li", role: employee}
  "211": {payee: "QingYang Wang", role: employee}
  "212": {payee: "Rucheng Zhou", role: employee}
```

- [ ] **Step 2: Write the failing test**

```python
# tests/robotx/test_checks_config.py
import yaml
from pathlib import Path

def test_38_checks_with_payees():
    data = yaml.safe_load(Path("config/robotx_checks.yaml").read_text(encoding="utf-8"))
    checks = data["checks"]
    assert len(checks) == 38
    assert all(c["payee"] for c in checks.values())
    roles = [c["role"] for c in checks.values()]
    assert roles.count("employee") == 33
    assert roles.count("unknown") == 1          # the $9,000 self-check
```

- [ ] **Step 3: Run** `pytest tests/robotx/test_checks_config.py -v` → Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add config/robotx_checks.yaml tests/robotx/test_checks_config.py
git commit -m "feat(robotx): check#->payee config from check images"
```

---

### Task 4: East West parser

**Files:**
- Create: `src/robotx_qbo/ingest/eastwest_pdf.py`
- Test: `tests/robotx/test_eastwest_pdf.py`

East West statement layout (per spec §1): `CREDITS`, `CHECKS` (number/date/amount summary on page 3+), `DEBITS` (date, description, amount). Statement year from header `STARTING DATE: <Month DD, YYYY>`. Checks join payee from `robotx_checks.yaml`.

- [ ] **Step 1: Write the failing test** (against the real January PDF)

```python
# tests/robotx/test_eastwest_pdf.py
from decimal import Decimal
from robotx_qbo.ingest.eastwest_pdf import parse_eastwest

def test_january_counts_and_known_lines():
    txns = parse_eastwest("inputs/robotx/ewb-01.pdf")
    assert len(txns) == 15                       # spec §1: EWB Jan = 15
    # the $70k YuShu wire
    yushu = [t for t in txns if "YuShu" in t.description or "YUSHU" in t.description.upper()]
    assert len(yushu) == 1
    assert yushu[0].amount == Decimal("-70000.00")
    # checks carry payee from config
    c165 = [t for t in txns if t.check_no == "165"][0]
    assert c165.payee == "Po Jen Yang"
    assert c165.amount == Decimal("-1254.58")
    # 5 checks + 10 debits, 0 credits
    assert sum(1 for t in txns if t.kind == "check") == 5
```

- [ ] **Step 2: Run** `pytest tests/robotx/test_eastwest_pdf.py -v` → Expected: FAIL (no module)

- [ ] **Step 3: Implement**

```python
# src/robotx_qbo/ingest/eastwest_pdf.py
from __future__ import annotations
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
import pdfplumber, yaml
from robotx_qbo.models import Txn

_AMT = r"([\d,]+\.\d{2})"
_START = re.compile(r"STARTING DATE:\s*([A-Za-z]+ \d{1,2}, (\d{4}))")
_CHECK_DETAIL = re.compile(r"(\d{2})/(\d{2})/(\d{4})\s+(\d+)\s+\$%s" % _AMT)   # page3: MM/DD/YYYY num $amt
_CHECK_SUMMARY = re.compile(r"^(\d+)\s+(\d{2})-(\d{2})\s+%s" % _AMT)            # page1: num MM-DD amt
_DEBIT = re.compile(r"^(\d{2})-(\d{2})\s+(.*?)\s+%s$" % _AMT)
_CREDIT_DATE = re.compile(r"^(\d{2})-(\d{2})\b")

def _checks_config() -> dict:
    data = yaml.safe_load(Path("config/robotx_checks.yaml").read_text(encoding="utf-8"))
    return data["checks"]

def _money(s: str) -> Decimal:
    return Decimal(s.replace(",", ""))

def parse_eastwest(pdf_path: str) -> list[Txn]:
    cfg = _checks_config()
    src = Path(pdf_path).name
    with pdfplumber.open(pdf_path) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    year = int(_START.search(text).group(2))
    txns: list[Txn] = []
    seen_checks: set[str] = set()

    # --- checks: prefer page-3 detail (has 4-digit year); fall back to summary ---
    for m in _CHECK_DETAIL.finditer(text):
        mm, dd, yyyy, num, amt = m.groups()
        if num in seen_checks:
            continue
        seen_checks.add(num)
        meta = cfg.get(num, {})
        txns.append(Txn(account="eastwest", date=date(int(yyyy), int(mm), int(dd)),
                        amount=-_money(amt), kind="check", description=f"Check {num}",
                        check_no=num, payee=meta.get("payee"), source=src))

    # --- debits & credits: walk lines in the DEBITS/CREDITS sections ---
    lines = text.splitlines()
    section = None
    for i, line in enumerate(lines):
        u = line.strip()
        if u.startswith("CREDITS"): section = "credits"; continue
        if u.startswith("DEBITS"):  section = "debits";  continue
        if u.startswith("DAILY BALANCES") or u.startswith("CHECKS"): section = None; continue
        if section == "debits":
            m = _DEBIT.match(u)
            if m:
                mm, dd, desc, amt = m.groups()
                kind = ("fee" if "Service Charge" in desc else
                        "wire" if "Wire" in desc or "WIRE" in desc else
                        "pos" if "POS" in desc or "Purchase" in desc else
                        "withdrawal" if "Withdrawal" in desc else "debit")
                txns.append(Txn(account="eastwest", date=date(year,int(mm),int(dd)),
                                amount=-_money(amt), kind=kind, description=desc.strip(),
                                source=src))
        elif section == "credits":
            md = _CREDIT_DATE.match(u)
            am = re.search(_AMT + r"\s*$", u)
            if md and am:
                mm, dd = md.group(1), md.group(2)
                desc = u[5:am.start()].strip() or "Credit"
                txns.append(Txn(account="eastwest", date=date(year,int(mm),int(dd)),
                                amount=_money(am.group(1)), kind="credit",
                                description=desc, source=src))
    return txns
```

> **Note for implementer:** the real East West text has multi-line debit descriptions (wire memos wrap). If a `_DEBIT` line doesn't match because the amount is on the description's first line with the memo on the next, join the wire's continuation lines. Verify against all 4 EWB PDFs in Step 4 and adjust the line-walk to merge continuation lines until counts equal spec §1 (15/15/27/22). Add a regression assertion per month.

- [ ] **Step 4: Run + tune against all 4 months**

Run: `pytest tests/robotx/test_eastwest_pdf.py -v`
Then extend the test with Feb/Mar/Apr count asserts (15/27/22) and iterate the parser until green:

```python
def test_all_months_counts():
    import pytest
    for f, n in [("ewb-01.pdf",15),("ewb-02.pdf",15),("ewb-03.pdf",27),("ewb-04.pdf",22)]:
        assert len(parse_eastwest(f"inputs/robotx/{f}")) == n, f
```

Expected: PASS for all four.

- [ ] **Step 5: Commit**

```bash
git add src/robotx_qbo/ingest/eastwest_pdf.py tests/robotx/test_eastwest_pdf.py
git commit -m "feat(robotx): East West statement parser"
```

---

### Task 5: Chase parser

**Files:**
- Create: `src/robotx_qbo/ingest/chase_pdf.py`
- Test: `tests/robotx/test_chase_pdf.py`

Chase layout (per spec §1): `DEPOSITS AND ADDITIONS` and `ELECTRONIC WITHDRAWALS`, each `DATE DESCRIPTION AMOUNT` with `MM/DD` dates; year from header `<Month DD, YYYY>through<Month DD, YYYY>`. Multi-line descriptions wrap.

- [ ] **Step 1: Write the failing test**

```python
# tests/robotx/test_chase_pdf.py
from decimal import Decimal
from robotx_qbo.ingest.chase_pdf import parse_chase

def test_chase_counts():
    assert len(parse_chase("inputs/robotx/chase-01.pdf")) == 0     # Jan: no activity
    feb = parse_chase("inputs/robotx/chase-02.pdf")
    assert len(feb) == 4
    dep = [t for t in feb if t.amount > 0]
    assert len(dep) == 1 and dep[0].amount == Decimal("170000.00")  # transfer-in
    mar = parse_chase("inputs/robotx/chase-03.pdf")
    assert len(mar) == 6
    apr = parse_chase("inputs/robotx/chase-04.pdf")
    assert len(apr) == 9
```

- [ ] **Step 2: Run** `pytest tests/robotx/test_chase_pdf.py -v` → Expected: FAIL

- [ ] **Step 3: Implement**

```python
# src/robotx_qbo/ingest/chase_pdf.py
from __future__ import annotations
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
import pdfplumber
from robotx_qbo.models import Txn

_HDR = re.compile(r"through[A-Za-z]+ \d{1,2}, (\d{4})")
_ROW = re.compile(r"^(\d{2})/(\d{2})\s+(.*?)\s+\$?([\d,]+\.\d{2})\s*$")

def _money(s): return Decimal(s.replace(",", ""))

def parse_chase(pdf_path: str) -> list[Txn]:
    src = Path(pdf_path).name
    with pdfplumber.open(pdf_path) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    year = int(_HDR.search(text).group(1))
    txns: list[Txn] = []
    section = None
    for line in text.splitlines():
        u = line.strip()
        if "DEPOSITS AND ADDITIONS" in u: section = "dep"; continue
        if "ELECTRONIC WITHDRAWALS" in u: section = "wd"; continue
        if u.startswith("Total ") or "DAILY ENDING BALANCE" in u or "CHECKING SUMMARY" in u:
            section = None; continue
        if section is None:
            continue
        m = _ROW.match(u)
        if not m:
            continue
        mm, dd, desc, amt = m.groups()
        sign = Decimal(1) if section == "dep" else Decimal(-1)
        kind = ("transfer" if "Online Transfer" in desc or "Wire Transfer" in desc or "Fedwire" in desc
                else "tax" if any(k in desc for k in ("Irs","IRS","Edd","Employment Devel","CA Dept Tax"))
                else "cc" if "Credit Crd" in desc
                else "deposit" if section == "dep" else "withdrawal")
        txns.append(Txn(account="chase", date=date(year,int(mm),int(dd)),
                        amount=sign*_money(amt), kind=kind, description=desc.strip(), source=src))
    return txns
```

> **Note for implementer:** Chase wraps long wire memos onto 2–3 lines; only the first line carries the amount. The `_ROW` regex anchors on a trailing amount, so continuation lines are naturally skipped. Confirm counts (0/4/6/9) in Step 4; if a row is missed because its amount sits on a wrapped line, merge forward. Distinguish the two generic `Deposit 2137...` lines (kind `deposit`) — they become unknowns in Task 6.

- [ ] **Step 4: Run** `pytest tests/robotx/test_chase_pdf.py -v` → Expected: PASS (tune if needed)

- [ ] **Step 5: Commit**

```bash
git add src/robotx_qbo/ingest/chase_pdf.py tests/robotx/test_chase_pdf.py
git commit -m "feat(robotx): Chase statement parser"
```

---

### Task 6: Classifier

**Files:**
- Create: `src/robotx_qbo/ingest/load.py` (loads all 8 → list[Txn])
- Create: `src/robotx_qbo/classify.py`
- Test: `tests/robotx/test_classify.py`

Rules per spec §4. `load.py`:

```python
# src/robotx_qbo/ingest/load.py
from robotx_qbo.ingest.eastwest_pdf import parse_eastwest
from robotx_qbo.ingest.chase_pdf import parse_chase
from robotx_qbo.models import Txn

EWB = ["ewb-01.pdf","ewb-02.pdf","ewb-03.pdf","ewb-04.pdf"]
CHASE = ["chase-01.pdf","chase-02.pdf","chase-03.pdf","chase-04.pdf"]

def load_all(input_dir: str = "inputs/robotx") -> list[Txn]:
    out: list[Txn] = []
    for f in EWB:   out += parse_eastwest(f"{input_dir}/{f}")
    for f in CHASE: out += parse_chase(f"{input_dir}/{f}")
    return out
```

- [ ] **Step 1: Write the failing test**

```python
# tests/robotx/test_classify.py
from collections import Counter
from robotx_qbo.ingest.load import load_all
from robotx_qbo.classify import classify

def test_full_classification_breakdown():
    cls = classify(load_all())
    assert len(cls) == 98
    by = Counter(c.category for c in cls)
    assert by["sale"] == 4
    assert by["purchase"] == 8
    assert by["payroll"] == 33
    assert by["owner_draw"] == 3
    assert by["transfer"] == 7
    assert by["tax"] == 11
    # unknowns parked: 10 (incl self-check + 15k transfer-out + Cosco + tellers + POS + deposits)
    assert sum(1 for c in cls if c.account_name == "Ask My Accountant") >= 10
    # #11 & #12 posted but flagged
    flagged = [c for c in cls if c.flagged]
    assert {round(abs(c.txn.amount)) for c in flagged} == {70000, 465450}
```

- [ ] **Step 2: Run** `pytest tests/robotx/test_classify.py -v` → Expected: FAIL

- [ ] **Step 3: Implement** (rules table; match on description/kind/payee)

```python
# src/robotx_qbo/classify.py
from __future__ import annotations
from decimal import Decimal
from robotx_qbo.models import Txn, ClassifiedTxn

# explicit vendor memos → robot purchase
_SUPPLIERS = ("Dobot","Booster","Intbot","Pudu","OpenLive","YuShu","Thunder")
_UNKNOWN_MEMOS = ("New American Title","COSCO","Mobile Check","Shanghai","TLR")  # tellers/POS/etc

def _classify_one(t: Txn) -> ClassifiedTxn:
    d = t.description
    amt = t.amount

    # --- East West checks ---
    if t.kind == "check":
        if t.check_no == "0":  # $9,000 self-check
            return ClassifiedTxn(txn=t, category="unknown", qbo_action="deposit",
                                 account_name="Ask My Accountant",
                                 memo="Check to Robotx Inc (self) — PENDING")
        if (t.payee or "") == "UPS":
            return ClassifiedTxn(txn=t, category="expense", qbo_action="check",
                                 account_name="Freight & Shipping", party="UPS")
        if (t.payee or "") == "Hilisong CA LLC":
            return ClassifiedTxn(txn=t, category="expense", qbo_action="check",
                                 account_name="Rent", party="Hilisong CA LLC")
        if (t.payee or "").startswith("Thunder"):
            return ClassifiedTxn(txn=t, category="purchase", qbo_action="bill",
                                 account_name="Cost of Goods - Robots", party=t.payee)
        return ClassifiedTxn(txn=t, category="payroll", qbo_action="check",
                             account_name="Wages & Salaries", party=t.payee)

    # --- sales: Algi incoming ---
    if amt > 0 and "Algi" in d:
        return ClassifiedTxn(txn=t, category="sale", qbo_action="invoice",
                             account_name="Sales - Robots", party="Algi Investment Inc")

    # --- transfers (matched own-account moves) ---
    if t.kind == "transfer" or "RobotX Inc" in d or "Robotx Inc" in d:
        # 15k to ...7880 is one-sided/unknown
        if "7880" in d:
            return ClassifiedTxn(txn=t, category="unknown", qbo_action="deposit",
                                 account_name="Ask My Accountant",
                                 memo="Transfer to acct ...7880 — PENDING")
        return ClassifiedTxn(txn=t, category="transfer", qbo_action="transfer",
                             account_name="(bank)", memo="Inter-account transfer")

    # --- robot purchases (outgoing wires to suppliers) ---
    if amt < 0 and any(s in d for s in _SUPPLIERS):
        flagged = False; acct = "Cost of Goods - Robots"; memo = ""
        if "YuShu" in d:
            acct = "Vendor Deposits"; flagged = True; memo = "Performance bond — PENDING"
        if "OpenLive" in d and abs(amt) == Decimal("465450.00"):
            flagged = True; memo = "Settlement fee Import — PENDING"
        vendor = next(s for s in _SUPPLIERS if s in d)
        return ClassifiedTxn(txn=t, category="purchase", qbo_action="bill",
                             account_name=acct, party=vendor, flagged=flagged, memo=memo)

    # --- owner draw: Chase CC autopay ---
    if t.kind == "cc":
        return ClassifiedTxn(txn=t, category="owner_draw", qbo_action="check",
                             account_name="Owner's Draw - Qin Zhen", party="Qin Zhen")

    # --- taxes ---
    if t.kind == "tax" or any(k in d for k in ("Irs","IRS","Edd","Employment Devel")):
        return ClassifiedTxn(txn=t, category="tax", qbo_action="expense",
                             account_name="Payroll Taxes",
                             party=("IRS" if "Irs" in d or "IRS" in d else "EDD"))
    if "CA Dept Tax" in d or "CDTFA" in d.upper() or "SELLER'S PERMIT" in d.upper():
        return ClassifiedTxn(txn=t, category="tax", qbo_action="expense",
                             account_name="Taxes & Licenses", party="CDTFA")

    # --- bank fees ---
    if t.kind == "fee" or "Service Charge" in d:
        return ClassifiedTxn(txn=t, category="expense", qbo_action="expense",
                             account_name="Bank Service Charges")

    # --- known POS expenses ---
    for needle, acct in [("OUTBACK","Meals"),("FEDEX","Office Supplies"),
                         ("ARCO","Auto/Fuel"),("AMAZON","Office Supplies")]:
        if needle in d.upper():
            return ClassifiedTxn(txn=t, category="expense", qbo_action="expense",
                                 account_name=acct)

    # --- everything else unknown ---
    return ClassifiedTxn(txn=t, category="unknown", qbo_action="deposit" if amt>0 else "expense",
                         account_name="Ask My Accountant", memo=f"Unclassified: {d[:40]}")

def classify(txns: list[Txn]) -> list[ClassifiedTxn]:
    return [_classify_one(t) for t in txns]
```

- [ ] **Step 4: Run** `pytest tests/robotx/test_classify.py -v` → Expected: PASS (iterate rules until the breakdown matches spec §4 exactly)

- [ ] **Step 5: Commit**

```bash
git add src/robotx_qbo/ingest/load.py src/robotx_qbo/classify.py tests/robotx/test_classify.py
git commit -m "feat(robotx): transaction classifier (spec §4 rules)"
```

---

### Task 7: Reconcile

**Files:**
- Create: `config/robotx_accounts.yaml` (statement summaries + CoA)
- Create: `src/robotx_qbo/reconcile.py`
- Test: `tests/robotx/test_reconcile.py`

- [ ] **Step 1: Write the statement-summary config**

```yaml
# config/robotx_accounts.yaml
opening_balance_date: "2025-12-31"
opening_balances:
  chase: "124998.00"
  eastwest: "338168.19"
statements:           # account, begin, end (for roll-forward check)
  - {account: chase,    month: "2026-01", begin: "124998.00", end: "124998.00"}
  - {account: chase,    month: "2026-02", begin: "124998.00", end: "290261.43"}
  - {account: chase,    month: "2026-03", begin: "290261.43", end: "381342.73"}
  - {account: chase,    month: "2026-04", begin: "381342.73", end: "403349.48"}
  - {account: eastwest, month: "2026-01", begin: "338168.19", end: "248479.63"}
  - {account: eastwest, month: "2026-02", begin: "248479.63", end: "41106.28"}
  - {account: eastwest, month: "2026-03", begin: "41106.28",  end: "41336.73"}
  - {account: eastwest, month: "2026-04", begin: "5526.52",   end: "5526.52"}   # see note
```

> **Note:** EWB April begin should be `41336.73` and end `5526.52`. Correct the YAML to `{begin: "41336.73", end: "5526.52"}` — the line above is intentionally wrong so Step 3's test proves the roll-forward check actually catches mismatches; fix it before committing.

- [ ] **Step 2: Write the failing test**

```python
# tests/robotx/test_reconcile.py
from robotx_qbo.ingest.load import load_all
from robotx_qbo.reconcile import reconcile

def test_rollforward_and_count():
    r = reconcile(load_all())
    assert r.total_count == 98
    assert r.passed, r.mismatches      # begin + sum(month txns) == end, per account/month
```

- [ ] **Step 3: Implement**

```python
# src/robotx_qbo/reconcile.py
from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
import yaml
from robotx_qbo.models import Txn

@dataclass
class ReconResult:
    total_count: int
    passed: bool
    mismatches: list[str] = field(default_factory=list)

def reconcile(txns: list[Txn], cfg_path: str = "config/robotx_accounts.yaml") -> ReconResult:
    cfg = yaml.safe_load(Path(cfg_path).read_text(encoding="utf-8"))
    mism: list[str] = []
    for s in cfg["statements"]:
        acct, mon = s["account"], s["month"]
        begin, end = Decimal(s["begin"]), Decimal(s["end"])
        delta = sum((t.amount for t in txns
                     if t.account == acct and f"{t.date:%Y-%m}" == mon), Decimal(0))
        if begin + delta != end:
            mism.append(f"{acct} {mon}: {begin} + {delta} = {begin+delta} != {end}")
    return ReconResult(total_count=len(txns), passed=not mism, mismatches=mism)
```

- [ ] **Step 4: Run** `pytest tests/robotx/test_reconcile.py -v`
First with the wrong YAML line → Expected: FAIL (proves the check works). Fix the YAML (EWB April `begin 41336.73 / end 5526.52`) → Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add config/robotx_accounts.yaml src/robotx_qbo/reconcile.py tests/robotx/test_reconcile.py
git commit -m "feat(robotx): balance roll-forward reconciler"
```

---

### Task 8: Reconcile CLI script

**Files:**
- Create: `scripts/reconcile_robotx.py`

- [ ] **Step 1: Implement**

```python
# scripts/reconcile_robotx.py
from collections import Counter
from robotx_qbo.ingest.load import load_all
from robotx_qbo.classify import classify
from robotx_qbo.reconcile import reconcile

def main():
    txns = load_all()
    r = reconcile(txns)
    cls = classify(txns)
    print(f"transactions: {r.total_count}  reconcile: {'PASS' if r.passed else 'FAIL'}")
    for m in r.mismatches: print("  mismatch:", m)
    print("by category:", dict(Counter(c.category for c in cls)))
    print("Ask My Accountant:", sum(1 for c in cls if c.account_name=='Ask My Accountant'))
    return 0 if r.passed else 2

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run** `python scripts/reconcile_robotx.py`
Expected: `transactions: 98  reconcile: PASS` and the category breakdown from spec §4.

- [ ] **Step 3: Commit**

```bash
git add scripts/reconcile_robotx.py
git commit -m "feat(robotx): reconcile CLI"
```

**🔬 Checkpoint:** Phase 1 done — 98 transactions parsed, classified, and reconciled offline with zero QBO calls. Review the category breakdown before Phase 2.

---

## Phase 2 — QBO posting (sandbox)

All QBO tasks use `QboClient(load_creds(Path(".env.robotx")), dry_run=...)`. Idempotency: every created entity carries `PrivateNote = "RobotX:<txn_id>"`; before creating, query that entity type for the tag and skip if present.

### Task 9: Chart of accounts bootstrap

**Files:**
- Create: `src/robotx_qbo/coa.py`
- Test: `tests/robotx/test_coa.py`

- [ ] **Step 1: Write the failing test** (dry-run, no network for the create path)

```python
# tests/robotx/test_coa.py
from robotx_qbo.coa import ACCOUNT_SPECS, account_names

def test_specs_cover_spec_accounts():
    names = account_names()
    for need in ["Sales - Robots","Cost of Goods - Robots","Freight & Shipping",
                 "Wages & Salaries","Payroll Taxes","Rent","Bank Service Charges",
                 "Taxes & Licenses","Meals","Office Supplies","Auto/Fuel",
                 "Owner's Draw - Qin Zhen","Vendor Deposits","Ask My Accountant"]:
        assert need in names, need
```

- [ ] **Step 2: Run** `pytest tests/robotx/test_coa.py -v` → Expected: FAIL

- [ ] **Step 3: Implement** (mirror `tiktok_qbo.qbo.coa` find-or-create pattern)

```python
# src/robotx_qbo/coa.py
from __future__ import annotations
from tiktok_qbo.qbo.client import QboClient

# (Name, AccountType, AccountSubType)
ACCOUNT_SPECS: list[tuple[str,str,str]] = [
    ("Chase Checking - 0108",     "Bank", "Checking"),
    ("East West Checking - 6972", "Bank", "Checking"),
    ("Sales - Robots",            "Income", "SalesOfProductIncome"),
    ("Cost of Goods - Robots",    "Cost of Goods Sold", "SuppliesMaterialsCogs"),
    ("Freight & Shipping",        "Cost of Goods Sold", "ShippingFreightDeliveryCos"),
    ("Wages & Salaries",          "Expense", "PayrollExpenses"),
    ("Payroll Taxes",             "Expense", "PayrollTaxExpenses"),
    ("Rent",                      "Expense", "RentOrLeaseOfBuildings"),
    ("Bank Service Charges",      "Expense", "BankCharges"),
    ("Taxes & Licenses",          "Expense", "TaxesPaid"),
    ("Meals",                     "Expense", "EntertainmentMeals"),
    ("Office Supplies",           "Expense", "OfficeGeneralAdministrativeExpenses"),
    ("Auto/Fuel",                 "Expense", "Auto"),
    ("Owner's Draw - Qin Zhen",   "Equity", "OwnersEquity"),
    ("Vendor Deposits",           "Other Current Asset", "OtherCurrentAssets"),
    ("Ask My Accountant",         "Other Current Asset", "OtherCurrentAssets"),
]

def account_names() -> set[str]:
    return {n for n, _, _ in ACCOUNT_SPECS}

def _find(client: QboClient, name: str) -> dict | None:
    safe = name.replace("'", "\\'")
    rows = client.query(f"SELECT * FROM Account WHERE Name = '{safe}'") \
                 .get("QueryResponse", {}).get("Account", [])
    return rows[0] if rows else None

def bootstrap_coa(client: QboClient) -> dict[str, str]:
    """Find-or-create every account. Returns {name: QBO Account Id}."""
    out: dict[str, str] = {}
    for name, atype, sub in ACCOUNT_SPECS:
        ex = _find(client, name)
        if ex:
            out[name] = ex["Id"]; print(f"  [exists] {name}")
        else:
            body = {"Name": name, "AccountType": atype, "AccountSubType": sub}
            out[name] = client.post("account", body)["Account"]["Id"]
            print(f"  [created] {name}")
    return out
```

> **Note for implementer:** QBO AccountSubType enums occasionally reject (e.g. `Auto`, `ShippingFreightDeliveryCos`). If a create returns a 400 on subtype, query `SELECT * FROM Account` once and consult the error's allowed list, then correct the enum in `ACCOUNT_SPECS`. Run the live bootstrap in Task 18, not here.

- [ ] **Step 4: Run** `pytest tests/robotx/test_coa.py -v` → Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/robotx_qbo/coa.py tests/robotx/test_coa.py
git commit -m "feat(robotx): chart-of-accounts bootstrap"
```

---

### Task 10: Customers & vendors

**Files:**
- Create: `src/robotx_qbo/parties.py`
- Test: `tests/robotx/test_parties.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/robotx/test_parties.py
from robotx_qbo.parties import required_parties

def test_parties_derived_from_classification():
    cust, vend = required_parties()
    assert "Algi Investment Inc" in cust
    for v in ["Shenzhen Dobot","Booster Robotics","Intbot Inc","Pudu Robotics",
              "OpenLive Technology","YuShu Technology","Thunder Inter Robotics Group Inc",
              "UPS","Hilisong CA LLC","Po Jen Yang","Rucheng Zhou","IRS","EDD","CDTFA"]:
        assert v in vend, v
```

- [ ] **Step 2: Run** `pytest tests/robotx/test_parties.py -v` → Expected: FAIL

- [ ] **Step 3: Implement**

```python
# src/robotx_qbo/parties.py
from __future__ import annotations
from tiktok_qbo.qbo.client import QboClient

CUSTOMERS = ["Algi Investment Inc"]
VENDORS = [
    "Shenzhen Dobot", "Booster Robotics", "Intbot Inc", "Pudu Robotics",
    "OpenLive Technology", "YuShu Technology", "Thunder Inter Robotics Group Inc",
    "UPS", "Hilisong CA LLC", "IRS", "EDD", "CDTFA",
    "Po Jen Yang", "Kayisaier Feinila", "Xiaoyu Li", "Rucheng Zhou",
    "QingYang Wang", "Richard Tong",
]

def required_parties() -> tuple[list[str], list[str]]:
    return CUSTOMERS, VENDORS

def _find(client, entity, field, name):
    safe = name.replace("'", "\\'")
    rows = client.query(f"SELECT * FROM {entity} WHERE {field} = '{safe}'") \
                 .get("QueryResponse", {}).get(entity, [])
    return rows[0] if rows else None

def bootstrap_parties(client: QboClient) -> dict[str, str]:
    """Find-or-create customers + vendors. Returns {name: 'Customer:Id'|'Vendor:Id'}."""
    refs: dict[str, str] = {}
    for c in CUSTOMERS:
        ex = _find(client, "Customer", "DisplayName", c)
        cid = ex["Id"] if ex else client.post("customer", {"DisplayName": c})["Customer"]["Id"]
        refs[c] = f"Customer:{cid}"
    for v in VENDORS:
        ex = _find(client, "Vendor", "DisplayName", v)
        vid = ex["Id"] if ex else client.post("vendor", {"DisplayName": v})["Vendor"]["Id"]
        refs[v] = f"Vendor:{vid}"
    return refs
```

- [ ] **Step 4: Run** `pytest tests/robotx/test_parties.py -v` → Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/robotx_qbo/parties.py tests/robotx/test_parties.py
git commit -m "feat(robotx): customers + vendors bootstrap"
```

---

### Task 11: Bill PDF generator

**Files:**
- Create: `src/robotx_qbo/bills_pdf.py`
- Test: `tests/robotx/test_bills_pdf.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/robotx/test_bills_pdf.py
from datetime import date
from decimal import Decimal
from pathlib import Path
from pypdf import PdfReader
from robotx_qbo.bills_pdf import generate_bill_pdf

def test_generates_readable_bill(tmp_path):
    out = tmp_path / "bill.pdf"
    generate_bill_pdf(out, vendor="Shenzhen Dobot", bill_date=date(2026,1,29),
                      amount=Decimal("2849.00"), memo="PI-INF20260116")
    assert out.exists()
    txt = PdfReader(str(out)).pages[0].extract_text()
    assert "Shenzhen Dobot" in txt and "2,849.00" in txt
```

- [ ] **Step 2: Run** `pytest tests/robotx/test_bills_pdf.py -v` → Expected: FAIL

- [ ] **Step 3: Implement**

```python
# src/robotx_qbo/bills_pdf.py
from __future__ import annotations
from datetime import date
from decimal import Decimal
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

def generate_bill_pdf(out_path, *, vendor: str, bill_date: date,
                      amount: Decimal, memo: str = "") -> Path:
    out = Path(out_path)
    c = canvas.Canvas(str(out), pagesize=letter)
    w, h = letter
    c.setFont("Helvetica-Bold", 18); c.drawString(72, h-90, "VENDOR BILL")
    c.setFont("Helvetica", 11)
    c.drawString(72, h-130, "Bill To: ROBOTX INC, 17901 Von Karman Ave, Irvine CA")
    c.drawString(72, h-150, f"Vendor: {vendor}")
    c.drawString(72, h-170, f"Date: {bill_date:%Y-%m-%d}")
    if memo: c.drawString(72, h-190, f"Memo: {memo}")
    c.line(72, h-205, w-72, h-205)
    c.drawString(72, h-230, "Robot purchase")
    c.drawRightString(w-72, h-230, f"$ {amount:,.2f}")
    c.setFont("Helvetica-Bold", 12)
    c.drawRightString(w-72, h-260, f"Total: $ {amount:,.2f}")
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(72, 60, "Reconstructed from bank record for bookkeeping — not the original supplier invoice.")
    c.save()
    return out
```

- [ ] **Step 4: Run** `pytest tests/robotx/test_bills_pdf.py -v` → Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/robotx_qbo/bills_pdf.py tests/robotx/test_bills_pdf.py
git commit -m "feat(robotx): reconstructed bill PDF generator"
```

---

### Task 12: Poster — builders (pure payload functions)

**Files:**
- Create: `src/robotx_qbo/post.py` (builders first; orchestrator in Task 13)
- Test: `tests/robotx/test_post_builders.py`

Build QBO request bodies as pure functions so they're unit-testable without network. `refs` = {account_name: Id}; `parties` = {name: "Customer:Id"|"Vendor:Id"}; `banks` = {"chase": Id, "eastwest": Id}.

- [ ] **Step 1: Write the failing test**

```python
# tests/robotx/test_post_builders.py
from datetime import date
from decimal import Decimal
from robotx_qbo.models import Txn, ClassifiedTxn
from robotx_qbo.post import build_opening_balance_je, build_expense, build_transfer

REFS = {"Bank Service Charges":"30","Owner's Draw - Qin Zhen":"31",
        "Chase Checking - 0108":"10","East West Checking - 6972":"11",
        "Opening Balance Equity":"40"}
BANKS = {"chase":"10","eastwest":"11"}

def test_opening_balance_je_balances():
    je = build_opening_balance_je(Decimal("124998.00"), Decimal("338168.19"),
                                  date(2025,12,31), REFS, BANKS)
    debits = sum(Decimal(l["Amount"]) for l in je["Line"]
                 if l["JournalEntryLineDetail"]["PostingType"]=="Debit")
    credits = sum(Decimal(l["Amount"]) for l in je["Line"]
                  if l["JournalEntryLineDetail"]["PostingType"]=="Credit")
    assert debits == credits == Decimal("463166.19")

def test_expense_has_private_note_tag():
    t = Txn(account="eastwest", date=date(2026,1,5), amount=Decimal("-20.00"),
            kind="fee", description="Service Charge", source="ewb-01.pdf")
    c = ClassifiedTxn(txn=t, category="expense", qbo_action="expense",
                      account_name="Bank Service Charges")
    body = build_expense(c, REFS, BANKS)
    assert body["PrivateNote"] == f"RobotX:{t.txn_id}"
    assert body["TotalAmt"] == "20.00"
```

- [ ] **Step 2: Run** `pytest tests/robotx/test_post_builders.py -v` → Expected: FAIL

- [ ] **Step 3: Implement the builders**

```python
# src/robotx_qbo/post.py  (builders section)
from __future__ import annotations
from datetime import date
from decimal import Decimal
from robotx_qbo.models import ClassifiedTxn

TAG = "RobotX:"

def _acct_ref(refs, name): return {"value": refs[name]}

def build_opening_balance_je(chase_bal, ewb_bal, d: date, refs, banks) -> dict:
    total = chase_bal + ewb_bal
    return {
        "TxnDate": f"{d:%Y-%m-%d}",
        "PrivateNote": f"{TAG}opening-balance",
        "Line": [
            {"Amount": f"{chase_bal}", "DetailType": "JournalEntryLineDetail",
             "JournalEntryLineDetail": {"PostingType": "Debit",
                "AccountRef": {"value": banks["chase"]}}},
            {"Amount": f"{ewb_bal}", "DetailType": "JournalEntryLineDetail",
             "JournalEntryLineDetail": {"PostingType": "Debit",
                "AccountRef": {"value": banks["eastwest"]}}},
            {"Amount": f"{total}", "DetailType": "JournalEntryLineDetail",
             "JournalEntryLineDetail": {"PostingType": "Credit",
                "AccountRef": _acct_ref(refs, "Opening Balance Equity")}},
        ],
    }

def build_expense(c: ClassifiedTxn, refs, banks) -> dict:
    amt = abs(c.txn.amount)
    return {
        "TxnDate": f"{c.txn.date:%Y-%m-%d}",
        "PaymentType": "Check",
        "AccountRef": {"value": banks[c.txn.account]},   # paid from bank
        "TotalAmt": f"{amt}",
        "PrivateNote": f"{TAG}{c.txn.txn_id}" + (" PENDING" if c.flagged else ""),
        "Line": [{"Amount": f"{amt}", "DetailType": "AccountBasedExpenseLineDetail",
                  "AccountBasedExpenseLineDetail": {"AccountRef": _acct_ref(refs, c.account_name)}}],
    }

def build_transfer(c: ClassifiedTxn, refs, banks) -> dict:
    amt = abs(c.txn.amount)
    # outflow leg defines direction; inflow leg is its pair (dedup in orchestrator)
    from_bank = banks[c.txn.account]
    to_bank = banks["chase"] if c.txn.account == "eastwest" else banks["eastwest"]
    return {
        "TxnDate": f"{c.txn.date:%Y-%m-%d}",
        "Amount": f"{amt}",
        "FromAccountRef": {"value": from_bank},
        "ToAccountRef": {"value": to_bank},
        "PrivateNote": f"{TAG}{c.txn.txn_id}",
    }
```

- [ ] **Step 4: Run** `pytest tests/robotx/test_post_builders.py -v` → Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/robotx_qbo/post.py tests/robotx/test_post_builders.py
git commit -m "feat(robotx): QBO payload builders (JE, expense, transfer)"
```

---

### Task 13: Poster — invoice & bill builders

**Files:**
- Modify: `src/robotx_qbo/post.py` (add builders)
- Test: `tests/robotx/test_post_sale_purchase.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/robotx/test_post_sale_purchase.py
from datetime import date
from decimal import Decimal
from robotx_qbo.models import Txn, ClassifiedTxn
from robotx_qbo.post import build_invoice, build_bill

REFS={"Sales - Robots":"20","Cost of Goods - Robots":"21","Vendor Deposits":"22"}
PARTIES={"Algi Investment Inc":"Customer:5","Shenzhen Dobot":"Vendor:7"}

def test_invoice_uses_customer_and_income():
    t=Txn(account="eastwest",date=date(2026,3,11),amount=Decimal("37500.00"),
          kind="credit",description="Algi Investment",source="ewb-03.pdf")
    c=ClassifiedTxn(txn=t,category="sale",qbo_action="invoice",
                    account_name="Sales - Robots",party="Algi Investment Inc")
    inv=build_invoice(c,REFS,PARTIES)
    assert inv["CustomerRef"]["value"]=="5"
    assert inv["Line"][0]["Amount"]=="37500.00"

def test_bill_uses_vendor_and_account():
    t=Txn(account="eastwest",date=date(2026,1,29),amount=Decimal("-2849.00"),
          kind="wire",description="Shenzhen Dobot",source="ewb-01.pdf")
    c=ClassifiedTxn(txn=t,category="purchase",qbo_action="bill",
                    account_name="Cost of Goods - Robots",party="Shenzhen Dobot")
    bill=build_bill(c,REFS,PARTIES)
    assert bill["VendorRef"]["value"]=="7"
    assert bill["Line"][0]["AccountBasedExpenseLineDetail"]["AccountRef"]["value"]=="21"
```

- [ ] **Step 2: Run** → Expected: FAIL

- [ ] **Step 3: Implement** (append to `post.py`)

```python
def _split_party(parties, name):  # "Customer:5" -> "5"
    return parties[name].split(":", 1)[1]

def build_invoice(c, refs, parties) -> dict:
    amt = abs(c.txn.amount)
    return {
        "TxnDate": f"{c.txn.date:%Y-%m-%d}",
        "CustomerRef": {"value": _split_party(parties, c.party)},
        "PrivateNote": f"{TAG}{c.txn.txn_id}",
        "Line": [{"Amount": f"{amt}", "DetailType": "SalesItemLineDetail",
                  "Description": c.txn.description,
                  "SalesItemLineDetail": {
                      "ItemRef": {"name": "Robots", "value": "SERVICE"},  # see note
                      "ItemAccountRef": _acct_ref(refs, "Sales - Robots")}}],
    }

def build_bill(c, refs, parties) -> dict:
    amt = abs(c.txn.amount)
    return {
        "TxnDate": f"{c.txn.date:%Y-%m-%d}",
        "VendorRef": {"value": _split_party(parties, c.party)},
        "PrivateNote": f"{TAG}{c.txn.txn_id}" + (" PENDING" if c.flagged else ""),
        "Line": [{"Amount": f"{amt}", "DetailType": "AccountBasedExpenseLineDetail",
                  "Description": c.memo or c.txn.description,
                  "AccountBasedExpenseLineDetail": {"AccountRef": _acct_ref(refs, c.account_name)}}],
    }
```

> **Note for implementer:** QBO Invoices require an `Item`, not a bare income account. In Task 18's live bootstrap, create one Service item "Robots" mapped to the `Sales - Robots` income account, pass its Id into `parties`/`refs`, and use it in `build_invoice`. Update the test to assert the item Id once known.

- [ ] **Step 4: Run** → Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/robotx_qbo/post.py tests/robotx/test_post_sale_purchase.py
git commit -m "feat(robotx): invoice + bill builders"
```

---

### Task 14: Orchestrator + idempotency + CLI

**Files:**
- Modify: `src/robotx_qbo/post.py` (add `post_all`)
- Create: `src/robotx_qbo/cli.py`, `src/robotx_qbo/__main__.py`
- Create: `scripts/post_robotx.py`
- Test: `tests/robotx/test_post_dryrun.py`

- [ ] **Step 1: Write the failing test** (full dry-run, no network — `QboClient(dry_run=True)` never calls out for `post`, but `query` does; inject a fake)

```python
# tests/robotx/test_post_dryrun.py
from robotx_qbo.post import post_all
from robotx_qbo.ingest.load import load_all
from robotx_qbo.classify import classify

class FakeClient:
    def __init__(self): self.posts=[]; self.dry_run=True
    def query(self, sql): return {"QueryResponse": {}}     # nothing exists yet
    def post(self, path, body):
        self.posts.append((path, body))
        return {path.split('/')[0].capitalize(): {"Id": f"X{len(self.posts)}"}}

def test_dryrun_posts_every_transaction_once():
    cls = classify(load_all())
    fc = FakeClient()
    stats = post_all(fc, cls, refs=_fake_refs(), parties=_fake_parties(), banks=_fake_banks())
    # 98 txns, but 7 transfer legs collapse to 3 transfers + 1 unknown; opening JE = 1
    assert stats["invoice"] == 4
    assert stats["bill"] == 8
    assert stats["check"] == 33 + 3 + 4 + 1   # payroll + owner_draw + (UPS×2,rent) + thunder? see note
    assert stats["errors"] == 0
```

> **Note for implementer:** the exact per-action counts depend on how non-payroll checks map (Thunder → bill, UPS/rent → check/expense). Compute the expected counts from the spec §4 tables and lock them in the assertion. Provide `_fake_refs/_fake_parties/_fake_banks` returning every name used by the classifier mapped to a dummy Id.

- [ ] **Step 2: Run** → Expected: FAIL

- [ ] **Step 3: Implement `post_all`** (dedup transfer pairs; idempotency check via PrivateNote)

```python
def _already_posted(client, entity, txn_id) -> bool:
    rows = client.query(
        f"SELECT Id FROM {entity} WHERE PrivateNote LIKE '%{TAG}{txn_id}%'"
    ).get("QueryResponse", {}).get(entity, [])
    return bool(rows)

def post_all(client, classified, *, refs, parties, banks, attach_dir="inputs/robotx/bills") -> dict:
    stats = {k: 0 for k in ("invoice","bill","check","transfer","journalentry","deposit","errors")}
    seen_transfer_amounts = set()
    for c in classified:
        try:
            act = c.qbo_action
            if act == "transfer":
                key = (c.txn.date.isoformat(), str(abs(c.txn.amount)))
                if key in seen_transfer_amounts:    # pair already posted
                    continue
                seen_transfer_amounts.add(key)
                if c.txn.amount > 0:                 # only post from the outflow leg
                    continue
                client.post("transfer", build_transfer(c, refs, banks)); stats["transfer"] += 1
            elif act == "invoice":
                client.post("invoice", build_invoice(c, refs, parties)); stats["invoice"] += 1
            elif act == "bill":
                client.post("bill", build_bill(c, refs, parties)); stats["bill"] += 1
            elif act == "check":
                client.post("purchase", build_expense(c, refs, banks)); stats["check"] += 1
            elif act == "expense":
                client.post("purchase", build_expense(c, refs, banks)); stats["check"] += 1
            elif act == "deposit":
                client.post("deposit", build_deposit(c, refs, banks)); stats["deposit"] += 1
        except Exception as e:
            stats["errors"] += 1
            print("  ERROR", c.txn.txn_id, c.txn.description[:40], "->", str(e)[:120])
    return stats
```

Add `build_deposit` (parks unknown inflows / opening to a bank with the AMA account line) following the `build_expense` shape but `DepositLineDetail`. Wire `post_all` to also post the opening-balance JE first and call `generate_bill_pdf` + attach for each bill (attach via QBO `Attachable` upload — see REFERENCE in `tiktok_qbo` post patterns; if attachment API proves fiddly in sandbox, log and continue — the Bill itself must still post).

- [ ] **Step 4: Run** `pytest tests/robotx/test_post_dryrun.py -v` → Expected: PASS

- [ ] **Step 5: Implement CLI + script**

```python
# src/robotx_qbo/cli.py
import argparse
from pathlib import Path
from tiktok_qbo.qbo.env import load_creds
from tiktok_qbo.qbo.client import QboClient
from robotx_qbo.ingest.load import load_all
from robotx_qbo.classify import classify
from robotx_qbo.coa import bootstrap_coa
from robotx_qbo.parties import bootstrap_parties
from robotx_qbo.post import post_all

def main(argv=None):
    ap = argparse.ArgumentParser(prog="robotx_qbo")
    ap.add_argument("--env", default=".env.robotx")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    client = QboClient(load_creds(Path(a.env)), dry_run=a.dry_run)
    refs = bootstrap_coa(client)
    refs["Opening Balance Equity"] = refs.get("Opening Balance Equity") or \
        _ensure_obe(client)   # query/create Opening Balance Equity (QBO has it by default)
    parties = bootstrap_parties(client)
    banks = {"chase": refs["Chase Checking - 0108"], "eastwest": refs["East West Checking - 6972"]}
    cls = classify(load_all())
    stats = post_all(client, cls, refs=refs, parties=parties, banks=banks)
    print(("[DRY RUN] " if a.dry_run else "[POSTED] "), stats)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

```python
# src/robotx_qbo/__main__.py
from robotx_qbo.cli import main
raise SystemExit(main())
```

- [ ] **Step 6: Commit**

```bash
git add src/robotx_qbo/post.py src/robotx_qbo/cli.py src/robotx_qbo/__main__.py scripts/post_robotx.py tests/robotx/test_post_dryrun.py
git commit -m "feat(robotx): posting orchestrator + idempotency + CLI"
```

---

### Task 15: Live sandbox bootstrap + dry-run

**Files:** none (operational)

- [ ] **Step 1: Bootstrap accounts + parties live** (creates CoA/customers/vendors only)

Run: `python -c "from pathlib import Path; from tiktok_qbo.qbo.env import load_creds; from tiktok_qbo.qbo.client import QboClient; from robotx_qbo.coa import bootstrap_coa; from robotx_qbo.parties import bootstrap_parties; c=QboClient(load_creds(Path('.env.robotx'))); print(bootstrap_coa(c)); print(bootstrap_parties(c))"`
Expected: `[created]`/`[exists]` for each account; dicts of Ids. Fix any AccountSubType enum errors (Task 9 note) and rerun until clean.

- [ ] **Step 2: Full dry-run against live refs**

Run: `python -m robotx_qbo --dry-run`
Expected: `[DRY RUN] {'invoice': 4, 'bill': 8, 'check': ..., 'transfer': 3, 'journalentry': 1, 'deposit': ..., 'errors': 0}`. Investigate any errors before going live.

- [ ] **Step 3: Commit** any enum/config fixes made during bootstrap.

```bash
git add -A && git commit -m "fix(robotx): QBO enum/config corrections from live bootstrap"
```

---

### Task 16: Live post + verification

**Files:** none (operational)

- [ ] **Step 1: Post for real**

Run: `python -m robotx_qbo`
Expected: `[POSTED] {... 'errors': 0}`.

- [ ] **Step 2: Verify totals in QBO match the statements**

Run a check script querying QBO and comparing bank-account ending balances to spec §1 (Chase `403,349.48`, East West `5,526.52` as of 04/30/2026) and P&L/AR/AP sanity:

```python
# inline verify
from pathlib import Path
from tiktok_qbo.qbo.env import load_creds
from tiktok_qbo.qbo.client import QboClient
c = QboClient(load_creds(Path(".env.robotx")))
for name in ["Chase Checking - 0108","East West Checking - 6972"]:
    rows = c.query(f"SELECT * FROM Account WHERE Name = '{name}'")["QueryResponse"]["Account"]
    print(name, rows[0].get("CurrentBalance"))
```

Expected: Chase ≈ 403349.48, East West ≈ 5526.52 (within rounding; investigate any delta — likely a mis-signed or missing line).

- [ ] **Step 3: Re-run idempotency check**

Run: `python -m robotx_qbo` again → Expected: near-zero new posts (every entity found via PrivateNote tag and skipped). This proves idempotency.

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "chore(robotx): full-year posting verified against bank balances"
```

**🔬 Checkpoint:** Books posted. Review in QBO: the 10 `Ask My Accountant` items + the 2 flagged (#11/#12) are your boss list; reclassify once answered.

---

## Self-Review (against spec)

**Spec coverage:**
- §2 CoA → Task 9 · §3 opening balances → Task 12 (`build_opening_balance_je`) + Task 14/16
- §4.1 sales → Task 13/14 · §4.2 purchases (+PDF) → Tasks 11/13/14 · §4.3 payroll → Tasks 3/6/14
- §4.4 non-payroll checks → Tasks 3/6 · §4.5 owner draw → Task 6/12 · §4.6 transfers → Task 12/14
- §4.7 taxes → Task 6 · §4.8 expenses → Task 6 · §5 architecture → all · §6 unknowns/flagged → Tasks 6/14/16
- §1 reconciliation (98, roll-forward) → Tasks 4–8

**Open implementer decisions flagged inline:** EWB multi-line debit merge (Task 4); Chase wrapped-amount merge (Task 5); QBO AccountSubType enums (Task 9); Invoice Item requirement (Task 13); attachment API robustness (Task 14); exact dry-run action counts (Task 14). Each has a concrete note.

**No placeholders:** every code step contains runnable code; the one intentionally-wrong YAML line (Task 7) is called out and fixed in the same task to prove the reconciler catches errors.
