import json
from tiktok_qbo.ingest.pipeline import run_ingest

def test_run_ingest_writes_four_jsonl_artifacts(tiny_xlsx, tmp_path):
    state_dir = tmp_path / "state"
    result = run_ingest(tiny_xlsx, shop_id="PLELNU", state_dir=state_dir)
    h = result.hash
    assert (state_dir / f"rows-{h}.jsonl").exists()
    assert (state_dir / f"statements-{h}.jsonl").exists()
    assert (state_dir / f"payments-{h}.jsonl").exists()
    assert (state_dir / f"reserves-{h}.jsonl").exists()

    with (state_dir / f"rows-{h}.jsonl").open() as f:
        rows = [json.loads(line) for line in f]
    assert len(rows) == 4
    assert rows[0]["classification"] == "sale"

def test_run_ingest_hash_is_stable(tiny_xlsx, tmp_path):
    a = run_ingest(tiny_xlsx, shop_id="PLELNU", state_dir=tmp_path / "a")
    b = run_ingest(tiny_xlsx, shop_id="PLELNU", state_dir=tmp_path / "b")
    assert a.hash == b.hash
