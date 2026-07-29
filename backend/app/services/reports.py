"""P&L and reporting (Section 9): realized P&L with FIFO COGS, inventory
aging, valuation, location summary."""
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    AcquisitionLog, CatalogCard, FifoConsumption, InventoryItem, Order, PriceData,
)
from . import inventory as inv_svc

AGE_BUCKETS = [(0, 30), (31, 60), (61, 90), (91, None)]


def realized_pnl(db: Session, *, group_by: str = "month",
                 date_from: datetime | None = None,
                 date_to: datetime | None = None) -> list[dict]:
    """Per-sale P&L, aggregated by day/week/month/game/set:

        profit = revenue − refunds − COGS − shipping − fees

    Fulfilled orders count even after a refund: a partial refund reduces net
    revenue; a full refund with the item returned nets to the shipping you ate
    (revenue, COGS and refunded fees all back out); a full refund with the item
    NOT returned leaves the COGS as a real loss.

    When grouping by game/set an order can span several groups: each line's COGS
    and units land in its own group, and the order-level costs (fees, shipping,
    refunds) are split pro-rata by each group's share of the item subtotal.
    """
    q = select(Order).where(
        Order.status.in_(["shipped", "partially_refunded", "refunded"]))
    orders = db.execute(q).scalars().all()
    groups: dict[str, dict] = defaultdict(lambda: {
        "revenue": 0.0, "refunds": 0.0, "cogs": 0.0, "shipping": 0.0, "fees": 0.0,
        "orders": 0, "units": 0})
    for order in orders:
        when = order.shipped_at or order.ordered_at
        if when and when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if date_from and when and when < date_from:
            continue
        if date_to and when and when > date_to:
            continue
        # Order-level money to attribute to this order's group(s).
        order_revenue = order.order_total + (order.shipping_charged or 0.0)
        order_refunds = order.amount_refunded or 0.0
        order_shipping = order.shipping_cost + (order.return_shipping_cost or 0.0)
        # Only the fees we actually ate are a cost — TCGplayer credits selling
        # fees back on a refund, so a refunded sale shouldn't carry them.
        order_fees = order.marketplace_fees - (order.fees_refunded or 0.0)

        if group_by in ("game", "set"):
            # One order can span several games/sets. Attribute each line's COGS
            # and units to its own group, then split the order-level costs
            # pro-rata by each group's share of the item subtotal — marketplace
            # fees scale with price, so a price-weighted split mirrors how they
            # were charged (an even split would overcharge the cheaper cards).
            per_group: dict[str, dict] = defaultdict(
                lambda: {"weight": 0.0, "cogs": 0.0, "units": 0})
            for line in order.items:
                item = db.get(InventoryItem, line.inventory_id) if line.inventory_id else None
                # Historical/migrated lines carry no inventory record; fall back to
                # the catalog card stamped on the line for game/set attribution.
                card = item.card if item else (
                    db.get(CatalogCard, line.catalog_card_id) if line.catalog_card_id else None)
                if group_by == "game":
                    key = card.game if card else "custom/unknown"
                else:
                    key = f"{card.game}:{card.set_code}" if card else "custom/unknown"
                pg = per_group[key]
                pg["weight"] += line.unit_price * line.quantity
                pg["cogs"] += line.cogs
                pg["units"] += line.quantity
            if not per_group:  # order with no line items: keep its money visible
                per_group["unknown"] = {"weight": 0.0, "cogs": 0.0, "units": 0}
            total_weight = sum(pg["weight"] for pg in per_group.values())
            for key, pg in per_group.items():
                # even split when nothing in the order carries a price (all 0)
                share = pg["weight"] / total_weight if total_weight else 1.0 / len(per_group)
                g = groups[key]
                g["revenue"] += order_revenue * share
                g["refunds"] += order_refunds * share
                g["shipping"] += order_shipping * share
                g["fees"] += order_fees * share
                g["cogs"] += pg["cogs"]
                g["units"] += pg["units"]
                g["orders"] += 1  # an order is counted in every group it touches
        else:
            if group_by == "day":
                key = when.strftime("%Y-%m-%d") if when else "unknown"
            elif group_by == "week":
                key = f"{when.isocalendar().year}-W{when.isocalendar().week:02d}" if when else "unknown"
            else:  # month
                key = when.strftime("%Y-%m") if when else "unknown"
            g = groups[key]
            g["revenue"] += order_revenue
            g["refunds"] += order_refunds
            g["shipping"] += order_shipping
            g["fees"] += order_fees
            g["cogs"] += sum(line.cogs for line in order.items)
            g["units"] += sum(line.quantity for line in order.items)
            g["orders"] += 1
    out = []
    for key in sorted(groups):
        g = groups[key]
        out.append({
            "group": key, **{k: round(v, 2) for k, v in g.items() if isinstance(v, float)},
            "orders": g["orders"], "units": g["units"],
            "profit": round(g["revenue"] - g["refunds"] - g["cogs"]
                            - g["shipping"] - g["fees"], 2),
        })
    return out


