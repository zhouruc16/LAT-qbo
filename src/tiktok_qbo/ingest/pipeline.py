import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from tiktok_qbo.ingest.lat_xlsx import (
    read_order_details, read_statements, read_payments, read_reserves,
)


@dataclass
class IngestResult:
    hash: str
    n_rows: int
    n_statements: int
    n_payments: int
    n_reserves: int


def _content_hash(path: Path) -> str:
    h = hashlib.sha256()
    h.update(Path(path).read_bytes())
    return h.hexdigest()[:16]


def _write_jsonl(path: Path, items) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(item.model_dump_json() + "\n")


def run_ingest(xlsx_path, shop_id: str, state_dir: Path) -> IngestResult:
    xlsx_path = Path(xlsx_path)
    state_dir = Path(state_dir)
    h = _content_hash(xlsx_path)

    rows = read_order_details(xlsx_path, shop_id=shop_id)
    stmts = read_statements(xlsx_path, shop_id=shop_id)
    pays = read_payments(xlsx_path, shop_id=shop_id)
    reserves = read_reserves(xlsx_path, shop_id=shop_id)

    _write_jsonl(state_dir / f"rows-{h}.jsonl", rows)
    _write_jsonl(state_dir / f"statements-{h}.jsonl", stmts)
    _write_jsonl(state_dir / f"payments-{h}.jsonl", pays)
    _write_jsonl(state_dir / f"reserves-{h}.jsonl", reserves)

    return IngestResult(
        hash=h, n_rows=len(rows), n_statements=len(stmts),
        n_payments=len(pays), n_reserves=len(reserves),
    )
