"""P&L and reporting (Section 9): realized P&L with FIFO COGS, inventory
aging, valuation, location summary."""
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AcquisitionLog, CatalogCard, InventoryItem, Order, PriceData
from . import inventory as inv_svc

AGE_BUCKETS = [(0, 30), (31, 60), (61, 90), (91, None)]


def realized_pnl(db: Session, *, group_by: str = "month",
                 date_from: datetime | None = None,
                 date_to: datetime | None = None) -> list[dict]:
    """Per-sale P&L, aggregated by day/week/month/game/set:

        profit = revenue − refunds − COGS − shipping − fees

    Fulfilled orders count even after a refund: a partial refund reduces net
    revenue; a full refund with the item returned nets to ~0 (revenue and COGS
    both back out) minus any return shipping; a full refund with the item NOT
    returned leaves the COGS as a real loss.

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
        order_fees = order.marketplace_fees

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
