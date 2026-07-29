"""Inventory service: quantity mutations, audit logging, FIFO costing.

Every quantity change flows through apply_delta() so the audit log is
complete by construction. FIFO consumption/restoration is separate and
only used for sale deductions (and their reversals).
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
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


def purge_deleted(db: Session, preview: bool = False) -> dict:
    """Empty the trash: permanently remove soft-deleted rows and their cost basis.

    Soft delete only flips a flag — the row keeps its quantity and its FIFO
    batches — so deleted stock still reads as unsold in any batch-level roll-up
    (it inflated "units unsold" on Purchases by 4,103 units before this existed).

    A batch is dropped only when its pool has no live rows left AND nothing has
    ever consumed it; otherwise removing it would strip cost basis off a live row
    or orphan a sale's FIFO link, so it stays and is reported in ``batches_kept``.
    Audit entries are detached rather than deleted — each carries its own item
    description, so the trail outlives the row it described.
    """
    items = db.execute(select(InventoryItem).where(
        InventoryItem.deleted == True)).scalars().all()  # noqa: E712
    live_pools = {pool_key(i) for i in db.execute(select(InventoryItem).where(
        InventoryItem.deleted == False)).scalars()}  # noqa: E712
    consumed = {c.acquisition_id for c in db.execute(select(FifoConsumption)).scalars()}

    purged_pools = {pool_key(i) for i in items}
    dead_pools = purged_pools - live_pools
    pool_batches = [b for b in db.execute(select(AcquisitionLog)).scalars()
                    if pool_key(b) in purged_pools]
    droppable = [b for b in pool_batches
                 if pool_key(b) in dead_pools and b.id not in consumed]
    ids = [i.id for i in items]
    logs = db.execute(select(InventoryLog).where(
        InventoryLog.inventory_id.in_(ids))).scalars().all() if ids else []

    summary = {
        "items": len(items), "item_ids": ids,
        "units": sum(i.quantity for i in items),
        "listings": sum(len(i.listings) for i in items),
        "log_entries_detached": len(logs),
        "batches": len(droppable),
        "batch_units_remaining": sum(b.quantity_remaining for b in droppable),
        "batches_kept": len(pool_batches) - len(droppable),
        "preview": preview,
    }
    if preview:
        return summary

    for log in logs:
        log.inventory_id = None
    for batch in droppable:
        db.delete(batch)
    for item in items:
        for listing in item.listings:
            db.delete(listing)
        db.delete(item)
    db.commit()
    return summary


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


def pull_from_bulk(db: Session, target: InventoryItem, bulk: InventoryItem,
                   quantity: int, comment: str = "") -> int:
    """Pull `quantity` cards OUT of a bulk pile into a tracked inventory record.

    Moves both the units and their FIFO cost basis (oldest first, preserving
    unit_cost + acquired_at) from the pile onto `target` — no sale/COGS is
    booked and total cost basis is conserved, so the pulled card carries its
    share of the pile's per-card cost and inherits the pile's acquisition age.
    Deductions clamp at the pile's on-hand, so the return value (units actually
    moved) can be less than `quantity` if the pile ran short. Uses
    split_cost_basis (NOT record_acquisition) precisely so cost isn't
    double-counted against the pile's original purchase.
    """
    target_label = item_description(target)
    bulk_label = item_description(bulk)
    applied = apply_delta(db, bulk, -abs(quantity), type="deduction",
                          cause="pull_to_inventory",
                          comment=comment or f"pulled {abs(quantity)} to {target_label}")
    moved = -applied
    if moved > 0:
        apply_delta(db, target, moved, type="addition", cause="pull_from_bulk",
                    comment=comment or f"pulled from bulk {bulk_label}")
        split_cost_basis(db, bulk, target, moved)
    return moved


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
    return max(0, (datetime.now(timezone.utc) - _as_utc(oldest)).days)


def _as_utc(dt: datetime) -> datetime:
    """SQLite hands back naive datetimes; treat them as the UTC they were stored as."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def pool_key(row) -> tuple:
    """The FIFO pool an item (or acquisition batch) belongs to.

    Language and bin are deliberately NOT part of the key — see rekey_cost_basis.
    """
    return (row.catalog_card_id, row.custom_sku_id, row.condition, row.printing)