def aging_report(db: Session) -> dict:
    """Cards by age bucket + total value at cost and at market."""
    items = db.execute(select(InventoryItem).where(
        InventoryItem.deleted == False,  # noqa: E712
        InventoryItem.quantity > 0)).scalars().all()
    buckets = {f"{lo}-{hi if hi else '+'}d": {"units": 0, "cost_value": 0.0, "market_value": 0.0}
               for lo, hi in AGE_BUCKETS}
    unknown = {"units": 0, "cost_value": 0.0, "market_value": 0.0}
    total_cost = total_market = 0.0
    for item in items:
        age = inv_svc.inventory_age_days(db, item)
        cost = inv_svc.fifo_unit_cost(db, item) or 0.0
        market = item.current_price or item.price_override or 0.0
        total_cost += cost * item.quantity
        total_market += market * item.quantity
        target = unknown
        if age is not None:
            for lo, hi in AGE_BUCKETS:
                if age >= lo and (hi is None or age <= hi):
                    target = buckets[f"{lo}-{hi if hi else '+'}d"]
                    break
        target["units"] += item.quantity
        target["cost_value"] += cost * item.quantity
        target["market_value"] += market * item.quantity
    for b in list(buckets.values()) + [unknown]:
        b["cost_value"] = round(b["cost_value"], 2)
        b["market_value"] = round(b["market_value"], 2)
    return {"buckets": buckets, "unknown_age": unknown,
            "total_at_cost": round(total_cost, 2),
            "total_at_market": round(total_market, 2)}


def location_summary(db: Session) -> list[dict]:
    """Inventory grouped by bin, with an explicit 'unassigned' bucket —
    for spotting orphaned/mistyped bins (no automatic fuzzy-merge)."""
    items = db.execute(select(InventoryItem).where(
        InventoryItem.deleted == False)).scalars().all()  # noqa: E712
    bins: dict[str, dict] = defaultdict(lambda: {"records": 0, "units": 0, "value": 0.0})
    for item in items:
        key = item.bin or "(unassigned)"
        b = bins[key]
        b["records"] += 1
        b["units"] += item.quantity
        b["value"] += (item.current_price or 0.0) * item.quantity
    return [{"bin": k, "records": v["records"], "units": v["units"],
             "value": round(v["value"], 2)} for k, v in sorted(bins.items())]


