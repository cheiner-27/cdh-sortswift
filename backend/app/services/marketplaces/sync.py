"""Marketplace sync orchestration (Section 6).

Four distinct listing operations: Resync, Push Remaining, Clear Listing IDs,
Rebuild. Order polling applies deductions with replay safety and cross-delists
the other marketplace in the same pass.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import (
    InventoryItem, LotItem, MarketplaceAccount, MarketplaceListing, Order,
    OrderItem, utcnow,
)
from .. import inventory as inv_svc
from ..exporting import internal_sku
from .base import ListingError, MarketplaceAdapter, find_rule
from .ebay import EbayAdapter
from .tcgplayer import TcgplayerAdapter

log = logging.getLogger(__name__)

ADAPTERS: dict[str, MarketplaceAdapter] = {
    "ebay": EbayAdapter(),
    "tcgplayer": TcgplayerAdapter(),
}


def account(db: Session, marketplace: str) -> MarketplaceAccount | None:
    return db.execute(select(MarketplaceAccount).where(
        MarketplaceAccount.marketplace == marketplace)).scalars().first()


def _push_allowed(db: Session, marketplace: str) -> bool:
    acct = account(db, marketplace)
    return bool(acct and acct.status == "connected")


def lot_reserved_qty(db: Session, item: InventoryItem) -> int:
    """Units reserved in open/listed lots (Section 7)."""
    rows = db.execute(
        select(LotItem).join(LotItem.lot).where(
            LotItem.inventory_id == item.id,
        )
    ).scalars().all()
    return sum(li.quantity for li in rows if li.lot.status in ("open", "listed"))


def push_quantity(db: Session, item: InventoryItem, marketplace: str) -> int:
    """Derived quantity to expose (6.1): master − other-marketplace reserves
    − lot reservations, capped by listing cap."""
    qty = inv_svc.effective_quantity(item, marketplace)
    return max(0, qty - lot_reserved_qty(db, item))


def _record_error(listing: MarketplaceListing, err: ListingError) -> None:
    listing.status = "error"
    listing.error_code = err.code
    listing.error_message = str(err)


def _clear_error(listing: MarketplaceListing) -> None:
    listing.error_code = None
    listing.error_message = None


def _has_external_id(listing: MarketplaceListing) -> bool:
    if listing.marketplace == "ebay":
        return bool(listing.ebay_listing_id or listing.ebay_offer_id)
    return bool(listing.tcg_sku_id)


def _price_for(item: InventoryItem, listing: MarketplaceListing) -> float | None:
    return listing.listed_price or item.price_override or item.current_price


def _push_one(db: Session, item: InventoryItem, marketplace: str,
              create_if_missing: bool) -> str:
    """Push one item. Returns: created|updated|delisted|skipped|error."""
    adapter = ADAPTERS[marketplace]
    listing = inv_svc.get_or_create_listing(db, item, marketplace)
    price = _price_for(item, listing)
    qty = push_quantity(db, item, marketplace)

    try:
        if _has_external_id(listing):
            if qty <= 0:
                adapter.end_listing(db, listing)
                listing.status = "unlisted"
                listing.listed_quantity = 0
                listing.dirty = False
                listing.last_synced_at = utcnow()
                _clear_error(listing)
                return "delisted"
            if price is None:
                raise ListingError("missing_field", "no price set (run reprice first)")
            adapter.update_listing(db, item, listing, price, qty)
            listing.status = "listed"
            listing.listed_price = price
            listing.listed_quantity = qty
            listing.dirty = False
            listing.last_synced_at = utcnow()
            _clear_error(listing)
            return "updated"

        if not create_if_missing:
            return "skipped"
        if qty <= 0:
            return "skipped"
        if price is None:
            raise ListingError("missing_field", "no price set (run reprice first)")
        rule = find_rule(db, marketplace, item, price)
        if rule is None:
            raise ListingError("no_matching_rule",
                               "no active listing rule matches this item")
        adapter.create_listing(db, item, listing, rule, price, qty)
        listing.status = "listed"
        listing.listed_price = price
        listing.listed_quantity = qty
        listing.dirty = False
        listing.last_synced_at = utcnow()
        _clear_error(listing)
        return "created"
    except ListingError as e:
        _record_error(listing, e)
        return "error"


def resync(db: Session, marketplace: str, items: list[InventoryItem],
           changed_only: bool = True) -> dict:
    """Refresh price/qty on listings that already have an external ID.
    Never creates new listings."""
    if not _push_allowed(db, marketplace):
        return {"error": f"{marketplace} not connected (or paused)"}
    counts = {"updated": 0, "delisted": 0, "skipped": 0, "error": 0}
    for item in items:
        listing = next((l for l in item.listings if l.marketplace == marketplace), None)
        if listing is None or not _has_external_id(listing):
            counts["skipped"] += 1
            continue
        if changed_only and not listing.dirty:
            counts["skipped"] += 1
            continue
        if listing.error_code:  # errored items excluded from bulk ops (6.4)
            counts["skipped"] += 1
            continue
        result = _push_one(db, item, marketplace, create_if_missing=False)
        counts[result if result in counts else "skipped"] += 1
    db.commit()
    return counts


def push_remaining(db: Session, marketplace: str, items: list[InventoryItem]) -> dict:
    """Create listings only for eligible records with no stored listing ID."""
    if not _push_allowed(db, marketplace):
        return {"error": f"{marketplace} not connected (or paused)"}
    counts = {"created": 0, "skipped": 0, "error": 0}
    for item in items:
        listing = next((l for l in item.listings if l.marketplace == marketplace), None)
        if listing is not None and _has_external_id(listing):
            counts["skipped"] += 1
            continue
        if listing is not None and listing.error_code:
            counts["skipped"] += 1
            continue
        result = _push_one(db, item, marketplace, create_if_missing=True)
        counts[result if result in counts else "skipped"] += 1
    db.commit()
    return counts


def sync_single(db: Session, marketplace: str, item: InventoryItem) -> dict:
    """On-demand sync of one record from its detail view. Re-attempts errored
    items individually (the recovery path from 6.4)."""
    if not _push_allowed(db, marketplace):
        return {"error": f"{marketplace} not connected (or paused)"}
    listing = inv_svc.get_or_create_listing(db, item, marketplace)
    _clear_error(listing)
    result = _push_one(db, item, marketplace, create_if_missing=True)
    db.commit()
    return {"result": result,
            "error_code": listing.error_code,
            "error_message": listing.error_message}


def clear_listing_ids(db: Session, marketplace: str,
                      items: list[InventoryItem]) -> int:
    """Local-only: forget stored listing IDs (recovers from out-of-band
    deletions / phantom-listed state). No marketplace API calls."""
    n = 0
    for item in items:
        for listing in item.listings:
            if listing.marketplace != marketplace:
                continue
            listing.ebay_sku = listing.ebay_offer_id = listing.ebay_listing_id = None
            listing.tcg_sku_id = None
            listing.status = "unlisted"
            listing.listed_quantity = 0
            listing.dirty = True
            _clear_error(listing)
            n += 1
    db.commit()
    return n


def rebuild(db: Session, marketplace: str, items: list[InventoryItem]) -> dict:
    """Destructive: end existing listings, clear IDs, re-push. Only for broken
    listing rules (wrong category, deleted policy reference)."""
    if not _push_allowed(db, marketplace):
        return {"error": f"{marketplace} not connected (or paused)"}
    adapter = ADAPTERS[marketplace]
    for item in items:
        for listing in item.listings:
            if listing.marketplace == marketplace and _has_external_id(listing):
                try:
                    adapter.end_listing(db, listing)
                except ListingError as e:
                    log.warning("rebuild end_listing failed: %s", e)
    clear_listing_ids(db, marketplace, items)
    return push_remaining(db, marketplace, items)


# ---------------------------------------------------------------------------
# Order polling, deduction, cross-delist
# ---------------------------------------------------------------------------

def _find_inventory_by_sku(db: Session, sku: str) -> InventoryItem | None:
    for it in db.execute(select(InventoryItem).where(
            InventoryItem.deleted == False)).scalars():  # noqa: E712
        if internal_sku(it) == sku:
            return it
    return None


def apply_order_deduction(db: Session, order: Order) -> list[InventoryItem]:
    """Deduct an order's items from master inventory with replay safety:
    always reverse any previous deduction first, then reapply."""
    if order.deduction_applied:
        reverse_order_deduction(db, order, cause="undo",
                                comment=f"replay: re-deducting order {order.external_order_id}")
    touched: list[InventoryItem] = []
    for line in order.items:
        item = None
        if line.inventory_id:
            item = db.get(InventoryItem, line.inventory_id)
        if item is None and line.marketplace_product_id and order.marketplace == "ebay":
            listing = db.execute(select(MarketplaceListing).where(
                MarketplaceListing.ebay_listing_id == line.marketplace_product_id
            )).scalars().first()
            if listing:
                item = listing.item
        if item is None:
            continue
        line.inventory_id = item.id
        applied = inv_svc.apply_delta(
            db, item, -line.quantity, type="deduction", cause="sale",
            comment=f"{order.marketplace} order {order.external_order_id}",
            source="platform")
        line.cogs = inv_svc.consume_fifo(db, item, -applied, order_id=order.id)
        touched.append(item)
    order.deduction_applied = True
    return touched


def reverse_order_deduction(db: Session, order: Order, cause: str = "refund",
                            comment: str = "") -> None:
    """Reverse an order's deduction (refund/return/cancel/replay)."""
    if not order.deduction_applied:
        return
    for line in order.items:
        if not line.inventory_id:
            continue
        item = db.get(InventoryItem, line.inventory_id)
        if item is None:
            continue
        inv_svc.apply_delta(
            db, item, line.quantity, type="addition", cause=cause,
            comment=comment or f"reverse {order.marketplace} order {order.external_order_id}",
            source="platform")
        line.cogs = 0.0
    inv_svc.restore_fifo(db, order.id)
    order.deduction_applied = False


