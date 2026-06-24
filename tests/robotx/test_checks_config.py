# tests/robotx/test_checks_config.py
import yaml
from pathlib import Path

def test_checks_config_payees():
    data = yaml.safe_load(Path("config/robotx_checks.yaml").read_text(encoding="utf-8"))
    checks = data["checks"]
    # The placeholder "0" entry was removed: numberless checks carry no payee
    # and are routed by the classifier (override or default-to-payroll).
    assert "0" not in checks
    assert len(checks) == 37
    assert all(c["payee"] for c in checks.values())
    roles = [c["role"] for c in checks.values()]
    assert roles.count("employee") == 33
    assert roles.count("unknown") == 0
