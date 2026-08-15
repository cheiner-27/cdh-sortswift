"""Shared JSON serializers for ORM objects."""
from ..models import (
    CatalogCard, ImportBatch, InventoryItem, Lot, MarketplaceListing, Order,
    ScanQueueItem, SlipBatch, SlipOrder, StagingItem,
)


def card_dict(c: CatalogCard | None) -> dict | None:
    if c is None:
        return None
    return {
        "id": c.id, "game": c.game, "external_id": c.external_id,
        "tcgplayer_product_id": c.tcgplayer_product_id,
        "set_code": c.set_code, "set_name": c.set_name,
        "collector_number": c.collector_number, "name": c.name,
        "rarity": c.rarity, "finishes": c.finishes, "image_url": c.image_url,
        "back_image_url": c.back_image_url, "is_double_faced": c.is_double_faced,
    }


def listing_dict(l: MarketplaceListing) -> dict:
    return {
        "id": l.id, "marketplace": l.marketplace, "status": l.status,
        "listed_price": l.listed_price, "listed_quantity": l.listed_quantity,
        "listing_cap": l.listing_cap, "reserve_quantity": l.reserve_quantity,
        "ebay_sku": l.ebay_sku, "ebay_offer_id": l.ebay_offer_id,
        "ebay_listing_id": l.ebay_listing_id, "tcg_sku_id": l.tcg_sku_id,
        "error_code": l.error_code, "error_message": l.error_message,
        "dirty": l.dirty,
        "last_synced_at": l.last_synced_at.isoformat() if l.last_synced_at else None,
    }


def inventory_dict(it: InventoryItem, *, age_days: int | None = None,
                   fifo_cost: float | None = None) -> dict:
    return {
        "id": it.id,
        "card": card_dict(it.card),
        "custom_sku_id": it.custom_sku_id,
        "custom_name": (it.custom_sku.product.name
                        if it.custom_sku and it.custom_sku.product else None),
        "condition": it.condition, "printing": it.printing,
        "language": it.language, "bin": it.bin, "quantity": it.quantity,
        "comment": it.comment, "price_override": it.price_override,
        "price_floor": it.price_floor, "current_price": it.current_price,
        "scan_image_path": it.scan_image_path,
        "deleted": it.deleted,
        "age_days": age_days, "fifo_cost": fifo_cost,
        "listings": [listing_dict(l) for l in it.listings],
        "created_at": it.created_at.isoformat() if it.created_at else None,
    }


def scan_item_dict(s: ScanQueueItem, *, market_value: float | None = None) -> dict:
    return {
        "id": s.id, "pull_id": s.pull_id, "seq": s.seq,
        "image_path": s.image_path, "back_image_path": s.back_image_path,
        "file_name": s.file_name, "status": s.status,
        "low_resolution": s.low_resolution, "method": s.method,
        "confidence": s.confidence, "candidates": s.candidates,
        "card": card_dict(s.card), "card_id": s.card_id,
        "condition": s.condition, "printing": s.printing,
        "language": s.language, "bin": s.bin, "quantity": s.quantity,
        "cost": s.cost, "market_value": market_value,
        "source_bulk_id": s.source_bulk_id,
    }


