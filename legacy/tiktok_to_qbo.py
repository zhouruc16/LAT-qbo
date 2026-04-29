"""
tiktok_to_qbo.py
----------------
Convert a TikTok Shop (LAT) settlement .xlsx into QuickBooks Online Invoices.

USAGE
  # 1. Set up .env (see README / design doc)
  # 2. Dry-run (no API calls) — writes payloads to ./dry-run-output/
  python tiktok_to_qbo.py --input sample.xlsx --dry-run

  # 3. Commit to QBO sandbox
  python tiktok_to_qbo.py --input sample.xlsx --commit --env sandbox

  # 4. Commit to production
  python tiktok_to_qbo.py --input sample.xlsx --commit --env production

Deliberate scope:
  * Creates ONE QBO Invoice per "Initial sale" row (Type=Order AND Gross sales > 0).
  * Refund rows and fee-adjustment rows are written to reconcile-<statement>.csv for
    manual or follow-up handling. They do NOT become invoices here.
  * Idempotent: re-running will not create duplicates.

Author: Claude (prototype) — see design doc for rationale.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import random
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

# -------- lazy optional deps --------
def _require(pkg: str, import_name: str | None = None):
    import importlib
    try:
        return importlib.import_module(import_name or pkg)
    except ImportError as e:
        sys.exit(f"Missing dependency '{pkg}'. Install with:  pip install {pkg}\n  ({e})")

# ---------- constants ----------
MINOR_VERSION = "70"
SANDBOX_BASE = "https://sandbox-quickbooks.api.intuit.com"
PRODUCTION_BASE = "https://quickbooks.api.intuit.com"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
GENERIC_CUSTOMER_NAME = "TikTok Shop Customer"
DEFAULT_INCOME_ACCOUNT_NAME = "Sales of Product Income"
IDEMPOTENCY_NAMESPACE = uuid.UUID("6b6e9f7a-6d3d-4a0a-9a75-4a5b6c7d8e90")
LEDGER_FILE = "imported_orders.jsonl"

log = logging.getLogger("tiktok_to_qbo")


# ========== Data classes ==========
@dataclass
class OrderRow:
    """Normalized LAT row."""
    order_id: str
    sku_id: str
    sku_name: str
    product_name: str
    qty: int
    gross_sales: float
    gross_sales_refund: float
    seller_discount: float
    customer_paid_shipping: float
    sales_tax: float
    currency: str
    statement_id: str
    payment_id: str
    order_created_date: str
    order_type: str
    status: str
    raw: dict

    @property
    def classification(self) -> str:
        """One of: 'sale', 'refund', 'adjustment'.

        A true sale has positive Gross sales with no offsetting refund on the
        same row. Rows where Gross sales and Gross sales refund net to zero
        represent a cancellation/refund event for a prior invoice.
        """
        if self.order_type != "Order":
            return "adjustment"
        net_gross = self.gross_sales + self.gross_sales_refund
        if self.gross_sales_refund < 0 and abs(net_gross) < 0.01:
            return "refund"
        if self.gross_sales < 0 or self.gross_sales_refund < 0:
            return "refund"
        if net_gross > 0 and self.qty > 0:
            return "sale"
        return "adjustment"


@dataclass
class InvoiceGroup:
    """All sale-rows for one Order ID, plus aggregates."""
    order_id: str
    rows: list[OrderRow] = field(default_factory=list)

    @property
    def currency(self) -> str:
        return self.rows[0].currency

    @property
    def txn_date(self) -> str:
        raw = self.rows[0].order_created_date
        # TikTok uses YYYY/MM/DD; QBO wants YYYY-MM-DD.
        try:
            return datetime.strptime(raw, "%Y/%m/%d").date().isoformat()
        except ValueError:
            return datetime.today().date().isoformat()

    @property
    def sales_tax(self) -> float:
        # Reported as negative values representing tax collected.
        return round(sum(abs(r.sales_tax) for r in self.rows), 2)

    @property
    def shipping(self) -> float:
        return round(sum(max(r.customer_paid_shipping, 0) for r in self.rows), 2)


# ========== XLSX -> OrderRow ==========
def _num(v) -> float:
    if v in (None, "", "/"):
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _str(v) -> str:
    return "" if v is None else str(v).strip()


def load_rows(xlsx_path: Path) -> list[OrderRow]:
    openpyxl = _require("openpyxl")
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    header = [str(h) if h else "" for h in rows[0]]

    def col(name: str) -> int:
        try:
            return header.index(name)
        except ValueError:
            return -1

    idx = {k: col(k) for k in [
        "Order/adjustment ID", "SKU ID", "SKU name", "Product name",
        "Quantity", "Gross sales", "Gross sales refund", "Seller discount",
        "Customer-paid shipping fee", "Sales tax payment",
        "Currency", "Statement ID", "Payment ID",
        "Order created date", "Type", "Status",
    ]}
    missing = [k for k, v in idx.items() if v < 0]
    if missing:
        raise SystemExit(f"Input missing required columns: {missing}")

    out: list[OrderRow] = []
    for r in rows[1:]:
        if r is None or all(c in (None, "") for c in r):
            continue
        out.append(OrderRow(
            order_id=_str(r[idx["Order/adjustment ID"]]),
            sku_id=_str(r[idx["SKU ID"]]),
            sku_name=_str(r[idx["SKU name"]]),
            product_name=_str(r[idx["Product name"]]),
            qty=int(_num(r[idx["Quantity"]])),
            gross_sales=_num(r[idx["Gross sales"]]),
            gross_sales_refund=_num(r[idx["Gross sales refund"]]),
            seller_discount=_num(r[idx["Seller discount"]]),
            customer_paid_shipping=_num(r[idx["Customer-paid shipping fee"]]),
            sales_tax=_num(r[idx["Sales tax payment"]]),
            currency=_str(r[idx["Currency"]]) or "USD",
            statement_id=_str(r[idx["Statement ID"]]),
            payment_id=_str(r[idx["Payment ID"]]),
            order_created_date=_str(r[idx["Order created date"]]),
            order_type=_str(r[idx["Type"]]) or "Order",
            status=_str(r[idx["Status"]]),
            raw={header[i]: r[i] for i in range(len(header)) if header[i]},
        ))
    return out


def classify_and_group(rows: list[OrderRow]) -> tuple[list[InvoiceGroup], list[OrderRow], list[OrderRow]]:
    """Return (sale_groups, refund_rows, adjustment_rows)."""
    sales: dict[str, InvoiceGroup] = {}
    refunds: list[OrderRow] = []
    adjustments: list[OrderRow] = []
    for r in rows:
        c = r.classification
        if c == "sale":
            sales.setdefault(r.order_id, InvoiceGroup(order_id=r.order_id)).rows.append(r)
        elif c == "refund":
            refunds.append(r)
        else:
            adjustments.append(r)
    return list(sales.values()), refunds, adjustments


# ========== QBO HTTP client ==========
class QBOClient:
    """Thin wrapper around the QBO v3 REST API with OAuth refresh and retry."""

    def __init__(self, client_id: str, client_secret: str, refresh_token: str,
                 realm_id: str, env: str = "sandbox"):
        self.requests = _require("requests")
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.realm_id = realm_id
        self.base = SANDBOX_BASE if env == "sandbox" else PRODUCTION_BASE
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

    # ---- OAuth ----
    def _refresh_access_token(self) -> None:
        log.info("Refreshing OAuth access token")
        r = self.requests.post(
            TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": self.refresh_token},
            auth=(self.client_id, self.client_secret),
            headers={"Accept": "application/json"},
            timeout=30,
        )
        r.raise_for_status()
        body = r.json()
        self._access_token = body["access_token"]
        self._token_expires_at = time.time() + int(body.get("expires_in", 3600)) - 60
        if "refresh_token" in body:
            # Intuit occasionally rotates the refresh token.
            self.refresh_token = body["refresh_token"]
            log.warning("Refresh token rotated — persist the new value: %s", self.refresh_token)

    def _token(self) -> str:
        if not self._access_token or time.time() >= self._token_expires_at:
            self._refresh_access_token()
        return self._access_token  # type: ignore[return-value]

    # ---- request with retry ----
    def _request(self, method: str, path: str, *, params=None, json_body=None,
                 request_id: str | None = None) -> dict:
        url = f"{self.base}{path}"
        attempt = 0
        while True:
            attempt += 1
            headers = {
                "Authorization": f"Bearer {self._token()}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
            if request_id:
                headers["Request-Id"] = request_id
            resp = self.requests.request(method, url, headers=headers, params=params,
                                         json=json_body, timeout=60)
            if resp.status_code < 300:
                return resp.json() if resp.text else {}
            if resp.status_code == 401 and attempt == 1:
                log.info("401 received; forcing token refresh")
                self._refresh_access_token()
                continue
            if resp.status_code in (429, 500, 502, 503, 504) and attempt <= 5:
                delay = min(60, (2 ** attempt) + random.random())
                log.warning("Retryable %s from QBO (attempt %s) — sleeping %.1fs",
                            resp.status_code, attempt, delay)
                time.sleep(delay)
                continue
            raise QBOError(resp.status_code, resp.text, method, url)

    # ---- API methods ----
    def query(self, sql: str) -> list[dict]:
        res = self._request("GET", f"/v3/company/{self.realm_id}/query",
                            params={"query": sql, "minorversion": MINOR_VERSION})
        response = res.get("QueryResponse", {})
        for key in response:
            if isinstance(response[key], list):
                return response[key]
        return []

    def create(self, entity: str, payload: dict, request_id: str | None = None) -> dict:
        res = self._request("POST", f"/v3/company/{self.realm_id}/{entity.lower()}",
                            params={"minorversion": MINOR_VERSION},
                            json_body=payload, request_id=request_id)
        return res.get(entity, res)

    # ---- upserts ----
    def get_or_create_customer(self, display_name: str) -> dict:
        safe = display_name.replace("'", "\\'")
        found = self.query(f"SELECT * FROM Customer WHERE DisplayName = '{safe}'")
        if found:
            return found[0]
        return self.create("Customer", {"DisplayName": display_name})

    def get_or_create_item(self, sku: str, name: str, income_account_id: str) -> dict:
        safe = sku.replace("'", "\\'")
        found = self.query(f"SELECT * FROM Item WHERE Sku = '{safe}'")
        if found:
            return found[0]
        # QBO Item.Name must be unique and ≤ 100 chars.
        short_name = (name or f"SKU {sku}")[:95] + (f" ({sku[-4:]})" if sku else "")
        return self.create("Item", {
            "Name": short_name[:100],
            "Sku": sku,
            "Type": "Service",
            "IncomeAccountRef": {"value": income_account_id},
        })

    def get_income_account_id(self, name: str = DEFAULT_INCOME_ACCOUNT_NAME) -> str:
        safe = name.replace("'", "\\'")
        found = self.query(f"SELECT * FROM Account WHERE Name = '{safe}'")
        if not found:
            raise SystemExit(f"Income account '{name}' not found in QBO. "
                             "Create it in QBO or pass --income-account-name.")
        return found[0]["Id"]

    def find_invoice_by_doc_number(self, doc_number: str) -> dict | None:
        safe = doc_number.replace("'", "\\'")
        found = self.query(f"SELECT * FROM Invoice WHERE DocNumber = '{safe}'")
        return found[0] if found else None


class QBOError(Exception):
    def __init__(self, status, body, method, url):
        self.status = status
        self.body = body
        self.method = method
        self.url = url
        super().__init__(f"{method} {url} -> {status}\n{body[:800]}")


# ========== Invoice payload builder ==========
def idempotency_key(order_id: str) -> str:
    return str(uuid.uuid5(IDEMPOTENCY_NAMESPACE, order_id))


def doc_number_for(order_id: str) -> str:
    # DocNumber must be ≤ 21 chars and unique per company.
    tail = order_id[-12:]
    return f"TT-{tail}"


def build_invoice_payload(group: InvoiceGroup, *, customer_ref: str,
                          sku_to_item_id: dict[str, str]) -> dict:
    lines: list[dict] = []
    for r in group.rows:
        unit_price = round(r.gross_sales / r.qty, 2) if r.qty else r.gross_sales
        line = {
            "DetailType": "SalesItemLineDetail",
            "Amount": round(r.gross_sales, 2),
            "Description": f"{r.product_name} (SKU {r.sku_id})"[:1000],
            "SalesItemLineDetail": {
                "ItemRef": {"value": sku_to_item_id[r.sku_id]},
                "Qty": r.qty,
                "UnitPrice": unit_price,
            },
        }
        if r.seller_discount and abs(r.seller_discount) > 0:
            # Keep the audit trail in the description; discount handling is account-dependent.
            line["Description"] += f" — Seller discount {r.seller_discount}"
        lines.append(line)

    if group.shipping > 0:
        lines.append({
            "DetailType": "SalesItemLineDetail",
            "Amount": group.shipping,
            "Description": "Customer-paid shipping",
            "SalesItemLineDetail": {
                # Shipping handled as a Service-type line referencing a "Shipping" item.
                # The caller is expected to ensure sku_to_item_id["__SHIPPING__"] exists.
                "ItemRef": {"value": sku_to_item_id["__SHIPPING__"]},
                "Qty": 1,
                "UnitPrice": group.shipping,
            },
        })

    payload: dict[str, Any] = {
        "CustomerRef": {"value": customer_ref},
        "TxnDate": group.txn_date,
        "CurrencyRef": {"value": group.currency},
        "DocNumber": doc_number_for(group.order_id),
        "PrivateNote": f"TikTok Order {group.order_id} | Statement {group.rows[0].statement_id} | Payment {group.rows[0].payment_id}",
        "Line": lines,
        "CustomField": [
            {"DefinitionId": "1", "Name": "TikTokOrderId", "Type": "StringType", "StringValue": group.order_id[:31]},
        ],
    }
    if group.sales_tax > 0:
        payload["TxnTaxDetail"] = {"TotalTax": group.sales_tax}
    return payload


# ========== Ledger (idempotency) ==========
def already_imported(ledger_path: Path, order_id: str) -> bool:
    if not ledger_path.exists():
        return False
    with ledger_path.open() as f:
        for line in f:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("order_id") == order_id:
                return True
    return False


def append_ledger(ledger_path: Path, row: dict) -> None:
    with ledger_path.open("a") as f:
        f.write(json.dumps(row) + "\n")


# ========== Main pipeline ==========
def run(args) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    xlsx = Path(args.input)
    if not xlsx.exists():
        log.error("Input file not found: %s", xlsx); return 2

    rows = load_rows(xlsx)
    sales, refunds, adjustments = classify_and_group(rows)
    log.info("Loaded %d rows → %d sale orders, %d refund rows, %d adjustment rows",
             len(rows), len(sales), len(refunds), len(adjustments))

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Reconcile CSV for refunds + adjustments
    reconcile_path = out_dir / "reconcile.csv"
    with reconcile_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["classification", "order_id", "statement_id", "product_name",
                    "qty", "gross_sales", "gross_sales_refund", "total_settlement",
                    "customer_payment", "sales_tax", "shipping"])
        for r in refunds + adjustments:
            w.writerow([r.classification, r.order_id, r.statement_id, r.product_name,
                        r.qty, r.gross_sales, r.gross_sales_refund,
                        r.raw.get("Total settlement amount"),
                        r.raw.get("Customer payment"),
                        r.sales_tax, r.customer_paid_shipping])
    log.info("Wrote reconcile CSV: %s", reconcile_path)

    # Dry-run: just emit payloads and exit
    if args.dry_run:
        # Use placeholder IDs
        customer_ref = "<CUSTOMER_REF_PLACEHOLDER>"
        sku_to_item_id: dict[str, str] = {}
        for g in sales:
            for r in g.rows:
                sku_to_item_id.setdefault(r.sku_id, f"<ITEM_{r.sku_id}>")
        sku_to_item_id["__SHIPPING__"] = "<ITEM_SHIPPING>"

        dry_dir = out_dir / "dry-run-output"
        dry_dir.mkdir(exist_ok=True)
        for g in sales:
            payload = build_invoice_payload(g, customer_ref=customer_ref,
                                            sku_to_item_id=sku_to_item_id)
            (dry_dir / f"invoice_{g.order_id}.json").write_text(json.dumps(payload, indent=2))
        log.info("DRY-RUN complete. Wrote %d invoice payloads to %s", len(sales), dry_dir)
        return 0

    # Live
    client = QBOClient(
        client_id=os.environ["QBO_CLIENT_ID"],
        client_secret=os.environ["QBO_CLIENT_SECRET"],
        refresh_token=os.environ["QBO_REFRESH_TOKEN"],
        realm_id=os.environ["QBO_REALM_ID"],
        env=args.env,
    )

    income_account_id = client.get_income_account_id(args.income_account_name)
    customer = client.get_or_create_customer(args.customer_name)
    customer_ref = customer["Id"]

    # Ensure "Shipping" Service item exists if any group needs it
    needs_shipping = any(g.shipping > 0 for g in sales)
    sku_to_item_id: dict[str, str] = {}
    if needs_shipping:
        ship_item = client.get_or_create_item("__SHIPPING__", "Customer-paid shipping", income_account_id)
        sku_to_item_id["__SHIPPING__"] = ship_item["Id"]

    # Pre-upsert all items the run will need
    unique_skus = {(r.sku_id, r.product_name) for g in sales for r in g.rows}
    for sku, name in unique_skus:
        item = client.get_or_create_item(sku, name, income_account_id)
        sku_to_item_id[sku] = item["Id"]
    log.info("Upserted %d items (%s shipping)", len(sku_to_item_id),
             "incl." if needs_shipping else "excl.")

    ledger_path = out_dir / LEDGER_FILE
    imported = skipped = failed = 0
    failures_path = out_dir / "failures.jsonl"

    for g in sales:
        if already_imported(ledger_path, g.order_id):
            log.info("SKIP %s — already in ledger", g.order_id); skipped += 1; continue

        existing = client.find_invoice_by_doc_number(doc_number_for(g.order_id))
        if existing:
            log.info("SKIP %s — invoice DocNumber exists in QBO (Id %s)",
                     g.order_id, existing.get("Id"))
            append_ledger(ledger_path, {"order_id": g.order_id,
                                        "invoice_id": existing.get("Id"),
                                        "status": "already_present",
                                        "ts": datetime.utcnow().isoformat()})
            skipped += 1
            continue

        payload = build_invoice_payload(g, customer_ref=customer_ref,
                                        sku_to_item_id=sku_to_item_id)
        try:
            inv = client.create("Invoice", payload, request_id=idempotency_key(g.order_id))
            log.info("OK %s → Invoice Id %s", g.order_id, inv.get("Id"))
            append_ledger(ledger_path, {"order_id": g.order_id,
                                        "invoice_id": inv.get("Id"),
                                        "doc_number": inv.get("DocNumber"),
                                        "status": "created",
                                        "ts": datetime.utcnow().isoformat()})
            imported += 1
        except QBOError as e:
            log.error("FAIL %s — %s", g.order_id, e)
            with failures_path.open("a") as f:
                f.write(json.dumps({"order_id": g.order_id,
                                    "status": e.status,
                                    "body": e.body,
                                    "payload": payload}) + "\n")
            failed += 1
        # Gentle pacing to stay well under 500/min limit
        time.sleep(0.15)

    log.info("==== DONE: imported=%d skipped=%d failed=%d ====", imported, skipped, failed)
    return 0 if failed == 0 else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Path to the TikTok LAT .xlsx")
    ap.add_argument("--output-dir", default="./qbo-out", help="Where logs + dry-run JSON go")
    ap.add_argument("--dry-run", action="store_true",
                    help="Do not call QBO; emit payloads to ./qbo-out/dry-run-output/")
    ap.add_argument("--commit", action="store_true", help="(inverse of --dry-run, required for live)")
    ap.add_argument("--env", choices=["sandbox", "production"], default="sandbox")
    ap.add_argument("--customer-name", default=GENERIC_CUSTOMER_NAME)
    ap.add_argument("--income-account-name", default=DEFAULT_INCOME_ACCOUNT_NAME)
    args = ap.parse_args()
    if args.commit and args.dry_run:
        sys.exit("--commit and --dry-run are mutually exclusive")
    if not args.commit and not args.dry_run:
        sys.exit("Pass --dry-run or --commit explicitly.")
    try:
        # load .env if present (optional)
        try:
            import dotenv; dotenv.load_dotenv()
        except ImportError:
            pass
        sys.exit(run(args))
    except KeyError as e:
        sys.exit(f"Missing required env var {e}. See the design doc §4.1.")


if __name__ == "__main__":
    main()
