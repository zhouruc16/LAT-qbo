"""Robust one-shot OAuth connect for the RobotX QBO sandbox.

Why this exists (not just `tiktok_qbo auth`):
the shared auth flow services exactly ONE HTTP request, so a stray
favicon/preflight hit consumes it before the real callback arrives and the
code is lost. This server stays up (serve_forever) and ignores non-callback
paths until it captures the authorization code, then exchanges + persists the
realm/refresh token into .env.robotx and verifies CompanyInfo.

Usage:
    python scripts/connect_robotx_sandbox.py            # uses .env.robotx
    python scripts/connect_robotx_sandbox.py --env .env.robotx
"""
from __future__ import annotations

import argparse
import http.server
import secrets
import socketserver
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path

import requests

from tiktok_qbo.qbo.auth import _exchange_code  # token exchange (reused)
from tiktok_qbo.qbo.env import load_creds, write_back_token

_captured: dict[str, str] = {"code": "", "realm_id": ""}
_done = threading.Event()
_expected_state = ""
_callback_path = "/callback"


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != _callback_path:
            # ignore favicon / stray requests, keep serving
            self.send_response(404)
            self.end_headers()
            return
        qs = urllib.parse.parse_qs(parsed.query)
        if qs.get("state", [""])[0] != _expected_state:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"State mismatch - possible CSRF; aborting.")
            return
        _captured["code"] = qs.get("code", [""])[0]
        _captured["realm_id"] = qs.get("realmId", [""])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<h2>RobotX QBO authorization received.</h2>"
            b"<p>You can close this tab.</p>"
        )
        _done.set()

    def log_message(self, *a, **k):
        return


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=".env.robotx")
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()

    env_path = Path(args.env)
    if not env_path.exists():
        print(f"ERROR: {env_path} not found", file=sys.stderr)
        return 1

    creds = load_creds(env_path)
    redirect_uri = creds.redirect_uri
    parsed = urllib.parse.urlparse(redirect_uri)
    port = parsed.port or 8080
    global _expected_state, _callback_path
    _callback_path = parsed.path or "/callback"
    _expected_state = secrets.token_urlsafe(16)

    auth_url = creds.auth_url + "?" + urllib.parse.urlencode({
        "client_id": creds.client_id,
        "scope": "com.intuit.quickbooks.accounting",
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": _expected_state,
    })

    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", port), _Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()

    print(f"Listening on {redirect_uri}")
    print("Opening browser. Pick the RobotX Inc sandbox company and approve.")
    print(f"If it doesn't open, visit:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    got = _done.wait(timeout=args.timeout)
    httpd.shutdown()
    httpd.server_close()

    if not got or not _captured["code"]:
        print("ERROR: no authorization code captured (timed out or denied).",
              file=sys.stderr)
        return 2
    realm = _captured["realm_id"]
    if not realm:
        print("ERROR: no realm ID returned.", file=sys.stderr)
        return 2

    tok = _exchange_code(creds, _captured["code"])
    write_back_token(tok["refresh_token"], realm, env_path)
    print(f"\nAuthorized. realm_id={realm}")
    print(f"Refresh token + realm written to {env_path}")

    # Verify with a CompanyInfo call
    access = tok["access_token"]
    url = f"{creds.base_url}/{realm}/companyinfo/{realm}"
    r = requests.get(url, headers={
        "Authorization": f"Bearer {access}",
        "Accept": "application/json",
    }, timeout=30)
    if r.ok:
        ci = r.json().get("CompanyInfo", {})
        print("\nCompanyInfo OK:")
        print("  CompanyName :", ci.get("CompanyName"))
        print("  LegalName   :", ci.get("LegalName"))
        print("  Country     :", ci.get("Country"))
        print("  realm match :", realm)
    else:
        print(f"\nCompanyInfo call failed: HTTP {r.status_code}\n{r.text[:300]}",
              file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
