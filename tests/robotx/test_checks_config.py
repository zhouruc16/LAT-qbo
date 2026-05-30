# tests/robotx/test_checks_config.py
import yaml
from pathlib import Path

def test_38_checks_with_payees():
    data = yaml.safe_load(Path("config/robotx_checks.yaml").read_text(encoding="utf-8"))
    checks = data["checks"]
    assert len(checks) == 38
    assert all(c["payee"] for c in checks.values())
    roles = [c["role"] for c in checks.values()]
    assert roles.count("employee") == 33
    assert roles.count("unknown") == 1          # the $9,000 self-check
