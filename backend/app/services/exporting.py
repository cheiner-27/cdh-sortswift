"""Inventory / scan-session export to CSV or XLSX (Section 3), on-demand only."""
import csv
import io

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import CONDITION_LABELS
from ..models import InventoryItem
from . import inventory as inv_svc

# column key -> (header, getter)
NATIVE_COLUMNS = {
    "game": ("Game", lambda db, it: it.card.game if it.card else "custom"),
    "name": ("Name", lambda db, it: it.card.name if it.card else
             (it.custom_sku.product.name if it.custom_sku else "")),
    "set_code": ("Set Code", lambda db, it: it.card.set_code if it.card else ""),
    "set_name": ("Set Name", lambda db, it: it.card.set_name if it.card else ""),
    "collector_number": ("Collector #", lambda db, it: it.card.collector_number if it.card else ""),
    "rarity": ("Rarity", lambda db, it: it.card.rarity if it.card else ""),
    "condition": ("Condition", lambda db, it: it.condition),
    "printing": ("Printing", lambda db, it: it.printing),
    "language": ("Language", lambda db, it: it.language),
    "bin": ("Bin", lambda db, it: it.bin),
    "quantity": ("Quantity", lambda db, it: it.quantity),
    "price": ("Price", lambda db, it: it.price_override or it.current_price or ""),
    "cost": ("FIFO Cost", lambda db, it: inv_svc.fifo_unit_cost(db, it) or ""),
    "comment": ("Comment", lambda db, it: it.comment),
    "age_days": ("Days In Inventory", lambda db, it: inv_svc.inventory_age_days(db, it) or ""),
    "external_id": ("Catalog ID", lambda db, it: it.card.external_id if it.card else ""),
    "tcgplayer_product_id": ("TCGplayer Product Id",
                             lambda db, it: it.card.tcgplayer_product_id if it.card else ""),
    "sku": ("Internal SKU", lambda db, it: internal_sku(it)),
}

DEFAULT_COLUMNS = ["game", "name", "set_code", "collector_number", "condition",
                   "printing", "language", "bin", "quantity", "price", "cost", "comment"]


def internal_sku(item: InventoryItem) -> str:
    """Stable internal SKU: identity + condition + printing + bin (Section 6)."""
    base = f"C{item.catalog_card_id}" if item.catalog_card_id else f"X{item.custom_sku_id}"
    return f"{base}-{item.condition}-{item.printing}-{item.language}-{item.bin or 'nobin'}"


# TCGplayer's "Pricing Custom Export" columns, in the exact order TCGplayer's
# staged-inventory / pricing upload expects (matches a real export). Only a
# handful drive an upload — TCGplayer Id, Condition, Add to Quantity and TCG
# Marketplace Price — the market/low reference columns are informational and
# filled from our local price data when available.
TCGPLAYER_HEADERS = [
    "TCGplayer Id", "Product Line", "Set Name", "Product Name", "Title",
    "Number", "Rarity", "Condition", "TCG Market Price", "TCG Direct Low",
    "TCG Low Price With Shipping", "TCG Low Price", "Total Quantity",
    "Add to Quantity", "TCG Marketplace Price", "Photo URL",
]

# Internal game code -> TCGplayer "Product Line" label.
_PRODUCT_LINE = {"mtg": "Magic", "pokemon": "Pokemon",
                 "onepiece": "One Piece Card Game", "yugioh": "YuGiOh"}


