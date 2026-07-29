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


def test_lot_ask_matches_the_inventory_drill_through(db, card):
    """The Asking column has to agree with what the Inventory screen totals when
    you click into the lot, or the two screens argue about the same money."""
    day = datetime(2026, 6, 26, tzinfo=timezone.utc)
    a = _lot(db, card, day=day, unit_cost=7.643, condition="NM", qty=2)
    a.price_override = 12.50
    b = _lot(db, card, day=day, unit_cost=7.643, condition="LP", qty=1)
    b.current_price = 9.00
    db.commit()

    lot = next(l for l in reports.purchase_lots(db) if l["unit_cost"] == 7.643)
    drill = inventory_router.search(
        {"in_stock_only": False, "lot": {"date": "2026-06-26", "unit_cost": 7.643}}, db)
    assert lot["ask"] == 2 * 12.50 + 9.00
    assert lot["ask"] == drill["totals"]["listed"]
    assert lot["units_on_hand"] == drill["totals"]["units"] == 3
    assert lot["rows"] == drill["total"] == 2


def test_purchases_endpoint_totals(db, card):
    day = datetime(2026, 6, 26, tzinfo=timezone.utc)
    _lot(db, card, day=day, unit_cost=2.00, condition="NM", qty=3)
    _lot(db, card, day=day - timedelta(days=1), unit_cost=5.00, condition="LP", qty=1)
    db.commit()

    payload = purchases_router.lots(db)
    assert [l["date"] for l in payload["lots"]] == ["2026-06-26", "2026-06-25"]
    assert payload["totals"] == {"purchases": 2, "units": 4, "paid": 11.00,
                                 "left": 4, "ask": 0.0}
