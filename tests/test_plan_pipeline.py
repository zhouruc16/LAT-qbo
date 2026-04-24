import json
from tiktok_qbo.ingest.pipeline import run_ingest
from tiktok_qbo.plan.pipeline import run_plan

def test_run_plan_writes_three_entity_dirs(tiny_xlsx, tmp_path):
    state_dir = tmp_path / "state"
    ingest = run_ingest(tiny_xlsx, shop_id="PLELNU", state_dir=state_dir)
    plan_dir = run_plan(hash=ingest.hash, shop_id="PLELNU", state_dir=state_dir)

    inv_files  = list((plan_dir / "invoices").glob("*.json"))
    cm_files   = list((plan_dir / "credit_memos").glob("*.json"))
    je_files   = list((plan_dir / "journal_entries").glob("*.json"))

    assert len(inv_files) == 1          # one sale on 2024-04-30
    assert len(cm_files) == 1           # refund + chargeback both on 2024-05-12
    assert len(je_files) == 1           # one payment P1

    inv = json.loads(inv_files[0].read_text(encoding="utf-8"))
    assert inv["doc_number"] == "INV-PLELNU-20240430"
    assert inv["total_amount"] == "15.99"