def _tcgplayer_layout(db: Session, items: list[InventoryItem]) -> tuple[list[str], list[list]]:
    from fastapi import HTTPException
    from .pricing import _pick_price_row, _price_rows
    from .tcg_counts import identity

    rows, used_skus = [], set()
    for it in items:
        card = it.card
        pid = card.tcgplayer_product_id if card else None
        pr = _pick_price_row(_price_rows(db, pid), it.printing) if pid else None
        listing = next((l for l in it.listings if l.marketplace == "tcgplayer"), None)
        metadata = (listing.tcg_metadata or {}) if listing else {}
        valid = metadata.get("identity") == identity(it)
        sku = listing.tcg_sku_id if listing and valid else ""
        raw = metadata.get("raw", {}) if valid else {}
        if sku and sku in used_skus:
            raise HTTPException(409, f"TCG SKU {sku} has multiple inventory links; reconcile before exporting")
        used_skus.add(sku)
        # Export the applied platform target, even when its manual override was
        # deliberately ignored during repricing and remains stored.
        our_price = listing.listed_price if listing else None
        if our_price is None:
            our_price = it.price_override if it.price_override is not None else it.current_price
        if our_price is not None:
            our_price = max(.01, our_price)
        condition = CONDITION_LABELS.get(it.condition, it.condition)
        suffix = {"foil": " Foil", "holo": " Holofoil", "reverse_holo": " Reverse Holofoil",
                  "first_edition": " 1st Edition"}.get(it.printing, "")
        rows.append([
            sku or "",
            raw.get("Product Line", _PRODUCT_LINE.get(card.game, card.game) if card else ""),
            raw.get("Set Name", card.set_name if card else ""),
            raw.get("Product Name", card.name if card else (it.custom_sku.product.name if it.custom_sku else "")),
            raw.get("Title", ""),
            raw.get("Number", card.collector_number if card else ""),
            raw.get("Rarity", card.rarity if card else ""),
            raw.get("Condition", condition + suffix),
            pr.market if pr and pr.market is not None else "",
            pr.direct_low if pr and pr.direct_low is not None else "",
            "",
            pr.low if pr and pr.low is not None else "",
            it.quantity,
            0,  # price-only; quantity corrections have their own cycle-count export
            our_price if our_price is not None else "",
            raw.get("Photo URL", card.image_url if card else ""),
        ])
    return TCGPLAYER_HEADERS, rows


def _ebay_layout(db: Session, items: list[InventoryItem]) -> tuple[list[str], list[list]]:
    headers = ["SKU", "Title", "ConditionID", "Card Condition", "Quantity", "StartPrice"]
    rows = []
    for it in items:
        name = it.card.name if it.card else (it.custom_sku.product.name if it.custom_sku else "")
        set_part = f" {it.card.set_name} {it.card.collector_number}" if it.card else ""
        rows.append([
            internal_sku(it),
            f"{name}{set_part} {CONDITION_LABELS.get(it.condition, '')}".strip()[:80],
            4000,  # eBay trading cards: Ungraded
            CONDITION_LABELS.get(it.condition, it.condition),
            it.quantity,
            it.price_override or it.current_price or "",
        ])
    return headers, rows


def build_export(db: Session, items: list[InventoryItem], *,
                 columns: list[str] | None = None, layout: str = "native",
                 exclude_zero: bool = True, merge_duplicates: bool = False) -> tuple[list[str], list[list]]:
    if exclude_zero:
        items = [i for i in items if i.quantity > 0]
    if layout == "tcgplayer":
        headers, rows = _tcgplayer_layout(db, items)
    elif layout == "ebay":
        headers, rows = _ebay_layout(db, items)
    else:
        cols = [c for c in (columns or DEFAULT_COLUMNS) if c in NATIVE_COLUMNS]
        headers = [NATIVE_COLUMNS[c][0] for c in cols]
        rows = [[NATIVE_COLUMNS[c][1](db, it) for c in cols] for it in items]
    if merge_duplicates:
        merged: dict[tuple, list] = {}
        try:
            qty_idx = next(i for i, h in enumerate(headers) if "quantity" in h.lower())
        except StopIteration:
            qty_idx = None
        for row in rows:
            key = tuple(v for i, v in enumerate(row) if i != qty_idx)
            if key in merged and qty_idx is not None:
                merged[key][qty_idx] += row[qty_idx] or 0
            else:
                merged[key] = list(row)
        rows = list(merged.values())
    return headers, rows


def to_csv_bytes(headers: list[str], rows: list[list]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(headers)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")


def to_xlsx_bytes(headers: list[str], rows: list[list]) -> bytes:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
