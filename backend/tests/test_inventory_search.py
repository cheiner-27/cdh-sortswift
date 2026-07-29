"""Inventory search: the money roll-up shown on the Inventory screen, and the
cost/age range filters that drive it."""
from datetime import datetime, timedelta, timezone

from app.routers import inventory as inventory_router
from app.services import inventory as inv


def test_search_totals_cover_the_whole_filtered_set(db, card):
    nm = inv.find_or_create_item(db, catalog_card_id=card.id, condition="NM", bin="A")
    inv.add_stock(db, nm, 3, 2.00)
    nm.price_override = 12.0
    lp = inv.find_or_create_item(db, catalog_card_id=card.id, condition="LP", bin="A")
    inv.add_stock(db, lp, 2, 1.00)
    lp.current_price = 6.0
    db.commit()

    all_rows = inventory_router.search({"in_stock_only": True}, db)
    assert all_rows["total"] == 2
    assert all_rows["totals"] == {
        "units": 5,
        "cost": 3 * 2.00 + 2 * 1.00,
        "market": 5 * 10.00,             # $10 normal market from the card fixture
        "listed": 3 * 12.00 + 2 * 6.00,  # override where set, else auto price
    }

    # ...and follow the filter
    filtered = inventory_router.search({"in_stock_only": True, "condition": "LP"}, db)
    assert filtered["total"] == 1
    assert filtered["totals"] == {"units": 2, "cost": 2.00, "market": 20.00,
                                  "listed": 12.00}


def test_search_totals_ignore_page_limit(db, card):
    for i in range(3):
        item = inv.find_or_create_item(db, catalog_card_id=card.id, bin=f"B{i}")
        inv.add_stock(db, item, 1, 4.00)
    db.commit()
    page = inventory_router.search({"in_stock_only": True, "limit": 1}, db)
    assert len(page["items"]) == 1
    assert page["total"] == 3
    assert page["totals"] == {"units": 3, "cost": 12.00, "market": 30.00, "listed": 0.0}


def test_cost_and_age_range_filters(db, card):
    # Cost and age are pool-level facts, and the pool key is
    # card+condition+printing — so vary condition to get three distinct pools.
    now = datetime.now(timezone.utc)
    cheap_old = inv.find_or_create_item(db, catalog_card_id=card.id,
                                        condition="NM", bin="OLD")
    inv.add_stock(db, cheap_old, 1, 0.25, acquired_at=now - timedelta(days=200))
    pricey_new = inv.find_or_create_item(db, catalog_card_id=card.id,
                                         condition="LP", bin="NEW")
    inv.add_stock(db, pricey_new, 1, 8.00, acquired_at=now - timedelta(days=3))
    no_history = inv.find_or_create_item(db, catalog_card_id=card.id,
                                         condition="MP", bin="BARE")
    inv.apply_delta(db, no_history, 1)  # quantity but no acquisition batch
    db.commit()

    def ids(**params):
        return {i.id for i in inventory_router.filter_items(
            db, {"in_stock_only": True, **params})}

    assert ids() == {cheap_old.id, pricey_new.id, no_history.id}
    assert ids(cost_min=1.0) == {pricey_new.id}
    assert ids(cost_max=1.0) == {cheap_old.id}  # unknown cost matches neither bound
    assert ids(cost_min=0.25, cost_max=8.00) == {cheap_old.id, pricey_new.id}
    assert ids(age_max_days=30) == {pricey_new.id}   # unknown age matches neither bound
    assert ids(age_min_days=30) == {cheap_old.id}
    assert ids(age_min_days=1, age_max_days=300) == {cheap_old.id, pricey_new.id}


def test_cost_filter_reaches_sold_out_rows(db, card):
    """A row that has sold through keeps its last purchase cost, so the Cost
    filters still work on it — the age filters deliberately do not."""
    now = datetime.now(timezone.utc)
    sold_out = inv.find_or_create_item(db, catalog_card_id=card.id, condition="NM")
    inv.add_stock(db, sold_out, 2, 0.30, acquired_at=now - timedelta(days=90))
    inv.add_stock(db, sold_out, 2, 4.00, acquired_at=now - timedelta(days=10))
    on_hand = inv.find_or_create_item(db, catalog_card_id=card.id, condition="LP")
    inv.add_stock(db, on_hand, 1, 2.00, acquired_at=now - timedelta(days=5))
    db.commit()

    # Drain the whole pool: both batches exhausted, quantity back to 0.
    inv.consume_fifo(db, sold_out, 4)
    inv.apply_delta(db, sold_out, -4)
    db.commit()
    assert sold_out.quantity == 0
    assert inv.fifo_unit_cost(db, sold_out) is None  # pricing/COGS still see nothing

    roll = inv.fifo_rollup(db, [sold_out])[sold_out.id]
    assert roll["unit_cost"] == 4.00   # newest spent batch — what we last paid
    assert roll["age_days"] is None    # no age once nothing is on hand
    assert roll["cost_basis"] == 0.0   # nothing on hand to value

    def ids(**params):
        return {i.id for i in inventory_router.filter_items(
            db, {"in_stock_only": False, **params})}

    assert ids(cost_min=3.00) == {sold_out.id}
    assert ids(cost_max=1.00) == set()          # was: matched every sold-out row
    assert ids(cost_min=1.00, cost_max=5.00) == {sold_out.id, on_hand.id}
    assert ids(age_max_days=3650) == {on_hand.id}
