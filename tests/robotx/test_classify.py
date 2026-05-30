from collections import Counter
from decimal import Decimal
from robotx_qbo.ingest.load import load_all
from robotx_qbo.classify import classify


def _cls():
    return classify(load_all())


def test_total_and_category_counts():
    cls = _cls()
    assert len(cls) == 98
    by = Counter(c.category for c in cls)
    assert by["sale"] == 4
    assert by["purchase"] == 8
    assert by["payroll"] == 33
    assert by["owner_draw"] == 3
    assert by["transfer"] == 7
    assert by["tax"] == 11
    assert by["expense"] == 22
    assert by["unknown"] == 10


def test_ask_my_accountant_and_flagged():
    cls = _cls()
    assert sum(1 for c in cls if c.account_name == "Ask My Accountant") == 10
    flagged = [c for c in cls if c.flagged]
    assert {int(abs(c.txn.amount)) for c in flagged} == {70000, 465450}
    assert all(c.category == "purchase" for c in flagged)


def test_specific_edge_cases():
    cls = _cls()
    nat = [c for c in cls if "New American Title" in c.txn.description][0]
    assert nat.category == "unknown" and nat.account_name == "Ask My Accountant"
    t15 = [c for c in cls if "7880" in c.txn.description][0]
    assert t15.category == "transfer"
    c0 = [c for c in cls if c.txn.check_no == "0"][0]
    assert c0.category == "unknown"
    # Intbot purchase memo contains "RobotX buy ..." but must NOT be a transfer
    intbot = [c for c in cls if "Intbot" in c.txn.description][0]
    assert intbot.category == "purchase"
