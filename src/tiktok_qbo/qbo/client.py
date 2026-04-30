"""Minimal QBO REST client with auto-refresh."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests

from tiktok_qbo.qbo.auth import refresh_access_token
from tiktok_qbo.qbo.env import QboCreds


# QBO API response wraps the created entity under a CamelCase key that doesn't
# always match the URL path.  e.g. POST /creditmemo → {"CreditMemo": {...}}.
_QBO_ENTITY_KEYS = {
    "creditmemo": "CreditMemo",
    "journalentry": "JournalEntry",
    "salesreceipt": "SalesReceipt",
}


def _qbo_entity_key(path: str) -> str:
    """Map a URL path component (e.g. 'creditmemo') to the response key
    QBO uses (e.g. 'CreditMemo').  Falls back to title-casing the first letter
    for single-word entities like 'invoice' → 'Invoice'."""
    if not path:
        return "Entity"
    return _QBO_ENTITY_KEYS.get(path, path[:1].upper() + path[1:])


@dataclass
class _AccessToken:
    token: str
    expires_at: float


class QboClient:
    def __init__(self, creds: QboCreds, dry_run: bool = False):
        self.creds = creds
        self.dry_run = dry_run
        self._access: _AccessToken | None = None
        self._dry_run_counter = 0

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
            self._dry_run_counter += 1
            entity = path.strip("/").split("/")[0]
            entity_key = _qbo_entity_key(entity)
            synthetic = {**body, "Id": f"DRY-{entity}-{self._dry_run_counter}"}
            return {entity_key: synthetic, "_dry_run": True}
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
