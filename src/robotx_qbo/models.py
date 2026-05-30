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