def _net_proceeds_by_batch(db: Session) -> dict[int, dict]:
    """``{acquisition_id: {"units", "net"}}`` — what the units drawn off each
    FIFO batch actually brought in.

    fifo_consumption says which batch a sale ate and in what quantity; the order
    line says what that sale fetched. Joining them is what lets a purchase report
    revenue at all. Money follows the same convention as realized_pnl — proceeds
    are net of refunds, shipping and the fees we actually ate — so the two
    screens can't disagree about what a sale was worth.

    Order-level money is split across lines pro-rata by line value (fees scale
    with price, so an even split would overcharge cheap cards), then down to
    units. Lines with no inventory record fall back to matching on catalog card,
    which is how migrated sales attribute.
    """
    consumptions = db.execute(select(FifoConsumption)).scalars().all()
    if not consumptions:
        return {}
    order_ids = {c.order_id for c in consumptions if c.order_id}
    orders = db.execute(select(Order).where(Order.id.in_(order_ids))).scalars().all() \
        if order_ids else []

    # per order: pool key (and card fallback) -> units sold + net proceeds
    by_order: dict[int, dict] = {}
    for order in orders:
        gross = order.order_total + (order.shipping_charged or 0.0)
        net = (gross - (order.amount_refunded or 0.0)
               - (order.shipping_cost + (order.return_shipping_cost or 0.0))
               - (order.marketplace_fees - (order.fees_refunded or 0.0)))
        lines = list(order.items)
        total_value = sum(l.unit_price * l.quantity for l in lines)
        pools: dict[tuple, dict] = defaultdict(lambda: {"units": 0, "net": 0.0})
        cards: dict[int, dict] = defaultdict(lambda: {"units": 0, "net": 0.0})
        for line in lines:
            share = ((line.unit_price * line.quantity) / total_value if total_value
                     else (1.0 / len(lines) if lines else 0.0))
            item = db.get(InventoryItem, line.inventory_id) if line.inventory_id else None
            bucket = pools[inv_svc.pool_key(item)] if item else None
            if bucket is not None:
                bucket["units"] += line.quantity
                bucket["net"] += net * share
            card_id = line.catalog_card_id or (item.catalog_card_id if item else None)
            if card_id:
                cards[card_id]["units"] += line.quantity
                cards[card_id]["net"] += net * share
        by_order[order.id] = {"pools": pools, "cards": cards}

    out: dict[int, dict] = defaultdict(lambda: {"units": 0, "net": 0.0})
    for c in consumptions:
        entry = out[c.acquisition_id]
        entry["units"] += c.quantity
        order = by_order.get(c.order_id)
        if not order:
            continue  # consumed without an order (manual deduction): no proceeds
        batch = db.get(AcquisitionLog, c.acquisition_id)
        if batch is None:
            continue
        bucket = order["pools"].get(inv_svc.pool_key(batch))
        if bucket is None or not bucket["units"]:
            bucket = order["cards"].get(batch.catalog_card_id)
        if bucket and bucket["units"]:
            entry["net"] += bucket["net"] / bucket["units"] * c.quantity
    return out


