"""Purchases: the per-lot reconciliation rebuilt from FIFO acquisition batches,
and the pool-based drill-through into inventory that backs it."""
from datetime import datetime, timedelta, timezone

from app.routers import inventory as inventory_router
from app.routers import purchases as purchases_router
from app.services import inventory as inv
from app.services import reports


def _lot(db, card, *, day, unit_cost, condition, qty):
    item = inv.find_or_create_item(db, catalog_card_id=card.id, condition=condition)
    inv.add_stock(db, item, qty, unit_cost, acquired_at=day)
    return item


def test_purchase_lot_totals_survive_the_stock_selling_through(db, card):
    """Units/paid describe the purchase, so they hold steady as stock sells —
    which is the whole reason this exists next to the on-hand Inventory totals."""
    day = datetime(2026, 6, 26, tzinfo=timezone.utc)
    a = _lot(db, card, day=day, unit_cost=7.643, condition="NM", qty=4)
    b = _lot(db, card, day=day, unit_cost=7.643, condition="LP", qty=2)
    # A second, unrelated purchase on another date must stay its own lot.
    _lot(db, card, day=day - timedelta(days=7), unit_cost=1.00, condition="MP", qty=5)
    db.commit()

    inv.consume_fifo(db, a, 3)
    inv.apply_delta(db, a, -3)
    db.commit()

    lots = {(l["date"], l["unit_cost"]): l for l in reports.purchase_lots(db)}
    lot = lots[("2026-06-26", 7.643)]
    assert lot["units"] == 6                      # bought, not on hand
    assert lot["paid"] == round(6 * 7.643, 2)     # reconcile this against the invoice
    assert lot["sold"] == 3
    assert lot["left"] == 3
    assert lot["other_out"] == 0
    assert lot["units"] == lot["left"] + lot["sold"] + lot["other_out"]
    assert lot["cards"] == 1 and lot["batches"] == 2
    assert lots[("2026-06-19", 1.00)]["units"] == 5

    # Supplier returns leave with no sale behind them, so they land in other_out
    # rather than silently unbalancing the row.
    inv.return_to_supplier(db, b, 1)
    db.commit()
    lot = {(l["date"], l["unit_cost"]): l for l in reports.purchase_lots(db)}[
        ("2026-06-26", 7.643)]
    assert (lot["sold"], lot["other_out"], lot["left"]) == (3, 1, 2)
    assert lot["units"] == lot["left"] + lot["sold"] + lot["other_out"]


def test_lot_drill_through_catches_what_a_cost_match_misses(db, card):
    """A card also owned from an earlier, cheaper buy reports that older batch's
    cost, so a cost filter drops it. The pool it sits in is still part of the
    purchase, and the drill-through goes by pool."""
    old_day = datetime(2025, 1, 1, tzinfo=timezone.utc)
    day = datetime(2026, 6, 26, tzinfo=timezone.utc)
    overlap = inv.find_or_create_item(db, catalog_card_id=card.id, condition="NM")
    inv.add_stock(db, overlap, 1, 0.10, acquired_at=old_day)   # earlier, cheaper
    inv.add_stock(db, overlap, 2, 7.643, acquired_at=day)      # same purchase
    fresh = _lot(db, card, day=day, unit_cost=7.643, condition="LP", qty=1)
    db.commit()

    def ids(**params):
        return {i.id for i in inventory_router.filter_items(
            db, {"in_stock_only": False, **params})}

    assert ids(cost_min=7.643, cost_max=7.643) == {fresh.id}          # leaks
    assert ids(lot={"date": "2026-06-26", "unit_cost": 7.643}) == {overlap.id, fresh.id}
    # Without a cost tier, the whole day's intake comes back.
    assert ids(lot={"date": "2026-06-26"}) == {overlap.id, fresh.id}
    assert ids(lot={"date": "2025-01-01"}) == {overlap.id}
    assert ids(lot={"date": "2020-01-01"}) == set()


def test_lot_ask_covers_only_the_lots_own_units(db, card):
    """Pools are shared. Two purchases of the same card must not each claim the
    whole pool's asking value — that double-counted $94 across two lots on the
    real data."""
    early = datetime(2025, 1, 1, tzinfo=timezone.utc)
    late = datetime(2026, 6, 26, tzinfo=timezone.utc)
    item = inv.find_or_create_item(db, catalog_card_id=card.id, condition="NM")
    inv.add_stock(db, item, 1, 2.00, acquired_at=early)
    inv.add_stock(db, item, 3, 8.00, acquired_at=late)
    item.price_override = 10.00       # 4 units on hand at $10 = $40 in the pool
    db.commit()

    lots = {(l["date"], l["unit_cost"]): l for l in reports.purchase_lots(db)}
    assert lots[("2025-01-01", 2.00)]["ask"] == 10.00   # its 1 unit
    assert lots[("2026-06-26", 8.00)]["ask"] == 30.00   # its 3
    assert sum(l["ask"] for l in lots.values()) == 40.00  # the pool, once

    # The drill-through shows whole rows, so it reads higher than either lot —
    # documented, and the reason ask is a share rather than the row total.
    drill = inventory_router.search(
        {"in_stock_only": False, "lot": {"date": "2026-06-26", "unit_cost": 8.00}}, db)
    assert drill["totals"]["listed"] == 40.00


