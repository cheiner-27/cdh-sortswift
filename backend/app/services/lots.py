"""Bulk lot builder (Section 7). Lot items are reserved in inventory until
the lot sells or is dissolved (reservation enforced by sync.push_quantity)."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import InventoryItem, Lot, LotItem, LotTemplate
from . import inventory as inv_svc
from .marketplaces.sync import lot_reserved_qty


def _matching_items(db: Session, filters: dict) -> list[InventoryItem]:
    q = select(InventoryItem).where(
        InventoryItem.deleted == False,  # noqa: E712
        InventoryItem.quantity > 0,
        InventoryItem.catalog_card_id.isnot(None),
    )
    items = db.execute(q).scalars().all()
    out = []
    for it in items:
        card = it.card
        if filters.get("games") and card.game not in filters["games"]:
            continue
        if filters.get("sets") and card.set_code not in filters["sets"]:
            continue
        if filters.get("rarities") and (card.rarity or "").lower() not in filters["rarities"]:
            continue
        if filters.get("conditions") and it.condition not in filters["conditions"]:
            continue
        value = it.price_override or it.current_price
        if filters.get("price_min") is not None and (value is None or value < filters["price_min"]):
            continue
        if filters.get("price_max") is not None and (value is None or value > filters["price_max"]):
            continue
        out.append(it)
    return out


def generate_lot(db: Session, template: LotTemplate, name: str | None = None) -> Lot:
    """Generate a lot from current inventory matching the template filters.

    Regenerating after inventory changes produces a new lot from remaining
    (unreserved) stock.
    """
    candidates = _matching_items(db, template.filters or {})
    lot = Lot(template_id=template.id,
              name=name or f"{template.name} #{db.query(Lot).count() + 1}")
    db.add(lot)
    db.flush()

    picked = 0
    total_value = 0.0
    for it in sorted(candidates, key=lambda x: (x.current_price or 0), reverse=True):
        if picked >= template.lot_size:
            break
        available = it.quantity - lot_reserved_qty(db, it)
        if available <= 0:
            continue
        take = min(available, template.max_duplicates, template.lot_size - picked)
        if take <= 0:
            continue
        unit_value = it.price_override or it.current_price or 0.0
        db.add(LotItem(lot_id=lot.id, inventory_id=it.id, quantity=take,
                       unit_value=unit_value))
        picked += take
        total_value += take * unit_value

    lot.total_value = round(total_value, 2)
    if template.pricing_method == "fixed" and template.fixed_price:
        lot.price = template.fixed_price
    else:
        lot.price = round(total_value * template.margin_pct / 100.0, 2)
    db.commit()
    return lot


def dissolve_lot(db: Session, lot: Lot) -> None:
    """Release reservations; inventory quantities were never deducted."""
    lot.status = "dissolved"
    for li in lot.items:
        db.delete(li)
    db.commit()


def sell_lot(db: Session, lot: Lot, order_id: int | None = None) -> float:
    """Deduct all reserved units (lot sold). Returns total COGS."""
    total_cogs = 0.0
    for li in lot.items:
        item = db.get(InventoryItem, li.inventory_id)
        if item is None:
            continue
        applied = inv_svc.apply_delta(
            db, item, -li.quantity, type="deduction", cause="sale",
            comment=f"lot '{lot.name}' sold", source="platform")
        total_cogs += inv_svc.consume_fifo(db, item, -applied, order_id=order_id)
    lot.status = "sold"
    db.commit()
    return total_cogs
