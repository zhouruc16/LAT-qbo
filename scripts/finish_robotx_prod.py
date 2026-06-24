"""Finish the production OAuth connect from the authorized redirect.

After authorizing the REAL RobotX company (redirect lands on Intuit's
OAuth Playground page), pass the full redirected URL here. We parse the
short-lived authorization code + realmId, exchange for tokens using the
production keys, write the refresh token + realm into .env.robotx.prod,
and verify the company name.

Usage:
  python scripts/finish_robotx_prod.py --url "https://developer.intuit.com/v2/OAuth2Playground/RedirectUrl?code=...&realmId=...&state=..."
  python scripts/finish_robotx_prod.py --code XXX --realm YYY
"""
from __future__ import annotations
import argparse
import sys
import urllib.parse
from pathlib import Path

import requests
from tiktok_qbo.qbo.auth import _exchange_code
from tiktok_qbo.qbo.env import load_creds, write_back_token


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=None, help="full redirected URL")
    ap.add_argument("--code", default=None)
    ap.add_argument("--realm", default=None)
    ap.add_argument("--env", default=".env.robotx.prod")
    a = ap.parse_args()

    code, realm = a.code, a.realm
    if a.url:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(a.url).query)
        code = qs.get("code", [None])[0]
        realm = qs.get("realmId", [None])[0]
    if not code or not realm:
        print("ERROR: need code + realmId (via --url or --code/--realm)", file=sys.stderr)
        return 1

    env_path = Path(a.env)
    creds = load_creds(env_path)
    if not creds.client_id or not creds.client_secret:
        print("ERROR: production keys not filled in .env.robotx.prod", file=sys.stderr)
        return 1

    tok = _exchange_code(creds, code)        # uses creds.redirect_uri (Playground URL)
    write_back_token(tok["refresh_token"], realm, env_path)
    print(f"Authorized PRODUCTION. realm_id={realm}")
    print(f"Refresh token + realm written to {env_path}")

    url = f"{creds.base_url}/{realm}/companyinfo/{realm}"
    r = requests.get(url, headers={"Authorization": f"Bearer {tok['access_token']}",
                                   "Accept": "application/json"}, timeout=30)
    if r.ok:
        ci = r.json().get("CompanyInfo", {})
        print("\nCompanyInfo OK — confirm this is your REAL company:")
        print("  CompanyName :", ci.get("CompanyName"))
        print("  LegalName   :", ci.get("LegalName"))
        print("  Country     :", ci.get("Country"))
    else:
        print(f"\nCompanyInfo failed: HTTP {r.status_code}\n{r.text[:300]}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
