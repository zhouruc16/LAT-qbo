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
