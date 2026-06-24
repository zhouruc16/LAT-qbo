"""Production OAuth connect for the real RobotX QuickBooks company.

Production requires an HTTPS redirect URI, so this serves the localhost
callback over TLS using a freshly generated self-signed cert (the browser
will warn once — click through to proceed). Captures the auth code, exchanges
it, and writes the production realm + refresh token into .env.robotx.prod.

Usage: python scripts/connect_robotx_prod.py
"""
from __future__ import annotations

import argparse
import datetime
import http.server
import secrets
import socketserver
import ssl
import sys
import tempfile
import threading
import urllib.parse
import webbrowser
from pathlib import Path

import requests
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from tiktok_qbo.qbo.auth import _exchange_code
from tiktok_qbo.qbo.env import load_creds, write_back_token

_captured: dict[str, str] = {"code": "", "realm_id": ""}
_done = threading.Event()
_expected_state = ""
_callback_path = "/callback"


def _make_self_signed_cert() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.utcnow()
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
            .sign(key, hashes.SHA256()))
    tmp = Path(tempfile.mkdtemp())
    cert_p, key_p = tmp / "cert.pem", tmp / "key.pem"
    cert_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_p.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))
    return str(cert_p), str(key_p)


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != _callback_path:
            self.send_response(404); self.end_headers(); return
        qs = urllib.parse.parse_qs(parsed.query)
        if qs.get("state", [""])[0] != _expected_state:
            self.send_response(400); self.end_headers()
            self.wfile.write(b"State mismatch - aborting."); return
        _captured["code"] = qs.get("code", [""])[0]
        _captured["realm_id"] = qs.get("realmId", [""])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers()
        self.wfile.write(b"<h2>RobotX PRODUCTION authorization received.</h2>"
                         b"<p>You can close this tab.</p>")
        _done.set()

    def log_message(self, *a, **k):
        return


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=".env.robotx.prod")
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()

    env_path = Path(args.env)
    if not env_path.exists():
        print(f"ERROR: {env_path} not found", file=sys.stderr); return 1
    creds = load_creds(env_path)
    if not creds.client_id or not creds.client_secret:
        print("ERROR: production CLIENT_ID/SECRET not filled in .env.robotx.prod",
              file=sys.stderr); return 1
    if creds.environment != "production":
        print("ERROR: INTUIT_ENVIRONMENT must be 'production' in this file",
              file=sys.stderr); return 1

    redirect_uri = creds.redirect_uri
    parsed = urllib.parse.urlparse(redirect_uri)
    if parsed.scheme != "https":
        print(f"ERROR: production redirect must be https, got {redirect_uri}",
              file=sys.stderr); return 1
    port = parsed.port or 443
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

    cert_p, key_p = _make_self_signed_cert()
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", port), _Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_p, key_p)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()

    print(f"HTTPS callback listening on {redirect_uri}")
    print("Opening browser. NOTE: your browser will warn about a self-signed")
    print("certificate on localhost — click 'Advanced' -> 'Proceed to localhost'.")
    print("Then pick your REAL RobotX company and approve.")
    print(f"If it doesn't open, visit:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    got = _done.wait(timeout=args.timeout)
    httpd.shutdown(); httpd.server_close()
    if not got or not _captured["code"]:
        print("ERROR: no authorization code captured (timed out or denied).",
              file=sys.stderr); return 2
    realm = _captured["realm_id"]
    if not realm:
        print("ERROR: no realm ID returned.", file=sys.stderr); return 2

    tok = _exchange_code(creds, _captured["code"])
    write_back_token(tok["refresh_token"], realm, env_path)
    print(f"\nAuthorized PRODUCTION. realm_id={realm}")
    print(f"Refresh token + realm written to {env_path}")

    # verify against production CompanyInfo
    url = f"{creds.base_url}/{realm}/companyinfo/{realm}"
    r = requests.get(url, headers={"Authorization": f"Bearer {tok['access_token']}",
                                   "Accept": "application/json"}, timeout=30)
    if r.ok:
        ci = r.json().get("CompanyInfo", {})
        print("\nCompanyInfo OK (this is your REAL company — verify the name):")
        print("  CompanyName :", ci.get("CompanyName"))
        print("  LegalName   :", ci.get("LegalName"))
        print("  Country     :", ci.get("Country"))
    else:
        print(f"\nCompanyInfo failed: HTTP {r.status_code}\n{r.text[:300]}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
