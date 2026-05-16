"""Bootstrap QBO Non-Inventory Items for every SKU.

Drop-shipping model: LAT never holds inventory. Each SKU becomes a QBO
Non-Inventory Item — has a product name + SKU code + income account, but
no quantity-on-hand tracking. Ending inventory is structurally always 0.

A sentinel "TikTok Platform Adjustment" item handles invoice/CM lines that
legitimately contribute to Net sales but lack a real SKU (TikTok-side
adjustments and platform fee corrections that flow through Net sales).

Idempotent: queries by `Sku` (and Name for the sentinel) before creating.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from openpyxl import load_workbook

from tiktok_qbo.models import NormalizedRow
from tiktok_qbo.qbo.client import QboClient


# QBO Item.Name has a 100-char hard limit.  Sku is 100-char too.
_NAME_MAX = 100
_SENTINEL_NAME = "TikTok Platform Adjustment"


@dataclass(frozen=True)
class ItemRefs:
    """Maps every SKU we'll post → its QBO Item Id, plus the sentinel."""
    sku_to_item_id: dict[str, str] = field(default_factory=dict)
    platform_adjustment_id: str = ""


def _truncate(name: str, limit: int = _NAME_MAX) -> str:
    name = name.strip()
    return name if len(name) <= limit else name[: limit - 1].rstrip() + "…"


def load_master_table(path: Path) -> dict[str, str]:
    """Read Master Table xlsx → {sku_id (string of digits) -> product_name}."""
    wb = load_workbook(path, data_only=True)
    ws = wb.active
    out: dict[str, str] = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[0]:
            continue
        sku = str(row[0]).strip()
        if not sku.isdigit():
            continue
        name = str(row[1]).strip() if row[1] else ""
        if name:
            out[sku] = name
    wb.close()
    return out


def build_sku_name_map(
    master: dict[str, str],
    rows: list[NormalizedRow],
) -> dict[str, str]:
    """Return {sku -> best product name} for every SKU that appears in `rows`.

    Preference order:
      1. Master Table name (authoritative).
      2. Most-voted product name from the rows themselves (fallback for
         SKUs not yet added to the Master Table — E2 in the design).
      3. "SKU <id>" sentinel as last resort.
    """
    name_votes: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        if not r.sku_id:
            continue
        sku = r.sku_id.strip()
        if not sku.isdigit():
            continue
        if r.product_name:
            name_votes[sku][r.product_name.strip()] += int(r.quantity or 1)

    result: dict[str, str] = {}
    for sku in name_votes:
        if sku in master:
            result[sku] = master[sku]
        elif name_votes[sku]:
            result[sku] = name_votes[sku].most_common(1)[0][0]
        else:
            result[sku] = f"SKU {sku}"
    return result


def _find_item_by_sku(client: QboClient, sku: str) -> dict | None:
    safe = sku.replace("'", "\\'")
    res = client.query(f"SELECT * FROM Item WHERE Sku = '{safe}'")
    rows = res.get("QueryResponse", {}).get("Item", [])
    return rows[0] if rows else None


def _find_item_by_name(client: QboClient, name: str) -> dict | None:
    safe = name.replace("'", "\\'")
    res = client.query(f"SELECT * FROM Item WHERE Name = '{safe}'")
    rows = res.get("QueryResponse", {}).get("Item", [])
    return rows[0] if rows else None


def _disambiguate_name(name: str, sku: str, used: set[str]) -> str:
    """Ensure Name is unique within this bootstrap run.

    QBO Item.Name must be globally unique. If two SKUs map to the same name
    (or the name is already taken), suffix with " (last8 of SKU)" to break
    the tie while still being human-readable in the QBO UI.
    """
    base = _truncate(name)
    if base not in used:
        return base
    suffix = f" ({sku[-8:]})" if len(sku) >= 8 else f" ({sku})"
    suffixed = _truncate(name, _NAME_MAX - len(suffix)) + suffix
    return suffixed


def _create_non_inventory_item(
    client: QboClient,
    name: str,
    sku: str,
    income_account_id: str,
) -> dict:
    body = {
        "Name": name,
        "Sku": sku,
        "Type": "NonInventory",
        "IncomeAccountRef": {"value": income_account_id},
        "Taxable": False,
    }
    res = client.post("item", body)
    return res.get("Item", {})


def bootstrap_items(
    client: QboClient,
    sku_to_name: dict[str, str],
    income_account_id: str,
    *,
    verbose: bool = True,
) -> ItemRefs:
    """Idempotently create one Non-Inventory Item per SKU, plus a sentinel.

    Returns ItemRefs with {sku -> item_id} for every SKU in `sku_to_name`
    that resolved (existing or newly created), plus the sentinel item id.

    Lookup strategy per SKU:
      1. Query by Sku — if found, reuse its Id.
      2. Otherwise POST a new Item, disambiguating Name on collision.
    """
    sku_to_id: dict[str, str] = {}
    used_names: set[str] = set()
    created = 0
    existed = 0

    for sku in sorted(sku_to_name.keys()):
        existing = _find_item_by_sku(client, sku)
        if existing:
            sku_to_id[sku] = existing["Id"]
            used_names.add(existing.get("Name", ""))
            existed += 1
            continue
        raw_name = sku_to_name[sku]
        name = _disambiguate_name(raw_name, sku, used_names)
        if name in used_names or _find_item_by_name(client, name) is not None:
            # Name collision with something not yet in our local `used_names`
            # tracking — suffix unconditionally.
            suffix = f" ({sku[-8:]})" if len(sku) >= 8 else f" ({sku})"
            name = _truncate(raw_name, _NAME_MAX - len(suffix)) + suffix
        created_item = _create_non_inventory_item(client, name, sku, income_account_id)
        sku_to_id[sku] = created_item.get("Id", "")
        used_names.add(name)
        created += 1

    # Sentinel item for no-SKU revenue lines (E1).
    sentinel_existing = _find_item_by_name(client, _SENTINEL_NAME)
    if sentinel_existing:
        sentinel_id = sentinel_existing["Id"]
        if verbose:
            print(f"  [exists]  sentinel '{_SENTINEL_NAME}' (Id={sentinel_id})")
    else:
        sentinel_body = {
            "Name": _SENTINEL_NAME,
            "Type": "NonInventory",
            "IncomeAccountRef": {"value": income_account_id},
            "Taxable": False,
        }
        res = client.post("item", sentinel_body)
        sentinel_id = res.get("Item", {}).get("Id", "")
        if verbose:
            print(f"  [created] sentinel '{_SENTINEL_NAME}' (Id={sentinel_id})")

    if verbose:
        print(f"  items: {created} created, {existed} existed, "
              f"{len(sku_to_id)} total SKUs mapped")

    return ItemRefs(sku_to_item_id=sku_to_id, platform_adjustment_id=sentinel_id)
