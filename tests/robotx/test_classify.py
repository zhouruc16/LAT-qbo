from collections import Counter
from robotx_qbo.ingest.load import load_all
from robotx_qbo.classify import classify


def _cls():
    return classify(load_all())


def test_total_and_category_counts():
    cls = _cls()
    assert len(cls) == 98
    by = Counter(c.category for c in cls)
    # Corrected with the owner's ("boss") notes: New American Title is a robot
    # purchase, the $95k/$497,550 deposits are Homecoin sales, …7880 is rent,
    # the $9k check + cash withdrawals are Sam service fees, POS Shanghai is
    # travel. Only COSCO ($2,500) and the Pho Ha Noi mobile check ($4,294.50)
    # remain unidentified.
    assert by["sale"] == 6          # 4 Algi + 2 Homecoin deposits
    assert by["purchase"] == 9      # 8 robot wires + New American Title
    assert by["payroll"] == 33
    assert by["owner_draw"] == 3
    assert by["transfer"] == 6      # EWB↔Chase legs (…7880 is now Rent)
    assert by["tax"] == 11
    assert by["expense"] == 28
    assert by["unknown"] == 2


def test_ask_my_accountant_and_flagged():
    cls = _cls()
    ama = [c for c in cls if c.account_name == "Ask My Accountant"]
    assert len(ama) == 2
    flagged = [c for c in cls if c.flagged]
    # YuShu performance bond, OpenLive settlement fee, New American Title.
    assert {int(abs(c.txn.amount)) for c in flagged} == {70000, 465450, 497000}


def test_boss_corrections():
    cls = _cls()
    nat = [c for c in cls if "New American Title" in c.txn.description][0]
    assert nat.category == "purchase" and nat.account_name == "Cost of Goods - Robots"
    assert nat.flagged
    t7880 = [c for c in cls if "7880" in c.txn.description][0]
    assert t7880.account_name == "Rent"
    shanghai = [c for c in cls if "SHANGHAILONGQIAO" in c.txn.description.upper()]
    assert shanghai and all(c.account_name == "Travel" for c in shanghai)
    # Intbot purchase memo contains "RobotX buy ..." but must NOT be a transfer
    intbot = [c for c in cls if "Intbot" in c.txn.description][0]
    assert intbot.category == "purchase"
