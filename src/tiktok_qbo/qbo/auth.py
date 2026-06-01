"""QBO OAuth 2.0 authorization-code flow (one-shot, localhost callback)."""
from __future__ import annotations

import base64
import http.server
import secrets
import socketserver
import threading
import urllib.parse
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from tiktok_qbo.qbo.env import QboCreds, _candidate_env_paths, load_creds, write_back_token


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str
    expires_in: int
    realm_id: str


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    captured: dict[str, Any] = {}
    expected_state: str = ""

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != urllib.parse.urlparse(_CallbackHandler.captured["redirect_uri"]).path:
            self.send_response(404)
            self.end_headers()
            return
        qs = urllib.parse.parse_qs(parsed.query)
        if qs.get("state", [""])[0] != _CallbackHandler.expected_state:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"State mismatch - possible CSRF; aborting.")
            return
        code = qs.get("code", [""])[0]
        realm = qs.get("realmId", [""])[0]
        _CallbackHandler.captured["code"] = code
        _CallbackHandler.captured["realm_id"] = realm
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<h2>QBO authorization received.</h2>"
            b"<p>You can close this tab; the pipeline will continue.</p>"
        )

    def log_message(self, *args, **kwargs):  # silence default access log
        return


def _basic_auth(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def _exchange_code(creds: QboCreds, code: str) -> dict:
    resp = requests.post(
        creds.token_url,
        headers={
            "Authorization": _basic_auth(creds.client_id, creds.client_secret),
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": creds.redirect_uri,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def refresh_access_token(creds: QboCreds) -> dict:
    """Use the refresh token to get a fresh access token.

    QBO rotates refresh tokens on use — the response's refresh_token may
    differ from the one we sent. Persist the returned refresh_token back
    to .env so subsequent calls don't 400 on a stale token.
    """
    if not creds.refresh_token:
        raise RuntimeError("No refresh token; run `tiktok_qbo auth` first.")
    resp = requests.post(
        creds.token_url,
        headers={
            "Authorization": _basic_auth(creds.client_id, creds.client_secret),
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "refresh_token",
            "refresh_token": creds.refresh_token,
        },
        timeout=30,
    )
    resp.raise_for_status()
    tok = resp.json()
    new_refresh = tok.get("refresh_token")
    if new_refresh and new_refresh != creds.refresh_token:
        # Write the rotated token back to the SAME file these creds came from,
        # so a multi-company setup (.env + .env.robotx) never clobbers the wrong
        # file. Fall back to discovery only if env_path is unknown.
        if creds.env_path and Path(creds.env_path).exists():
            write_back_token(new_refresh, creds.realm_id, Path(creds.env_path))
        else:
            for p in _candidate_env_paths():
                if p.exists():
                    write_back_token(new_refresh, creds.realm_id, p)
                    break
    return tok


def run_auth_flow(env_path: Path) -> TokenSet:
    """Open browser, capture code at localhost, exchange for tokens, persist."""
    creds = load_creds(env_path)
    redirect_uri = creds.redirect_uri
    parsed = urllib.parse.urlparse(redirect_uri)
    port = parsed.port or 8080

    state = secrets.token_urlsafe(16)
    auth_params = {
        "client_id": creds.client_id,
        "scope": "com.intuit.quickbooks.accounting",
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
    }
    auth_url = creds.auth_url + "?" + urllib.parse.urlencode(auth_params)

    _CallbackHandler.captured = {"redirect_uri": redirect_uri, "code": "", "realm_id": ""}
    _CallbackHandler.expected_state = state

    httpd = socketserver.TCPServer(("127.0.0.1", port), _CallbackHandler)
    server_thread = threading.Thread(target=httpd.handle_request, daemon=True)
    server_thread.start()

    print(f"Opening browser to authorize QBO access...")
    print(f"If the browser doesn't open, visit:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    server_thread.join(timeout=300)  # 5-minute window
    httpd.server_close()

    code = _CallbackHandler.captured.get("code")
    realm = _CallbackHandler.captured.get("realm_id")
    if not code:
        raise RuntimeError("OAuth flow did not return an authorization code.")
    if not realm:
        raise RuntimeError("OAuth flow did not return a realm ID.")

    tok = _exchange_code(creds, code)
    write_back_token(tok["refresh_token"], realm, env_path)
    print(f"Refresh token + realm_id written to {env_path}")

    return TokenSet(
        access_token=tok["access_token"],
        refresh_token=tok["refresh_token"],
        expires_in=tok.get("expires_in", 3600),
        realm_id=realm,
    )
