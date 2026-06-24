from robotx_qbo.ingest.eastwest_pdf import parse_eastwest
from robotx_qbo.ingest.chase_pdf import parse_chase
from robotx_qbo.models import Txn

EWB = ["ewb-01.pdf", "ewb-02.pdf", "ewb-03.pdf", "ewb-04.pdf"]
CHASE = ["chase-01.pdf", "chase-02.pdf", "chase-03.pdf", "chase-04.pdf"]


def load_all(input_dir: str = "inputs/robotx") -> list[Txn]:
    out: list[Txn] = []
    for f in EWB:
        out += parse_eastwest(f"{input_dir}/{f}")
    for f in CHASE:
        out += parse_chase(f"{input_dir}/{f}")
    return out


# 2025 statements (the period before the current books' 12/31/2025 opening).
EWB_2025 = [f"ewb-2025-{m:02d}.pdf" for m in (5, 6, 7, 8, 9, 10, 11, 12)]
CHASE_2025 = ["chase-2025-12.pdf"]


def load_2025(input_dir: str = "inputs/robotx_2025") -> list[Txn]:
    out: list[Txn] = []
    for f in EWB_2025:
        out += parse_eastwest(f"{input_dir}/{f}")
    for f in CHASE_2025:
        out += parse_chase(f"{input_dir}/{f}")
    return out
