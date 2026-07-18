"""Inventory service: quantity mutations, audit logging, FIFO costing.

Every quantity change flows through apply_delta() so the audit log is
complete by construction. FIFO consumption/restoration is separate and
only used for sale deductions (and their reversals).
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    AcquisitionLog, FifoConsumption, InventoryItem, InventoryLog,
    MarketplaceListing, utcnow,
)


def item_description(item: InventoryItem) -> str:
    if item.card:
        return f"{item.card.name} [{item.card.set_code} {item.card.collector_number}] {item.condition} {item.printing}"
    if item.custom_sku and item.custom_sku.product:
        return f"{item.custom_sku.product.name} (custom) {item.condition or ''}"
    return f"inventory #{item.id}"


def log_mutation(
    db: Session, item: InventoryItem, type: str, delta: int,
    cause: str = "manual", comment: str = "", source: str = "staff",
    bin_before: str | None = None, bin_after: str | None = None,
    cost_at: float | None = None,
) -> InventoryLog:
    entry = InventoryLog(
        inventory_id=item.id,
        item_description=item_description(item),
        type=type,
        quantity_delta=delta,
        price_at=item.current_price or item.price_override,
        cost_at=cost_at,
        bin_before=bin_before,
        bin_after=bin_after,
        comment=comment,
        cause=cause,
        source=source,
    )
    db.add(entry)
    return entry


def find_or_create_item(
    db: Session, *, catalog_card_id: int | None = None, custom_sku_id: int | None = None,
    condition: str = "NM", printing: str = "normal", language: str = "en", bin: str = "",
) -> InventoryItem:
    """Find the inventory record matching this identity+bin, or create it at qty 0."""
    q = select(InventoryItem).where(
        InventoryItem.catalog_card_id == catalog_card_id,
        InventoryItem.custom_sku_id == custom_sku_id,
        InventoryItem.condition == condition,
        InventoryItem.printing == printing,
        InventoryItem.language == language,
        InventoryItem.bin == bin,
        InventoryItem.deleted == False,  # noqa: E712
    )
    item = db.execute(q).scalars().first()
    if item is None:
        item = InventoryItem(
            catalog_card_id=catalog_card_id, custom_sku_id=custom_sku_id,
            condition=condition, printing=printing, language=language, bin=bin,
            quantity=0,
        )
        db.add(item)
        db.flush()
    return item


def apply_delta(
    db: Session, item: InventoryItem, delta: int, *, type: str | None = None,
    cause: str = "manual", comment: str = "", source: str = "staff",
    cost_at: float | None = None, clamp: bool = True,
) -> int:
    """Apply a quantity delta. Deductions clamp at 0 (never negative).

    Returns the delta actually applied. Marks marketplace listings dirty.
    """
    if delta < 0 and clamp:
        delta = -min(-delta, item.quantity)
    item.quantity += delta
    item.updated_at = utcnow()
    for listing in item.listings:
        listing.dirty = True
    if type is None:
        type = "addition" if delta >= 0 else "deduction"
    log_mutation(db, item, type, delta, cause=cause, comment=comment,
                 source=source, cost_at=cost_at)
    return delta


def record_acquisition(
    db: Session, item: InventoryItem, quantity: int, unit_cost: float | None,
    acquired_at: datetime | None = None,
) -> AcquisitionLog:
    """Record a FIFO cost batch for units being added."""
    entry = AcquisitionLog(
        catalog_card_id=item.catalog_card_id,
        custom_sku_id=item.custom_sku_id,
        condition=item.condition,
        printing=item.printing,
        language=item.language,
        quantity=quantity,
        quantity_remaining=quantity,
        unit_cost=unit_cost or 0.0,
        acquired_at=acquired_at or utcnow(),
    )
    db.add(entry)
    db.flush()
    return entry


def add_stock(
    db: Session, item: InventoryItem, quantity: int, unit_cost: float | None = None,
    cause: str = "manual", comment: str = "", source: str = "staff",
    acquired_at: datetime | None = None,
) -> None:
    """Add stock: quantity delta + FIFO acquisition batch in one step."""
    apply_delta(db, item, quantity, type="addition", cause=cause,
                comment=comment, source=source, cost_at=unit_cost)
    record_acquisition(db, item, quantity, unit_cost, acquired_at=acquired_at)


def _fifo_batches(db: Session, item: InventoryItem):
    """Oldest-first unexhausted acquisition batches for this card identity."""
    q = (
        select(AcquisitionLog)
        .where(
            AcquisitionLog.catalog_card_id == item.catalog_card_id,
            AcquisitionLog.custom_sku_id == item.custom_sku_id,
            AcquisitionLog.condition == item.condition,
            AcquisitionLog.printing == item.printing,
            AcquisitionLog.quantity_remaining > 0,
        )
        .order_by(AcquisitionLog.acquired_at.asc(), AcquisitionLog.id.asc())
    )
    return db.execute(q).scalars().all()


def consume_fifo(db: Session, item: InventoryItem, quantity: int,
                 order_id: int | None = None) -> float:
    """Consume `quantity` units from oldest acquisition batches. Returns total COGS.

    If acquisition history is short (e.g. migrated inventory without cost
    data), missing units carry zero cost rather than blocking the sale.
    """
    remaining = quantity
    total_cogs = 0.0
    for batch in _fifo_batches(db, item):
        if remaining <= 0:
            break
        take = min(batch.quantity_remaining, remaining)
        batch.quantity_remaining -= take
        remaining -= take
        total_cogs += take * batch.unit_cost
        db.add(FifoConsumption(
            acquisition_id=batch.id, order_id=order_id,
            quantity=take, unit_cost=batch.unit_cost,
        ))
    return total_cogs


def return_to_supplier(db: Session, item: InventoryItem, quantity: int,
                       comment: str = "") -> dict:
    """Full supplier refund on a purchase: send `quantity` units back and recover
    their cost. Removes the units from inventory and draws down the FIFO
    acquisition batches (oldest first) without booking a sale/COGS — the cost is
    recovered, not lost, so there's no P&L hit. Returns units removed + cost
    recovered."""
    applied = apply_delta(db, item, -abs(quantity), type="deduction",
                          cause="return_to_supplier",
                          comment=comment or "returned to supplier (full refund)")
    remaining = -applied
    cost_recovered = 0.0
    for batch in _fifo_batches(db, item):
        if remaining <= 0:
            break
        take = min(batch.quantity_remaining, remaining)
        batch.quantity_remaining -= take
        remaining -= take
        cost_recovered += take * batch.unit_cost
    return {"units": -applied, "cost_recovered": round(cost_recovered, 2)}


def reduce_cost_basis(db: Session, item: InventoryItem, refund_amount: float,
                      comment: str = "") -> dict:
    """Partial supplier refund on a purchase: keep the goods but recover
    `refund_amount` of cost, applied **FIFO** — draw the dollars off the oldest
    unexhausted acquisition batch first, overflowing into the next only once the
    oldest is fully written down. This lowers the cost of the units that will
    sell first (so near-term COGS drops), rather than averaging the refund across
    every unit. unit_cost floors at 0; any amount beyond the total remaining cost
    basis is reported as `unapplied`."""
    remaining = float(refund_amount)
    applied_total = 0.0
    batches_touched = 0
    for batch in _fifo_batches(db, item):  # oldest first
        if remaining <= 1e-9:
            break
        batch_cost = batch.quantity_remaining * batch.unit_cost
        take = min(remaining, batch_cost)
        if take <= 0:
            continue
        batch.unit_cost = round((batch_cost - take) / batch.quantity_remaining, 4)
        remaining -= take
        applied_total += take
        batches_touched += 1
    if applied_total <= 0:
        return {"applied": 0.0, "batches": 0, "unapplied": round(remaining, 2)}
    log_mutation(db, item, "adjustment", 0, cause="supplier_refund",
                 comment=comment or f"partial supplier refund ${applied_total:.2f} "
                 f"applied FIFO (oldest cost first) across {batches_touched} batch(es)")
    return {"applied": round(applied_total, 2), "batches": batches_touched,
            "unapplied": round(max(0.0, remaining), 2)}


def split_cost_basis(db: Session, source: InventoryItem, target: InventoryItem,
                     quantity: int) -> float:
    """Move `quantity` units of cost basis from `source` to `target`, oldest
    first, preserving each slice's unit_cost AND acquired_at (so FIFO cost and
    inventory age carry across a split/transfer instead of being duplicated or
    reset). Returns the total cost moved. The caller adjusts the item quantities."""
    remaining = quantity
    moved_cost = 0.0
    for batch in _fifo_batches(db, source):  # oldest first
        if remaining <= 0:
            break
        take = min(batch.quantity_remaining, remaining)
        batch.quantity_remaining -= take
        remaining -= take
        moved_cost += take * batch.unit_cost
        db.add(AcquisitionLog(
            catalog_card_id=target.catalog_card_id,
            custom_sku_id=target.custom_sku_id,
            condition=target.condition, printing=target.printing,
            language=target.language, quantity=take, quantity_remaining=take,
            unit_cost=batch.unit_cost, acquired_at=batch.acquired_at))
    return round(moved_cost, 4)


def rekey_cost_basis(db: Session, item: InventoryItem, *, old_condition: str,
                     old_printing: str) -> float:
    """Follow the FIFO cost basis when an item's condition/printing changes.

    The FIFO pool key is (card/sku, condition, printing) — language and bin are
    NOT part of it. So changing condition or printing moves the item to a
    different pool, and its acquisition batches (still keyed to the OLD
    condition/printing) would be orphaned, silently dropping the item's cost,
    age and acquisition lots. This moves up to ``item.quantity`` units of cost
    basis (oldest first, preserving unit_cost + acquired_at) from the old pool
    key onto the item's current (new) condition/printing so cost/age follow the
    reclassification. Moving exactly the item's on-hand quantity keeps any other
    item sharing the old pool (e.g. a different bin) consistent too.
    Returns the total cost basis moved.
    """
    if (old_condition, old_printing) == (item.condition, item.printing):
        return 0.0
    q = (
        select(AcquisitionLog)
        .where(
            AcquisitionLog.catalog_card_id == item.catalog_card_id,
            AcquisitionLog.custom_sku_id == item.custom_sku_id,
            AcquisitionLog.condition == old_condition,
            AcquisitionLog.printing == old_printing,
            AcquisitionLog.quantity_remaining > 0,
        )
        .order_by(AcquisitionLog.acquired_at.asc(), AcquisitionLog.id.asc())
    )
    remaining = item.quantity
    moved = 0.0
    for batch in db.execute(q).scalars().all():
        if remaining <= 0:
            break
        take = min(batch.quantity_remaining, remaining)
        batch.quantity_remaining -= take
        remaining -= take
        moved += take * batch.unit_cost
        db.add(AcquisitionLog(
            catalog_card_id=item.catalog_card_id,
            custom_sku_id=item.custom_sku_id,
            condition=item.condition, printing=item.printing,
            language=item.language, quantity=take, quantity_remaining=take,
            unit_cost=batch.unit_cost, acquired_at=batch.acquired_at))
    log_mutation(db, item, "adjustment", 0, cause="manual",
                 comment=f"reclassified {old_condition}/{old_printing} -> "
                 f"{item.condition}/{item.printing} "
                 f"(moved ${round(moved, 2)} cost basis)")
    return round(moved, 4)


def unrecord_acquisition(db: Session, item: InventoryItem, quantity: int) -> int:
    """Reverse a recent acquisition (e.g. import undo): draw `quantity` off the
    NEWEST batches (reverse-FIFO) so the just-added lots disappear cleanly rather
    than leaving phantom cost basis. Returns units actually removed."""
    remaining = quantity
    batches = sorted(_fifo_batches(db, item),
                     key=lambda b: (b.acquired_at, b.id), reverse=True)
    for batch in batches:
        if remaining <= 0:
            break
        take = min(batch.quantity_remaining, remaining)
        batch.quantity_remaining -= take
        remaining -= take
    return quantity - remaining


def restore_fifo(db: Session, order_id: int) -> None:
    """Reverse all FIFO consumption for an order (refund/cancel/replay)."""
    rows = db.execute(
        select(FifoConsumption).where(FifoConsumption.order_id == order_id)
    ).scalars().all()
    for row in rows:
        batch = db.get(AcquisitionLog, row.acquisition_id)
        if batch:
            batch.quantity_remaining = min(
                batch.quantity, batch.quantity_remaining + row.quantity)
        db.delete(row)


def oldest_acquisition_date(db: Session, item: InventoryItem) -> datetime | None:
    batches = _fifo_batches(db, item)
    return batches[0].acquired_at if batches else None


def fifo_unit_cost(db: Session, item: InventoryItem) -> float | None:
    """Cost of the oldest unexhausted unit (used by cost-based price floor)."""
    batches = _fifo_batches(db, item)
    return batches[0].unit_cost if batches else None


def inventory_age_days(db: Session, item: InventoryItem) -> int | None:
    oldest = oldest_acquisition_date(db, item)
    if oldest is None:
        return None
    if oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - oldest).days)


def transfer_bin(db: Session, item: InventoryItem, new_bin: str,
                 cause: str = "transfer", comment: str = "") -> None:
    old = item.bin
    if old == new_bin:
        return
    item.bin = new_bin
    item.updated_at = utcnow()
    log_mutation(db, item, "transfer", 0, cause=cause, comment=comment,
                 bin_before=old, bin_after=new_bin)


def get_or_create_listing(db: Session, item: InventoryItem,
                          marketplace: str) -> MarketplaceListing:
    for listing in item.listings:
        if listing.marketplace == marketplace:
            return listing
    listing = MarketplaceListing(inventory_id=item.id, marketplace=marketplace)
    db.add(listing)
    db.flush()
    item.listings.append(listing)
    return listing


def effective_quantity(item: InventoryItem, marketplace: str) -> int:
    """Derived listed quantity for a marketplace (Section 6.1).

    effective = raw quantity
              - reserves held for OTHER marketplaces
              - quantity reserved in open lots (handled by caller via lot reservation)
    capped by this marketplace's listing_cap (0 = excluded entirely).
    """
    qty = item.quantity
    this_listing = None
    for listing in item.listings:
        if listing.marketplace == marketplace:
            this_listing = listing
        else:
            qty -= listing.reserve_quantity
    qty = max(0, qty)
    if this_listing is not None and this_listing.listing_cap is not None:
        qty = min(qty, this_listing.listing_cap)
    return qty