_pool_key = pool_key  # internal alias, kept for readability at call sites below


def lot_pool_keys(db: Session, date: str, unit_cost: float | None = None) -> set[tuple]:
    """FIFO pools touched by one purchase: every batch acquired on ``date``
    (``YYYY-MM-DD``), optionally narrowed to a single cost tier.

    The Purchases screen drills into inventory through these rather than through
    a cost match. A card from this purchase that you *also* owned from an earlier
    buy reports the older batch's cost, so a cost filter would drop it — but its
    pool is still part of this purchase and belongs in the drill-through.
    """
    q = select(AcquisitionLog).where(
        func.date(AcquisitionLog.acquired_at) == date)
    if unit_cost is not None:
        q = q.where(AcquisitionLog.unit_cost == unit_cost)
    return {pool_key(b) for b in db.execute(q).scalars()}


def fifo_rollup(db: Session, items: list[InventoryItem]) -> dict[int, dict]:
    """Per-item FIFO facts for a whole result set in ONE query.

    Returns ``{item_id: {"unit_cost", "age_days", "cost_basis"}}``. ``unit_cost``
    and ``age_days`` match fifo_unit_cost() / inventory_age_days() for a single
    item — the batched form exists because those are a query each, which does
    not scale to "value my whole inventory".

    One deliberate difference: when a pool is fully drawn down (a sold-out row),
    ``unit_cost`` falls back to the NEWEST spent batch — "what I last paid" —
    so the Inventory screen's cost column and Cost ≥/≤ filters still say
    something about stock that has sold through. fifo_unit_cost() keeps
    returning None there, because its callers (price floors, COGS) must not
    price off units that no longer exist. ``age_days`` gets no such fallback:
    "days since acquisition" is meaningless once nothing is on hand.

    ``cost_basis`` is the remaining cost of *this item's* on-hand units: walk
    the pool oldest-first and take ``quantity`` units. Allocation is shared
    across the items handed in, because a pool spans bins and languages — two
    bins of the same card+condition+printing draw on the same batches, so
    valuing each one independently would count the same dollars twice.
    """
    keys = {_pool_key(it) for it in items}
    pools: dict[tuple, list[AcquisitionLog]] = {}
    last_spent: dict[tuple, AcquisitionLog] = {}  # newest exhausted batch per pool
    if keys:
        batches = db.execute(select(AcquisitionLog)).scalars().all()
        for batch in batches:
            key = _pool_key(batch)
            if key not in keys:
                continue
            if batch.quantity_remaining > 0:
                pools.setdefault(key, []).append(batch)
                continue
            prev = last_spent.get(key)
            if prev is None or ((_as_utc(batch.acquired_at), batch.id)
                                > (_as_utc(prev.acquired_at), prev.id)):
                last_spent[key] = batch
        for pool in pools.values():
            pool.sort(key=lambda b: (_as_utc(b.acquired_at), b.id))

    now = datetime.now(timezone.utc)
    claimed: dict[int, int] = {}  # batch id -> units already assigned to an item
    out: dict[int, dict] = {}
    for item in sorted(items, key=lambda i: i.id):  # stable allocation order
        key = _pool_key(item)
        pool = pools.get(key, [])
        oldest = pool[0] if pool else None
        priced_at = oldest or last_spent.get(key)
        needed = max(0, item.quantity)
        cost_basis = 0.0
        for batch in pool:
            if needed <= 0:
                break
            take = min(batch.quantity_remaining - claimed.get(batch.id, 0), needed)
            if take <= 0:
                continue
            claimed[batch.id] = claimed.get(batch.id, 0) + take
            needed -= take
            cost_basis += take * batch.unit_cost
        out[item.id] = {
            "unit_cost": priced_at.unit_cost if priced_at else None,
            "age_days": (max(0, (now - _as_utc(oldest.acquired_at)).days)
                         if oldest else None),
            "cost_basis": round(cost_basis, 2),
        }
    return out


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