def purchase_lots(db: Session) -> list[dict]:
    """Purchases reconstructed from the FIFO acquisition batches.

    There is no first-class Purchase record, but the batches carry everything a
    purchase is: an acquisition date, a per-unit cost, and the units bought. One
    intake writes all its batches at the same timestamp, so ``(date, unit_cost)``
    is the purchase — or one cost tier of it, where a lot was priced in bands.

    This is the reconciliation the Inventory screen structurally cannot do: its
    money is quantity-weighted, so a card that has sold contributes nothing,
    while ``units``/``paid`` here are what you *bought* and stay put as stock
    sells through. Compare ``paid`` against the invoice to confirm the whole
    purchase made it in.

    ``sold`` counts units consumed by sales; ``other_out`` is anything else that
    drew a batch down (supplier returns, an undone import), so
    ``units == left + sold + other_out`` always holds.

    The point of the whole thing is ``projected``: what the purchase makes if
    what's left sells at its asking price.

        projected = revenue + ask − paid

    ``revenue`` is realised, net of fees/shipping/refunds, traced back through
    the FIFO consumption records to the units of *this* purchase that sold.
    ``ask`` is what the unsold remainder is priced at, allocated to this
    purchase's own units: pools are shared, so two purchases of the same card
    would otherwise both claim the same on-hand stock and each report the full
    asking value. It is therefore a share of the pool's asking value, and will
    read slightly under what the Inventory drill-through totals for whole rows.

    ``ask`` is gross — marketplace fees come off when it actually sells. Pass the
    realised fee rate (see fee_rate) through it for a net view.
    """
    batches = db.execute(select(AcquisitionLog)).scalars().all()
    consumed: dict[int, int] = defaultdict(int)
    cogs_sold: dict[int, float] = defaultdict(float)
    for row in db.execute(select(FifoConsumption)).scalars():
        consumed[row.acquisition_id] += row.quantity
        cogs_sold[row.acquisition_id] += row.quantity * row.unit_cost
    proceeds = _net_proceeds_by_batch(db)

    rows_by_pool: dict[tuple, list[InventoryItem]] = defaultdict(list)
    for item in db.execute(select(InventoryItem).where(
            InventoryItem.deleted == False)).scalars():  # noqa: E712
        rows_by_pool[inv_svc.pool_key(item)].append(item)

    # Asking price per on-hand unit in each pool, so a purchase can be credited
    # with its own share of stock it part-owns rather than the pool's whole value.
    pool_ask: dict[tuple, dict] = {}
    for pool, rows in rows_by_pool.items():
        units = sum(i.quantity for i in rows)
        value = sum(((i.price_override if i.price_override is not None
                      else i.current_price) or 0.0) * i.quantity for i in rows)
        pool_ask[pool] = {"units": units, "value": value,
                          "per_unit": (value / units) if units else 0.0}

    lots: dict[tuple, dict] = {}
    for b in batches:
        key = (b.acquired_at.date().isoformat(), b.unit_cost)
        lot = lots.setdefault(key, {
            "date": key[0], "unit_cost": b.unit_cost, "batches": 0, "units": 0,
            "paid": 0.0, "left": 0, "sold": 0, "cogs_sold": 0.0, "revenue": 0.0,
            "cards": set(), "pools": set(), "left_by_pool": defaultdict(int),
        })
        lot["batches"] += 1
        lot["units"] += b.quantity
        lot["paid"] += b.quantity * b.unit_cost
        lot["left"] += b.quantity_remaining
        lot["sold"] += consumed.get(b.id, 0)
        lot["cogs_sold"] += cogs_sold.get(b.id, 0.0)
        lot["revenue"] += proceeds.get(b.id, {}).get("net", 0.0)
        lot["cards"].add((b.catalog_card_id, b.custom_sku_id))
        lot["pools"].add(inv_svc.pool_key(b))
        lot["left_by_pool"][inv_svc.pool_key(b)] += b.quantity_remaining

    out = []
    for lot in lots.values():
        on_hand = [i for pool in lot["pools"] for i in rows_by_pool.get(pool, [])]
        # Claim only as many units as this purchase still has in each pool, and
        # never more stock than the pool actually holds.
        ask = sum(pool_ask[pool]["per_unit"] * min(units, pool_ask[pool]["units"])
                  for pool, units in lot["left_by_pool"].items() if pool in pool_ask)
        # Unsold units carrying no price contribute $0 to ask, which drags the
        # projection to a flat loss. That is arithmetically right and looks
        # broken, so say how many units it is rather than leaving it implied.
        unpriced = sum(units for pool, units in lot["left_by_pool"].items()
                       if not pool_ask.get(pool, {}).get("per_unit"))
        revenue = round(lot["revenue"], 2)
        paid = round(lot["paid"], 2)
        out.append({
            "date": lot["date"], "unit_cost": lot["unit_cost"],
            "cards": len(lot["cards"]), "batches": lot["batches"],
            "units": lot["units"], "paid": paid,
            "sold": lot["sold"], "left": lot["left"],
            "other_out": lot["units"] - lot["left"] - lot["sold"],
            "rows": len(on_hand),
            "units_on_hand": sum(i.quantity for i in on_hand),
            "ask": round(ask, 2),
            "unpriced_units": unpriced,
            "revenue": revenue,
            "cogs_sold": round(lot["cogs_sold"], 2),
            "profit_realized": round(revenue - lot["cogs_sold"], 2),
            "projected": round(revenue + ask - lot["paid"], 2),
            "roi": round((revenue + ask - lot["paid"]) / lot["paid"] * 100, 1)
            if lot["paid"] else None,
        })
    out.sort(key=lambda l: (l["date"], l["paid"]), reverse=True)
    return out


def fee_rate(db: Session) -> float:
    """Realised marketplace fee rate — fees actually eaten over gross revenue.

    Asking prices are gross, so this is what to discount a projection by to see
    it after the marketplace takes its cut. Measured from your own orders rather
    than assumed, and 0.0 until there are sales to measure.
    """
    orders = db.execute(select(Order)).scalars().all()
    gross = sum(o.order_total + (o.shipping_charged or 0.0) for o in orders)
    fees = sum(o.marketplace_fees - (o.fees_refunded or 0.0) for o in orders)
    return round(fees / gross, 4) if gross else 0.0
