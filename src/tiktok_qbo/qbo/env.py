"""Load QBO credentials from .env file (searched in CWD, project root, parent dirs).

Supports both QBO_* and INTUIT_* prefixes for backward compatibility.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _candidate_env_paths() -> list[Path]:
    cwd = Path.cwd()
    candidates = [cwd / ".env"]
    for parent in cwd.parents[:5]:
        candidates.append(parent / ".env")
    return candidates


def load_env(explicit_path: Path | None = None) -> Path | None:
    """Load .env into os.environ. Returns the path used, or None if not found."""
    if explicit_path:
        load_dotenv(explicit_path, override=False)
        return explicit_path
    for p in _candidate_env_paths():
        if p.exists():
            load_dotenv(p, override=False)
            return p
    return None


def _get(*names: str, default: str | None = None) -> str | None:
    for n in names:
        v = os.getenv(n)
        if v:
            return v
    return default


@dataclass(frozen=True)
class QboCreds:
    client_id: str
    client_secret: str
    realm_id: str
    refresh_token: str | None
    redirect_uri: str
    environment: str  # "production" or "sandbox"
    env_path: Path | None = None  # file these creds were loaded from (for token write-back)

    @property
    def base_url(self) -> str:
        if self.environment == "sandbox":
            return "https://sandbox-quickbooks.api.intuit.com/v3/company"
        return "https://quickbooks.api.intuit.com/v3/company"

    @property
    def discovery_url(self) -> str:
        if self.environment == "sandbox":
            return "https://oauth.platform.intuit.com/op/v1/openid_connect/discovery_document"
        return "https://oauth.platform.intuit.com/op/v1/openid_connect/discovery_document"

    @property
    def auth_url(self) -> str:
        return "https://appcenter.intuit.com/connect/oauth2"

    @property
    def token_url(self) -> str:
        return "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"


def load_creds(env_path: Path | None = None) -> QboCreds:
    used = load_env(env_path)
    if used is None:
        raise RuntimeError(
            "No .env found. Place credentials in a .env file in the project root "
            "or any parent dir. See .env.example for the required keys."
        )
    cid = _get("INTUIT_CLIENT_ID", "QBO_CLIENT_ID")
    sec = _get("INTUIT_CLIENT_SECRET", "QBO_CLIENT_SECRET")
    realm = _get("INTUIT_REALM_ID", "QBO_REALM_ID", default="")
    refresh = _get("INTUIT_REFRESH_TOKEN", "QBO_REFRESH_TOKEN")
    redirect = _get("INTUIT_REDIRECT_URI", default="http://localhost:8080/callback")
    env = _get("INTUIT_ENVIRONMENT", "QBO_ENV", default="production")

    if not cid or not sec:
        raise RuntimeError(
            f"Missing INTUIT_CLIENT_ID / INTUIT_CLIENT_SECRET in {used}. "
            "Add them and re-run."
        )

    return QboCreds(
        client_id=cid, client_secret=sec, realm_id=realm or "",
        refresh_token=refresh, redirect_uri=redirect,
        environment=env.lower(), env_path=used,
    )


def write_back_token(refresh_token: str, realm_id: str, env_path: Path) -> None:
    """Update INTUIT_REFRESH_TOKEN and INTUIT_REALM_ID in the .env file."""
    text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    lines = text.splitlines()
    out: list[str] = []
    seen_refresh = seen_realm = False
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("INTUIT_REFRESH_TOKEN=") or stripped.startswith("QBO_REFRESH_TOKEN="):
            out.append(f"INTUIT_REFRESH_TOKEN={refresh_token}")
            seen_refresh = True
        elif stripped.startswith("INTUIT_REALM_ID=") or stripped.startswith("QBO_REALM_ID="):
            out.append(f"INTUIT_REALM_ID={realm_id}")
            seen_realm = True
        else:
            out.append(line)
    if not seen_refresh:
        out.append(f"INTUIT_REFRESH_TOKEN={refresh_token}")
    if not seen_realm:
        out.append(f"INTUIT_REALM_ID={realm_id}")
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