def cross_delist(db: Session, touched: list[InventoryItem],
                 sold_on: str) -> None:
    """Push reduced quantities to the OTHER marketplace in the same sync pass;
    delist there if effective quantity hit zero (6.1)."""
    other = "tcgplayer" if sold_on == "ebay" else "ebay"
    if not _push_allowed(db, other):
        return
    for item in touched:
        listing = next((l for l in item.listings if l.marketplace == other), None)
        if listing is None or not _has_external_id(listing):
            continue
        _push_one(db, item, other, create_if_missing=False)


def poll_orders(db: Session, marketplace: str) -> dict:
    """Pull open orders, upsert locally, deduct new ones, cross-delist."""
    acct = account(db, marketplace)
    if not acct or acct.status == "disconnected":
        return {"error": f"{marketplace} not connected"}
    adapter = ADAPTERS[marketplace]
    try:
        raw_orders = adapter.fetch_orders(db)
    except ListingError as e:
        return {"error": str(e)}
    new = existing = 0
    all_touched: list[InventoryItem] = []
    for ro in raw_orders:
        order = db.execute(select(Order).where(
            Order.marketplace == marketplace,
            Order.external_order_id == ro["external_order_id"])).scalars().first()
        if order is not None:
            existing += 1
            continue
        ordered_at = utcnow()
        if ro.get("ordered_at"):
            try:
                ordered_at = datetime.fromisoformat(
                    str(ro["ordered_at"]).replace("Z", "+00:00"))
            except ValueError:
                pass
        order = Order(
            marketplace=marketplace,
            external_order_id=ro["external_order_id"],
            buyer_name=ro.get("buyer_name", ""),
            ship_to=ro.get("ship_to", {}),
            order_total=ro.get("total", 0.0),
            marketplace_fees=ro.get("fees", 0.0),
            is_direct=ro.get("is_direct", False),
            ordered_at=ordered_at,
        )
        db.add(order)
        db.flush()
        for li in ro.get("items", []):
            inv_item = _find_inventory_by_sku(db, li.get("sku", "")) if li.get("sku") else None
            db.add(OrderItem(
                order_id=order.id,
                inventory_id=inv_item.id if inv_item else None,
                description=li.get("description", ""),
                marketplace_product_id=li.get("marketplace_product_id"),
                quantity=li.get("quantity", 1),
                unit_price=li.get("unit_price", 0.0),
            ))
        db.flush()
        db.refresh(order)
        all_touched.extend(apply_order_deduction(db, order))
        new += 1
    if all_touched:
        cross_delist(db, all_touched, marketplace)
    acct.last_order_poll_at = utcnow()
    db.commit()
    return {"new_orders": new, "already_known": existing}
