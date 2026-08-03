"""Order fulfillment (Section 8): pick lists, packing slips, Shippo labels,
mark-shipped, refunds/cancellations."""
import logging
import math
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import CONDITIONS, state_tax_rate
from ..models import InventoryItem, Order, collector_number_key, utcnow
from . import inventory as inv_svc
from .exporting import internal_sku
from .marketplaces.base import ListingError
from .marketplaces.sync import ADAPTERS, apply_order_deduction, cross_delist, reverse_order_deduction
from .settings import get_setting

log = logging.getLogger(__name__)

SHIPPO_API = "https://api.goshippo.com"

# Fields the pick list can be sorted by (Settings → pick_list_sort). Each maps a
# row to a comparable value; custom/unmatched items are always forced last.
PICK_SORT_FIELDS = ("condition", "name", "set_code", "bin", "collector_number", "printing")
DEFAULT_PICK_SORT = ["condition", "name"]


def _pick_field_value(row: dict, field: str):
    """One sort component for a pick-list row. Condition sorts NM→DMG by its
    canonical order; collector number sorts numerically; everything else is a
    lowercased string. A field's value type is stable across rows, so the
    per-field tuple stays comparable."""
    if field == "condition":
        try:
            return CONDITIONS.index(row.get("condition") or "")
        except ValueError:
            return len(CONDITIONS)  # unknown/blank conditions last
    if field == "collector_number":
        key = collector_number_key(row.get("collector_number")) or ""
        return f"{int(key):08d}" if key.isdigit() else key.lower()
    return str(row.get(field) or "").lower()


def _pick_sort_fields(db: Session) -> list[str]:
    raw = get_setting(db, "pick_list_sort") or DEFAULT_PICK_SORT
    fields = [f for f in raw if f in PICK_SORT_FIELDS]
    return fields or DEFAULT_PICK_SORT


def build_pick_list(db: Session, orders: list[Order]) -> list[dict]:
    """Merged pick list across orders, sorted the way the user organizes stock.

    Bin is resolved at render time from the live inventory record, so a bin
    rename after the sale doesn't orphan the pick list. Identical line items
    across orders are merged with summed quantity. TCGplayer Direct orders
    are flagged (different fulfillment ownership).

    Ordering follows the configurable ``pick_list_sort`` setting (default:
    condition, then A–Z by name). Custom / non-catalog and unmatched lines,
    which don't fit that scheme, always sort to the end.
    """
    merged: dict = {}
    for order in orders:
        for line in order.items:
            item = db.get(InventoryItem, line.inventory_id) if line.inventory_id else None
            if item is not None:
                key = ("inv", item.id)
                card = item.card
                entry = merged.setdefault(key, {
                    "name": card.name if card else (
                        item.custom_sku.product.name if item.custom_sku else line.description),
                    "set_name": card.set_name if card else "",
                    "set_code": card.set_code if card else "",
                    "collector_number": card.collector_number if card else "",
                    "condition": item.condition,
                    "printing": item.printing,
                    "bin": item.bin,  # live value at render time
                    "sku": internal_sku(item),
                    "marketplace_product_id": line.marketplace_product_id or "",
                    "quantity": 0,
                    "orders": [],
                    "is_direct": False,
                    "is_other": card is None,  # custom/non-catalog → sorts last
                })
            else:
                key = ("desc", line.description)
                entry = merged.setdefault(key, {
                    "name": line.description, "set_name": "", "set_code": "",
                    "collector_number": "", "condition": "", "printing": "",
                    "bin": "(unmatched)", "sku": "",
                    "marketplace_product_id": line.marketplace_product_id or "",
                    "quantity": 0, "orders": [], "is_direct": False,
                    "is_other": True,  # unmatched line → sorts last
                })
            entry["quantity"] += line.quantity
            entry["is_direct"] = entry["is_direct"] or order.is_direct
            ref = f"{order.marketplace}:{order.external_order_id}"
            if ref not in entry["orders"]:
                entry["orders"].append(ref)
    rows = list(merged.values())
    fields = _pick_sort_fields(db)
    rows.sort(key=lambda r: (r["is_other"],
                             tuple(_pick_field_value(r, f) for f in fields)))
    return rows


def build_packing_slip(db: Session, order: Order) -> dict:
    return {
        "order_number": order.external_order_id,
        "marketplace": order.marketplace,
        "buyer_name": order.buyer_name,
        "ship_to": order.ship_to,
        "ordered_at": order.ordered_at.isoformat() if order.ordered_at else None,
        "items": [{
            "description": line.description or (
                inv_svc.item_description(db.get(InventoryItem, line.inventory_id))
                if line.inventory_id else ""),
            "quantity": line.quantity,
            "unit_price": line.unit_price,
            "total": round(line.quantity * line.unit_price, 2),
        } for line in order.items],
        "order_total": order.order_total,
    }