def staging_dict(s: StagingItem, *, market_value: float | None = None) -> dict:
    return {
        "id": s.id, "source": s.source, "card": card_dict(s.card),
        "custom_sku_id": s.custom_sku_id,
        "custom_name": (s.custom_sku.product.name
                        if s.custom_sku and s.custom_sku.product else None),
        "condition": s.condition, "printing": s.printing,
        "language": s.language, "bin": s.bin, "quantity": s.quantity,
        "cost": s.cost, "price": s.price, "comment": s.comment,
        "acquired_at": s.acquired_at.isoformat() if s.acquired_at else None,
        "scan_image_path": s.scan_image_path,
        "import_batch_id": s.import_batch_id,
        "source_bulk_id": s.source_bulk_id,
        "market_value": market_value,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


def order_dict(o: Order) -> dict:
    return {
        "id": o.id, "marketplace": o.marketplace,
        "external_order_id": o.external_order_id, "buyer_name": o.buyer_name,
        "ship_to": o.ship_to, "status": o.status, "is_direct": o.is_direct,
        "order_total": o.order_total, "marketplace_fees": o.marketplace_fees,
        "shipping_cost": o.shipping_cost, "shipping_charged": o.shipping_charged,
        "amount_refunded": o.amount_refunded, "fees_refunded": o.fees_refunded,
        "return_shipping_cost": o.return_shipping_cost,
        "tracking_number": o.tracking_number,
        "carrier": o.carrier, "label_url": o.label_url,
        "deduction_applied": o.deduction_applied,
        "ordered_at": o.ordered_at.isoformat() if o.ordered_at else None,
        "shipped_at": o.shipped_at.isoformat() if o.shipped_at else None,
        "items": [{
            "id": li.id, "inventory_id": li.inventory_id,
            "description": li.description,
            "marketplace_product_id": li.marketplace_product_id,
            "quantity": li.quantity, "unit_price": li.unit_price,
            "cogs": li.cogs,
        } for li in o.items],
    }


def slip_order_dict(s: SlipOrder) -> dict:
    return {
        "id": s.id, "batch_id": s.batch_id, "order_number": s.order_number,
        "buyer_name": s.buyer_name,
        "ordered_at": s.ordered_at.isoformat() if s.ordered_at else None,
        "ship_city": s.ship_city, "ship_state": s.ship_state,
        "ship_postal_code": s.ship_postal_code,
        "item_total": s.item_total, "quantity_total": s.quantity_total,
        "reconciled": s.reconciled, "shipping_charged": s.shipping_charged,
        "shipping_cost": s.shipping_cost,
        "tax": s.tax, "estimated_fee": s.estimated_fee,
        "fee_overridden": s.fee_overridden,
        "fee_detail": s.fee_detail, "lines": s.lines,
        "page_count": s.page_count, "status": s.status, "error": s.error,
        "warning": s.warning, "order_id": s.order_id,
    }


def slip_batch_dict(b: SlipBatch, with_orders: bool = False) -> dict:
    counts: dict[str, int] = {}
    for s in b.orders:
        counts[s.status] = counts.get(s.status, 0) + 1
    d = {
        "id": b.id, "filename": b.filename, "marketplace": b.marketplace,
        "status": b.status, "order_count": len(b.orders), "counts": counts,
        "created_at": b.created_at.isoformat() if b.created_at else None,
    }
    if with_orders:
        d["orders"] = [slip_order_dict(s) for s in b.orders]
    return d


def import_batch_dict(b: ImportBatch, with_rows: bool = False) -> dict:
    d = {
        "id": b.id, "filename": b.filename, "mode": b.mode, "status": b.status,
        "row_count": b.row_count, "quantity_total": b.quantity_total,
        "error_count": b.error_count,
        "created_at": b.created_at.isoformat() if b.created_at else None,
    }
    if with_rows:
        d["rows"] = [{
            "id": r.id, "raw": r.raw, "mapped": r.mapped, "status": r.status,
            "error": r.error, "candidates": r.candidates,
            "quantity_applied": r.quantity_applied,
        } for r in b.rows]
    return d


def lot_dict(l: Lot) -> dict:
    return {
        "id": l.id, "template_id": l.template_id, "name": l.name,
        "status": l.status, "price": l.price, "total_value": l.total_value,
        "marketplace": l.marketplace,
        "external_listing_id": l.external_listing_id,
        "created_at": l.created_at.isoformat() if l.created_at else None,
        "items": [{
            "id": li.id, "inventory_id": li.inventory_id,
            "quantity": li.quantity, "unit_value": li.unit_value,
            "name": li.item.card.name if li.item and li.item.card else "",
            "set_code": li.item.card.set_code if li.item and li.item.card else "",
        } for li in l.items],
    }
