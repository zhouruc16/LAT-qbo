"""Minimal QBO REST client with auto-refresh."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests

from tiktok_qbo.qbo.auth import refresh_access_token
from tiktok_qbo.qbo.env import QboCreds


@dataclass
class _AccessToken:
    token: str
    expires_at: float


class QboClient:
    def __init__(self, creds: QboCreds, dry_run: bool = False):
        self.creds = creds
        self.dry_run = dry_run
        self._access: _AccessToken | None = None

    # ------ token lifecycle ------
    def _ensure_token(self) -> str:
        if self._access and self._access.expires_at > time.time() + 30:
            return self._access.token
        tok = refresh_access_token(self.creds)
        self._access = _AccessToken(
            token=tok["access_token"],
            expires_at=time.time() + tok.get("expires_in", 3600),
        )
        return self._access.token

    # ------ low-level HTTP ------
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._ensure_token()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        return f"{self.creds.base_url}/{self.creds.realm_id}/{path.lstrip('/')}"

    def get(self, path: str, params: dict | None = None) -> dict:
        r = requests.get(self._url(path), headers=self._headers(),
                         params=params or {}, timeout=60)
        if r.status_code >= 400:
            raise QboError(r.status_code, r.text, path)
        return r.json()

    def post(self, path: str, body: dict) -> dict:
        if self.dry_run:
            return {"_dry_run": True, "path": path, "body": body}
        r = requests.post(self._url(path), headers=self._headers(),
                          json=body, timeout=60)
        if r.status_code >= 400:
            raise QboError(r.status_code, r.text, path, body)
        return r.json()

    # ------ convenience: query ------
    def query(self, sql: str) -> dict:
        return self.get("query", {"query": sql})


class QboError(RuntimeError):
    def __init__(self, status: int, body: str, path: str, request_body: Any = None):
        super().__init__(f"QBO {status} on {path}: {body[:500]}")
        self.status = status
        self.body = body
        self.path = path
        self.request_body = request_body