# --- Shippo ------------------------------------------------------------------

def _shippo_headers(db: Session) -> dict:
    token = get_setting(db, "shippo_api_token")
    if not token:
        raise ValueError("Shippo API token not configured (Settings)")
    return {"Authorization": f"ShippoToken {token}"}


def buy_shippo_label(db: Session, order: Order, parcel: dict | None = None,
                     address_from: dict | None = None) -> dict:
    """Create a Shippo shipment, pick the cheapest USPS rate, buy the label.

    Only called for orders above the configured value threshold; below it the
    user handles postage manually (Section 8).
    """
    threshold = float(get_setting(db, "label_min_order_value"))
    if order.order_total <= threshold:
        raise ValueError(
            f"order total ${order.order_total:.2f} <= ${threshold:.2f} threshold — "
            "buy postage manually or enter tracking directly")
    headers = _shippo_headers(db)
    parcel = parcel or {"length": "6", "width": "4", "height": "1",
                        "distance_unit": "in", "weight": "3", "mass_unit": "oz"}
    if not address_from:
        address_from = get_setting(db, "ship_from_address") or {}
        if not address_from:
            raise ValueError("ship-from address not configured (Settings)")
    with httpx.Client(timeout=60) as c:
        r = c.post(f"{SHIPPO_API}/shipments/", headers=headers, json={
            "address_from": address_from,
            "address_to": order.ship_to,
            "parcels": [parcel],
            "async": False,
        })
        r.raise_for_status()
        shipment = r.json()
        rates = [rt for rt in shipment.get("rates", []) if rt.get("provider") == "USPS"] \
            or shipment.get("rates", [])
        if not rates:
            raise ValueError("Shippo returned no rates")
        rate = min(rates, key=lambda rt: float(rt["amount"]))
        r = c.post(f"{SHIPPO_API}/transactions/", headers=headers, json={
            "rate": rate["object_id"], "label_file_type": "PDF", "async": False,
        })
        r.raise_for_status()
        tx = r.json()
        if tx.get("status") != "SUCCESS":
            raise ValueError(f"Shippo label purchase failed: {tx.get('messages')}")
    order.tracking_number = tx.get("tracking_number")
    order.carrier = rate.get("provider", "USPS")
    order.label_url = tx.get("label_url")
    order.shipping_cost = float(rate["amount"])
    db.commit()
    return {"label_url": order.label_url, "tracking_number": order.tracking_number,
            "cost": order.shipping_cost, "carrier": order.carrier}


# --- Post-ship -----------------------------------------------------------------

def mark_shipped(db: Session, order: Order, tracking_number: str | None = None,
                 carrier: str | None = None) -> dict:
    """Mark shipped locally + on the originating marketplace. Also supports
    manually-entered tracking for postage purchased outside the app."""
    if tracking_number:
        order.tracking_number = tracking_number
    if carrier:
        order.carrier = carrier
    warnings = []
    if order.marketplace in ADAPTERS and order.tracking_number:
        try:
            ADAPTERS[order.marketplace].mark_shipped(
                db, order.external_order_id, order.tracking_number,
                order.carrier or "USPS")
        except (ListingError, Exception) as e:  # noqa: BLE001
            warnings.append(f"marketplace mark-shipped failed: {e}")
    order.status = "shipped"
    order.shipped_at = utcnow()
    if not order.deduction_applied:
        touched = apply_order_deduction(db, order)
        cross_delist(db, touched, order.marketplace)
    db.commit()
    return {"status": "shipped", "warnings": warnings}


def refundable_total(order: Order) -> float:
    """The most a buyer can be credited back: the item subtotal plus whatever
    they paid us for shipping. Both are revenue, so both have to be refundable
    for a full refund to zero out the sale."""
    return round(order.order_total + (order.shipping_charged or 0.0), 2)