def test_projected_profit_traces_revenue_back_to_the_lot(db, card):
    """The number the screen exists for: realised revenue on the units that sold
    plus the remainder at ask, less what the purchase cost."""
    from app.models import Order, OrderItem
    day = datetime(2026, 6, 26, tzinfo=timezone.utc)
    item = _lot(db, card, day=day, unit_cost=4.00, condition="NM", qty=5)
    item.price_override = 20.00
    db.commit()

    order = Order(marketplace="tcgplayer", external_order_id="o-1", status="shipped",
                  order_total=60.00, marketplace_fees=6.00, shipping_cost=4.00)
    db.add(order)
    db.flush()
    db.add(OrderItem(order_id=order.id, inventory_id=item.id, quantity=3,
                     unit_price=20.00, cogs=12.00))
    inv.consume_fifo(db, item, 3, order_id=order.id)
    inv.apply_delta(db, item, -3)
    db.commit()

    lot = next(l for l in reports.purchase_lots(db) if l["unit_cost"] == 4.00)
    assert lot["paid"] == 20.00                  # 5 units @ $4
    assert lot["sold"] == 3 and lot["left"] == 2
    assert lot["revenue"] == 50.00               # $60 − $6 fees − $4 shipping
    assert lot["cogs_sold"] == 12.00             # 3 @ $4
    assert lot["profit_realized"] == 38.00
    assert lot["ask"] == 40.00                   # 2 unsold @ $20
    assert lot["projected"] == 70.00             # 50 + 40 − 20
    assert lot["roi"] == 350.0

    payload = purchases_router.lots(db)
    assert payload["totals"]["projected"] == 70.00
    assert payload["fee_rate"] == round(6.00 / 60.00, 4)


def test_purge_deleted_removes_rows_and_their_cost_basis(db, card):
    """Soft delete leaves quantity and FIFO batches behind, so deleted stock kept
    counting as unsold. Purging has to take the batches with it."""
    day = datetime(2026, 6, 26, tzinfo=timezone.utc)
    doomed = _lot(db, card, day=day, unit_cost=1.00, condition="NM", qty=10)
    keep = _lot(db, card, day=day, unit_cost=2.00, condition="LP", qty=1)
    doomed.deleted = True
    db.commit()

    before = next(l for l in reports.purchase_lots(db) if l["unit_cost"] == 1.00)
    assert before["left"] == 10  # counted as unsold stock while only soft-deleted

    preview = inv.purge_deleted(db, preview=True)
    assert preview == {"items": 1, "item_ids": [doomed.id], "units": 10,
                       "listings": 0, "log_entries_detached": 1, "batches": 1,
                       "batch_units_remaining": 10, "batches_kept": 0,
                       "preview": True}
    assert db.get(type(doomed), doomed.id) is not None  # preview touches nothing

    inv.purge_deleted(db)
    costs = [l["unit_cost"] for l in reports.purchase_lots(db)]
    assert costs == [2.00]                       # the whole lot is gone
    assert db.get(type(keep), keep.id) is not None


def test_purge_keeps_batches_a_live_row_still_needs(db, card):
    """A pool shared with a live row keeps its cost basis — purging one bin must
    not strip the cost off the other."""
    day = datetime(2026, 6, 26, tzinfo=timezone.utc)
    a = inv.find_or_create_item(db, catalog_card_id=card.id, condition="NM", bin="A")
    b = inv.find_or_create_item(db, catalog_card_id=card.id, condition="NM", bin="B")
    inv.add_stock(db, a, 2, 3.00, acquired_at=day)
    inv.add_stock(db, b, 2, 3.00, acquired_at=day)
    a.deleted = True
    db.commit()

    summary = inv.purge_deleted(db)
    assert summary["items"] == 1
    assert summary["batches"] == 0 and summary["batches_kept"] == 2
    assert inv.fifo_unit_cost(db, b) == 3.00


def test_purchases_endpoint_totals(db, card):
    day = datetime(2026, 6, 26, tzinfo=timezone.utc)
    _lot(db, card, day=day, unit_cost=2.00, condition="NM", qty=3)
    _lot(db, card, day=day - timedelta(days=1), unit_cost=5.00, condition="LP", qty=1)
    db.commit()

    payload = purchases_router.lots(db)
    assert [l["date"] for l in payload["lots"]] == ["2026-06-26", "2026-06-25"]
    # Nothing is priced, so the whole spend projects as a loss — with the unit
    # count that explains it rather than leaving the number looking broken.
    assert payload["totals"] == {"purchases": 2, "units": 4, "paid": 11.00,
                                 "left": 4, "ask": 0.0, "unpriced_units": 4,
                                 "revenue": 0.0, "projected": -11.00}
