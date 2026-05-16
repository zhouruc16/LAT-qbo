"""Unit tests for qbo.items — Non-Inventory item bootstrap."""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from tiktok_qbo.models import NormalizedRow
from tiktok_qbo.qbo.items import (
    ItemRefs,
    bootstrap_items,
    build_sku_name_map,
)

SALES_ID = "300"


def _row(sku: str, name: str, qty: int = 1) -> NormalizedRow:
    return NormalizedRow(
        shop_id="PLELNU", order_id=f"O-{sku}", sku_id=sku,
        statement_id="S1", payment_id="P1",
        statement_date=date(2024, 5, 12),
        order_created_date=date(2024, 4, 28),
        order_shipment_date=date(2024, 4, 29),
        order_delivery_date=date(2024, 4, 30),
        row_type="Order", classification="sale",
        customer_payment=Decimal("0"), customer_refund=Decimal("0"),
        gross_sales=Decimal("0"), quantity=qty, product_name=name,
        raw={"Net sales": "10"},
    )


class FakeClient:
    """Records every post/query; answers query() from a preloaded existing map.

    `existing` is keyed by ("Item", "<sku-or-name>") so we can simulate either
    a Sku lookup or a Name lookup returning a pre-existing item.
    """

    def __init__(self, existing: dict | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.existing_by_sku: dict[str, dict] = {}
        self.existing_by_name: dict[str, dict] = {}
        for k, v in (existing or {}).items():
            kind, val = k
            if kind == "sku":
                self.existing_by_sku[val] = v
            elif kind == "name":
                self.existing_by_name[val] = v
        self._counter = 0
        self._created_names: dict[str, dict] = {}
        self._created_skus: dict[str, dict] = {}

    def query(self, sql: str) -> dict:
        m = re.search(r"FROM\s+Item\s+WHERE\s+Sku\s*=\s*'([^']*)'", sql)
        if m:
            sku = m.group(1)
            row = self.existing_by_sku.get(sku) or self._created_skus.get(sku)
            if row is not None:
                return {"QueryResponse": {"Item": [row]}}
            return {"QueryResponse": {}}
        m = re.search(r"FROM\s+Item\s+WHERE\s+Name\s*=\s*'([^']*)'", sql)
        if m:
            name = m.group(1)
            row = self.existing_by_name.get(name) or self._created_names.get(name)
            if row is not None:
                return {"QueryResponse": {"Item": [row]}}
            return {"QueryResponse": {}}
        return {"QueryResponse": {}}

    def post(self, path: str, body: dict) -> dict:
        self.calls.append((path, body))
        self._counter += 1
        new = {**body, "Id": f"I-{self._counter}"}
        # Make subsequently created items visible to later query() calls so
        # the in-run uniqueness check behaves like a real QBO instance.
        if "Sku" in body:
            self._created_skus[body["Sku"]] = new
        if "Name" in body:
            self._created_names[body["Name"]] = new
        return {"Item": new}

    def items_created(self) -> list[dict]:
        return [b for p, b in self.calls if p == "item"]


def test_build_sku_name_map_prefers_master_table():
    master = {"100": "MASTER NAME"}
    rows = [_row("100", "row-name", qty=5)]
    out = build_sku_name_map(master, rows)
    assert out == {"100": "MASTER NAME"}


def test_build_sku_name_map_falls_back_to_row_vote():
    master = {}  # SKU not in master
    rows = [
        _row("100", "Name A", qty=1),
        _row("100", "Name B", qty=5),  # higher vote
        _row("100", "Name A", qty=2),
    ]
    out = build_sku_name_map(master, rows)
    assert out == {"100": "Name B"}


def test_build_sku_name_map_skips_non_numeric_sku():
    master = {}
    rows = [_row("/", "garbage", qty=1), _row("100", "real", qty=1)]
    out = build_sku_name_map(master, rows)
    assert "/" not in out
    assert out == {"100": "real"}


def test_bootstrap_items_creates_for_new_skus_and_sentinel():
    client = FakeClient()
    refs = bootstrap_items(client, {"100": "Item A"}, SALES_ID, verbose=False)
    items = client.items_created()
    assert len(items) == 2  # one SKU item + sentinel
    skus_in_items = {i.get("Sku") for i in items if i.get("Sku")}
    assert skus_in_items == {"100"}
    sentinels = [i for i in items if i["Name"] == "TikTok Platform Adjustment"]
    assert len(sentinels) == 1
    assert refs.sku_to_item_id["100"]
    assert refs.platform_adjustment_id


def test_bootstrap_items_reuses_existing_by_sku():
    client = FakeClient(existing={("sku", "100"): {"Id": "EXISTING-1", "Name": "Whatever"}})
    refs = bootstrap_items(client, {"100": "Item A"}, SALES_ID, verbose=False)
    items_posted = [b for p, b in client.calls if p == "item"]
    # Only the sentinel should be POSTed; the SKU item already existed.
    assert len(items_posted) == 1
    assert items_posted[0]["Name"] == "TikTok Platform Adjustment"
    assert refs.sku_to_item_id["100"] == "EXISTING-1"


def test_bootstrap_items_reuses_existing_sentinel():
    client = FakeClient(existing={
        ("name", "TikTok Platform Adjustment"): {"Id": "SENT-9", "Name": "TikTok Platform Adjustment"},
    })
    refs = bootstrap_items(client, {}, SALES_ID, verbose=False)
    assert client.items_created() == []
    assert refs.platform_adjustment_id == "SENT-9"


def test_bootstrap_items_disambiguates_duplicate_names():
    """Two different SKUs with the same product name — second one gets a suffix."""
    client = FakeClient()
    refs = bootstrap_items(
        client,
        {"1000000000000000001": "Versace Eros 5ml",
         "1000000000000000002": "Versace Eros 5ml"},
        SALES_ID, verbose=False,
    )
    items = [b for b in client.items_created() if b.get("Sku")]
    names = sorted(b["Name"] for b in items)
    assert names[0] == "Versace Eros 5ml"
    assert names[1].startswith("Versace Eros 5ml")
    assert names[0] != names[1], "duplicate name was not disambiguated"
    assert refs.sku_to_item_id["1000000000000000001"]
    assert refs.sku_to_item_id["1000000000000000002"]
    assert refs.sku_to_item_id["1000000000000000001"] != refs.sku_to_item_id["1000000000000000002"]


def test_bootstrap_items_truncates_long_names():
    long_name = "X" * 250
    client = FakeClient()
    bootstrap_items(client, {"100": long_name}, SALES_ID, verbose=False)
    posted = [b for b in client.items_created() if b.get("Sku") == "100"][0]
    assert len(posted["Name"]) <= 100


def test_bootstrap_items_sets_non_inventory_type_and_income_account():
    client = FakeClient()
    bootstrap_items(client, {"100": "Item A"}, SALES_ID, verbose=False)
    items = client.items_created()
    sku_item = next(i for i in items if i.get("Sku") == "100")
    assert sku_item["Type"] == "NonInventory"
    assert sku_item["IncomeAccountRef"]["value"] == SALES_ID
    sentinel = next(i for i in items if i["Name"] == "TikTok Platform Adjustment")
    assert sentinel["Type"] == "NonInventory"
    assert sentinel["IncomeAccountRef"]["value"] == SALES_ID