def refund_sale(db: Session, order: Order, *, mode: str = "full",
                amount: float | None = None, returned: bool = True,
                return_shipping: float = 0.0,
                fees_refunded: float | None = None) -> dict:
    """Refund a customer on a sale. Two modes:

    - ``partial`` — refund `amount` to the buyer; inventory and COGS are
      untouched, the sale stays live but with reduced net revenue. Accumulates
      across multiple partial refunds.
    - ``full`` — refund the whole ``refundable_total`` (item subtotal + the
      shipping the buyer paid). If ``returned`` the card comes back: inventory is
      restored (+qty) and its COGS backed out to 0, so the cost follows the card
      and lands on whichever sale actually sticks. If not returned it's a
      write-off: inventory stays deducted and the COGS remains a real loss.
      ``return_shipping`` (what you paid to get the card back) is captured as an
      expense.

    ``fees_refunded`` is what the marketplace credited back in selling fees —
    TCGplayer returns them on a refund, so it defaults to the full fee on a full
    refund and pro-rata on a partial. Pass an explicit value for a marketplace
    that keeps part of the fee.

    Returns ``unlinked_lines``: lines whose COGS was backed out but which have no
    inventory record to restock (migrated/historical sales). Those cards have to
    be re-added by hand before they can be re-listed.
    """
    max_refund = refundable_total(order)
    if mode == "partial":
        amt = round(float(amount or 0), 2)
        already = order.amount_refunded or 0.0
        if amt <= 0:
            raise ValueError("partial refund amount must be positive")
        if already + amt > max_refund + 1e-9:
            raise ValueError("total refunded would exceed the order total — "
                             "use a full refund instead")
        order.amount_refunded = round(already + amt, 2)
        # Fees scale with what the buyer actually kept, so a partial refund gets
        # back a pro-rata slice of them.
        if fees_refunded is None:
            share = (order.amount_refunded / max_refund) if max_refund else 0.0
            order.fees_refunded = round((order.marketplace_fees or 0.0) * share, 2)
        else:
            order.fees_refunded = round(float(fees_refunded), 2)
        if order.status not in ("refunded",):
            order.status = "partially_refunded"
        db.commit()
        return {"status": order.status, "amount_refunded": order.amount_refunded,
                "fees_refunded": order.fees_refunded, "restocked": False,
                "unlinked_lines": []}

    # full refund
    order.amount_refunded = max_refund
    order.fees_refunded = round(
        float(fees_refunded) if fees_refunded is not None
        else (order.marketplace_fees or 0.0), 2)
    if return_shipping:
        order.return_shipping_cost = round((order.return_shipping_cost or 0.0)
                                           + float(return_shipping), 2)
    restocked = False
    unlinked: list[str] = []
    if returned:
        reverse_order_deduction(db, order, cause="refund",
                                comment=f"full refund + return {order.external_order_id}")
        restocked = True
        # Migrated/historical lines carry their COGS on the line with no
        # inventory record behind them, so the reversal above skips them. The
        # card still came back, so its cost must not stay expensed here — back it
        # out and report the line so the user can re-add the stock by hand.
        for line in order.items:
            if line.inventory_id or not line.cogs:
                continue
            unlinked.append(line.description or f"line {line.id}")
            line.cogs = 0.0
    else:
        # write-off: leave inventory deducted; the COGS on each line stays a loss.
        for line in order.items:
            item = db.get(InventoryItem, line.inventory_id) if line.inventory_id else None
            if item is not None:
                inv_svc.log_mutation(db, item, "adjustment", 0, cause="refund",
                                     comment=f"full refund, item NOT returned (write-off) "
                                     f"{order.external_order_id}")
    order.status = "refunded"
    db.commit()
    return {"status": order.status, "amount_refunded": order.amount_refunded,
            "fees_refunded": order.fees_refunded,
            "return_shipping_cost": order.return_shipping_cost,
            "restocked": restocked, "unlinked_lines": unlinked}


def ceil_cent(value: float) -> float:
    """Round up to the nearest cent. Marketplace fees are billed on cent-rounded
    percentages, so a fee computed from unrounded products is systematically a
    hair low."""
    return math.ceil(round(value, 6) * 100) / 100.0


def estimate_marketplace_fee(db: Session, *, subtotal: float,
                             shipping_charged: float = 0.0,
                             tax: float | None = None,
                             state: str | None = None) -> dict:
    """Anticipated TCGplayer fee on an order, before the payout confirms it.

    Commission applies to items plus the shipping the buyer paid; payment
    processing is a flat charge plus a percentage of the *tax-inclusive* total.
    Each percentage component is rounded up to the cent and then summed — the
    rounding happens per component, not once on the total.

    ``tax`` is what the buyer was actually charged, when known. Packing slips
    don't print it, so passing ``state`` instead estimates it from the
    destination (see domain.STATE_TAX_RATES); the returned ``tax_estimated``
    flag says which happened, so a reviewer can tell an exact fee from a close
    one.
    """
    commission_pct = float(get_setting(db, "tcg_commission_pct"))
    processing_pct = float(get_setting(db, "tcg_processing_pct"))
    processing_flat = float(get_setting(db, "tcg_processing_flat"))
    taxable = round(subtotal + shipping_charged, 2)
    estimated = tax is None
    if estimated:
        tax = round(taxable * state_tax_rate(state), 2)
    commission = ceil_cent(commission_pct * taxable)
    processing = ceil_cent(processing_pct * (taxable + tax))
    return {
        "fee": round(commission + processing_flat + processing, 2),
        "commission": commission,
        "processing": round(processing + processing_flat, 2),
        "processing_flat": processing_flat,
        "tax": round(tax, 2),
        "tax_estimated": estimated,
        "tax_rate": state_tax_rate(state) if estimated else None,
        "fee_base": taxable,
    }


