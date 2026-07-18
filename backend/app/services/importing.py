"""CSV import (Section 3): field mapping, three modes, disambiguation, 15-min undo.

Matching requires a stable catalog identifier per row:
- scryfall_id / external_id
- tcgplayer_product_id (or SKU id)
- set_code + collector_number (+ game)
Name-only rows go to the ambiguous queue for manual disambiguation.
"""
import csv
import io
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import normalize_condition, normalize_printing
from ..models import CatalogCard, ImportBatch, ImportRow, InventoryItem, StagingItem
from . import inventory as inv_svc
from .settings import get_setting

# System fields available for column mapping in the UI
SYSTEM_FIELDS = [
    "external_id", "tcgplayer_product_id", "game", "set_code",
    "collector_number", "name", "condition", "printing", "language",
    "quantity", "bin", "cost", "date_acquired", "comment", "price",
]


def _parse_acquired(mapped: dict):
    """Parse a date_acquired cell into a tz-aware datetime, trying common formats."""
    raw = mapped.get("date_acquired")
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(str(raw).strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def parse_csv(content: bytes) -> tuple[list[str], list[dict]]:
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = [dict(r) for r in reader]
    return list(reader.fieldnames or []), rows


def apply_mapping(raw: dict, mapping: dict, value_maps: dict | None = None) -> dict:
    """mapping: {csv_column: system_field}; value_maps: {system_field: {raw: canonical}}."""
    mapped: dict = {}
    for col, field in mapping.items():
        if not field or field == "ignore":
            continue
        v = (raw.get(col) or "").strip()
        if value_maps and field in value_maps and v in value_maps[field]:
            v = value_maps[field][v]
        mapped[field] = v
    game = (mapped.get("game") or "").lower().strip()
    mapped["game"] = {"magic": "mtg", "magic: the gathering": "mtg",
                      "pokémon": "pokemon", "one piece": "onepiece",
                      "yu-gi-oh!": "yugioh", "ygo": "yugioh"}.get(game, game)
    if "condition" in mapped:
        mapped["condition"] = normalize_condition(mapped["condition"])
    if "printing" in mapped:
        mapped["printing"] = normalize_printing(mapped["printing"], mapped.get("game") or None)
    return mapped


def match_card(db: Session, mapped: dict) -> tuple[CatalogCard | None, list[CatalogCard]]:
    """Resolve a row to a catalog card. Returns (match, candidates-if-ambiguous)."""
    if mapped.get("external_id"):
        card = db.execute(select(CatalogCard).where(
            CatalogCard.external_id == mapped["external_id"])).scalars().first()
        if card:
            return card, []
    if mapped.get("tcgplayer_product_id"):
        try:
            pid = int(float(mapped["tcgplayer_product_id"]))
        except ValueError:
            pid = None
        if pid:
            cards = db.execute(select(CatalogCard).where(
                CatalogCard.tcgplayer_product_id == pid)).scalars().all()
            if len(cards) == 1:
                return cards[0], []
            if cards:
                return None, cards
    if mapped.get("set_code") and mapped.get("collector_number"):
        q = select(CatalogCard).where(
            CatalogCard.set_code.ilike(mapped["set_code"]),
            CatalogCard.collector_number == str(mapped["collector_number"]).lstrip("0")
            if str(mapped["collector_number"]).isdigit()
            else CatalogCard.collector_number == mapped["collector_number"],
        )
        if mapped.get("game"):
            q = q.where(CatalogCard.game == mapped["game"])
        cards = db.execute(q).scalars().all()
        if len(cards) == 1:
            return cards[0], []
        if cards:
            return None, cards
    # name-only fallback: never auto-match, queue for disambiguation
    if mapped.get("name"):
        q = select(CatalogCard).where(CatalogCard.name.ilike(mapped["name"]))
        if mapped.get("game"):
            q = q.where(CatalogCard.game == mapped["game"])
        if mapped.get("set_code"):
            q = q.where(CatalogCard.set_code.ilike(f"%{mapped['set_code']}%"))
        cards = db.execute(q.limit(20)).scalars().all()
        if len(cards) == 1 and mapped.get("set_code"):
            return cards[0], []
        return None, cards
    return None, []


def run_import(db: Session, *, filename: str, content: bytes, mapping: dict,
               value_maps: dict | None, mode: str = "add",
               to_staging: bool = True) -> ImportBatch:
    """Execute an import. mode: add | overwrite | deduction.

    add/overwrite rows land in staging by default (to_staging), deduction
    applies directly (it reflects already-happened marketplace sales).
    """
    _, raws = parse_csv(content)
    batch = ImportBatch(filename=filename, mode=mode, status="in_progress",
                        row_count=len(raws))
    db.add(batch)
    db.flush()
    qty_total = errors = 0

    for raw in raws:
        mapped = apply_mapping(raw, mapping, value_maps)
        row = ImportRow(batch_id=batch.id, raw=raw, mapped=mapped)
        db.add(row)
        try:
            qty = int(float(mapped.get("quantity") or 1))
        except ValueError:
            qty = 1
        card, candidates = match_card(db, mapped)
        if card is None:
            if candidates:
                row.status = "ambiguous"
                row.candidates = [
                    {"card_id": c.id, "name": c.name, "set_code": c.set_code,
                     "set_name": c.set_name, "collector_number": c.collector_number,
                     "image_url": c.image_url} for c in candidates[:10]]
            else:
                row.status = "error"
                row.error = "no catalog match (stable identifier missing or unknown)"
                errors += 1
            continue

        cost = None
        if mapped.get("cost"):
            try:
                cost = float(str(mapped["cost"]).replace("$", ""))
            except ValueError:
                pass
        acquired_at = _parse_acquired(mapped)

        applied = _apply_row(db, batch, row, card, mapped, qty, cost, acquired_at,
                             mode, to_staging)
        qty_total += applied

    batch.quantity_total = qty_total
    batch.error_count = errors
    ambiguous = any(r.status == "ambiguous" for r in batch.rows)
    batch.status = ("partially_complete" if (errors or ambiguous)
                    else "completed") if len(raws) else "completed"
    db.commit()
    return batch


def _apply_row(db: Session, batch: ImportBatch, row: ImportRow, card: CatalogCard,
               mapped: dict, qty: int, cost: float | None, acquired_at,
               mode: str, to_staging: bool) -> int:
    condition = mapped.get("condition", "NM")
    # re-normalize with the matched card's game: a CSV without a game column
    # can't apply game-specific remaps (e.g. "holo" -> "foil" for MTG) earlier
    printing = normalize_printing(mapped.get("printing", "normal"), card.game)
    language = mapped.get("language") or "en"
    bin_name = mapped.get("bin", "")
    price = None
    if mapped.get("price"):
        try:
            price = float(str(mapped["price"]).replace("$", ""))
        except ValueError:
            pass

    if mode == "deduction":
        # find any live record matching identity, preferring exact bin
        q = select(InventoryItem).where(
            InventoryItem.catalog_card_id == card.id,
            InventoryItem.condition == condition,
            InventoryItem.printing == printing,
            InventoryItem.deleted == False,  # noqa: E712
            InventoryItem.quantity > 0,
        )
        items = db.execute(q).scalars().all()
        if not items:
            row.status = "error"
            row.error = "deduction target not in inventory"
            batch.error_count += 1
            return 0
        remaining = qty
        for item in items:
            if remaining <= 0:
                break
            take = min(item.quantity, remaining)
            inv_svc.apply_delta(db, item, -take, type="deduction",
                                cause="csv_import",
                                comment=f"order-export deduction (batch {batch.id})",
                                source="platform")
            inv_svc.consume_fifo(db, item, take)
            remaining -= take
            row.inventory_id = item.id
        row.status = "imported"
        row.quantity_applied = -(qty - remaining)
        return qty - remaining

    if to_staging and mode == "add":
        db.add(StagingItem(
            source="csv", catalog_card_id=card.id, condition=condition,
            printing=printing, language=language, bin=bin_name, quantity=qty,
            cost=cost, price=price, acquired_at=acquired_at,
            comment=mapped.get("comment", ""), import_batch_id=batch.id))
        row.status = "staged"
        row.quantity_applied = qty
        return qty

    item = inv_svc.find_or_create_item(
        db, catalog_card_id=card.id, condition=condition,
        printing=printing, language=language, bin=bin_name)
    if mapped.get("comment"):
        item.comment = mapped["comment"]
    if price is not None:
        item.current_price = price
    if mode == "overwrite":
        delta = qty - item.quantity
        if delta > 0:
            inv_svc.add_stock(db, item, delta, cost, cause="csv_import",
                              comment=f"overwrite import (batch {batch.id})",
                              acquired_at=acquired_at)
        elif delta < 0:
            inv_svc.apply_delta(db, item, delta, type="adjustment",
                                cause="csv_import",
                                comment=f"overwrite import (batch {batch.id})")
        row.status = "imported"
        row.inventory_id = item.id
        row.quantity_applied = delta
        return abs(delta)

    inv_svc.add_stock(db, item, qty, cost, cause="csv_import",
                      comment=f"import batch {batch.id}", acquired_at=acquired_at)
    row.status = "imported"
    row.inventory_id = item.id
    row.quantity_applied = qty
    return qty


def resolve_ambiguous_row(db: Session, row: ImportRow, card_id: int,
                          to_staging: bool = True) -> None:
    """User picked a card for an ambiguous row via the disambiguation UI."""
    card = db.get(CatalogCard, card_id)
    if card is None:
        raise ValueError("unknown card")
    batch = row.batch
    mapped = row.mapped
    try:
        qty = int(float(mapped.get("quantity") or 1))
    except ValueError:
        qty = 1
    cost = None
    if mapped.get("cost"):
        try:
            cost = float(str(mapped["cost"]).replace("$", ""))
        except ValueError:
            pass
    _apply_row(db, batch, row, card, mapped, qty, cost, _parse_acquired(mapped),
               batch.mode, to_staging)
    db.commit()


def undo_import(db: Session, batch: ImportBatch) -> dict:
    """Undo an import within the configured window. Removes exactly the added
    quantities (clamped at 0); warns on rows already sold/deleted."""
    window = int(get_setting(db, "import_undo_window_minutes"))
    created = batch.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - created > timedelta(minutes=window):
        raise ValueError(f"undo window ({window} min) has expired")
    warnings = []
    undone = 0
    for row in batch.rows:
        if row.status == "staged":
            staged = db.execute(select(StagingItem).where(
                StagingItem.import_batch_id == batch.id)).scalars().all()
            for s in staged:
                db.delete(s)
            row.status = "undone"
            undone += 1
            continue
        if row.status != "imported" or not row.inventory_id:
            continue
        item = db.get(InventoryItem, row.inventory_id)
        if item is None or item.deleted:
            warnings.append(f"row {row.id}: item deleted, skipped")
            continue
        delta = row.quantity_applied
        if delta > 0:
            actual = inv_svc.apply_delta(db, item, -delta, type="deduction",
                                         cause="undo",
                                         comment=f"undo import batch {batch.id}")
            # also unwind the acquisition lot(s) this import created, so undo
            # doesn't leave phantom cost basis behind
            inv_svc.unrecord_acquisition(db, item, -actual)
            if -actual < delta:
                warnings.append(
                    f"row {row.id}: only {-actual}/{delta} removed (some already sold)")
        elif delta < 0:
            inv_svc.apply_delta(db, item, -delta, type="addition", cause="undo",
                                comment=f"undo import batch {batch.id}")
        row.status = "undone"
        undone += 1
    batch.status = "undone"
    db.commit()
    return {"undone": undone, "warnings": warnings}
