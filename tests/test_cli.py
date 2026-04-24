import subprocess
import sys
import json
from pathlib import Path
from tiktok_qbo.ingest.pipeline import run_ingest

def _run(args, cwd):
    return subprocess.run(
        [sys.executable, "-m", "tiktok_qbo.cli", *args],
        cwd=cwd, capture_output=True, text=True,
    )

def test_cli_ingest_prints_hash(tiny_xlsx, tmp_path):
    r = _run(["ingest", str(tiny_xlsx), "--shop", "PLELNU",
              "--state-dir", str(tmp_path / "state")], cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "hash=" in r.stdout

def test_cli_plan_uses_prior_ingest(tiny_xlsx, tmp_path):
    state = tmp_path / "state"
    ingest = run_ingest(tiny_xlsx, shop_id="PLELNU", state_dir=state)
    r = _run(["plan", "--hash", ingest.hash, "--shop", "PLELNU",
              "--state-dir", str(state)], cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert (state / f"plan-{ingest.hash}" / "invoices").exists()

def test_cli_reconcile_reports_pass(tiny_xlsx, tmp_path):
    state = tmp_path / "state"
    ingest = run_ingest(tiny_xlsx, shop_id="PLELNU", state_dir=state)
    r = _run(["reconcile", "--hash", ingest.hash,
              "--state-dir", str(state)], cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "PASS" in r.stdout