def parse_sale_date(value) -> datetime | None:
    """Parse a user-supplied sale date into a UTC datetime. Accepts a plain
    ``YYYY-MM-DD`` (kept at midnight UTC) or a full ISO timestamp. Returns None
    for blank input so callers can leave the existing value untouched."""
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def set_order_costs(db: Session, order: Order, *, shipping_cost: float | None = None,
                    marketplace_fees: float | None = None,
                    shipping_charged: float | None = None,
                    ordered_at=None) -> Order:
    """Record/adjust an order's own costs after the fact — e.g. postage bought
    outside the app, fees not captured by the sync, or shipping charged to the
    buyer (revenue). ``ordered_at`` back-dates the sale (e.g. recorded a day
    late) and feeds Reports by sale date."""
    if shipping_cost is not None:
        order.shipping_cost = round(float(shipping_cost), 2)
    if marketplace_fees is not None:
        order.marketplace_fees = round(float(marketplace_fees), 2)
    if shipping_charged is not None:
        order.shipping_charged = round(float(shipping_charged), 2)
    when = parse_sale_date(ordered_at)
    if when is not None:
        order.ordered_at = when
    db.commit()
    return order


def cancel_order(db: Session, order: Order) -> None:
    """Pre-shipment cancellation: restore quantity; the listing is NOT
    auto-re-pushed — the next sync picks up the quantity change."""
    reverse_order_deduction(db, order, cause="cancellation",
                            comment=f"cancellation {order.external_order_id}")
    order.status = "cancelled"
    db.commit()


def delete_order(db: Session, order: Order) -> None:
    """Delete an order record WITHOUT touching inventory.

    Unlike ``cancel_order``/``refund_sale`` (returned), this does NOT restock the
    cards or reverse their COGS — the deduction stays applied. Any FIFO
    consumption linked to the order is detached (order_id → NULL) so the units
    remain consumed and the foreign key to the now-removed order is cleared.
    Order line items are removed by the relationship cascade. Because P&L only
    counts existing orders, deleting also drops this sale's revenue and COGS
    together from Reports.
    """
    from sqlalchemy import update

    from ..models import FifoConsumption
    db.execute(update(FifoConsumption)
               .where(FifoConsumption.order_id == order.id)
               .values(order_id=None))
    db.delete(order)
    db.commit()


def manual_sale_platforms(db: Session) -> list[str]:
    """Platform names for the manual-sale dropdown: the fixed defaults plus any
    platform already recorded on a prior manual order."""
    from ..domain import SALE_PLATFORMS
    used = {o.buyer_name for o in db.execute(
        select(Order).where(Order.marketplace == "manual")).scalars() if o.buyer_name}
    return sorted(set(SALE_PLATFORMS) | used)


def create_manual_order(db: Session, *, buyer_name: str, items: list[dict],
                        total: float | None = None, shipping_cost: float = 0.0,
                        marketplace_fees: float = 0.0,
                        shipping_charged: float = 0.0, ordered_at=None) -> Order:
    """Record a manual/offline sale (non-marketplace). ``ordered_at`` sets the
    sale date (defaults to now) so a late-recorded sale lands on the right day."""
    from ..models import OrderItem
    when = parse_sale_date(ordered_at)
    order = Order(marketplace="manual",
                  # microsecond precision so two manual sales in the same second
                  # (e.g. several bulk packs in a row) don't collide on the
                  # (marketplace, external_order_id) unique constraint.
                  external_order_id=f"manual-{utcnow().strftime('%Y%m%d%H%M%S%f')}",
                  buyer_name=buyer_name, order_total=total or 0.0,
                  shipping_cost=round(float(shipping_cost or 0), 2),
                  marketplace_fees=round(float(marketplace_fees or 0), 2),
                  shipping_charged=round(float(shipping_charged or 0), 2),
                  ordered_at=when or utcnow())
    db.add(order)
    db.flush()
    computed_total = 0.0
    for it in items:
        inv_id = it.get("inventory_id")
        desc = it.get("description", "")
        if not desc and inv_id:  # build a readable label from the picked record
            inv_item = db.get(InventoryItem, inv_id)
            if inv_item is not None:
                desc = inv_svc.item_description(inv_item)
        db.add(OrderItem(order_id=order.id, inventory_id=inv_id,
                         description=desc,
                         quantity=it.get("quantity", 1),
                         unit_price=it.get("unit_price", 0.0)))
        computed_total += it.get("quantity", 1) * it.get("unit_price", 0.0)
    if total is None:
        order.order_total = round(computed_total, 2)
    db.flush()
    db.refresh(order)
    apply_order_deduction(db, order)
    db.commit()
    return order
