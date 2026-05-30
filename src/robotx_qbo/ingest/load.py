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
