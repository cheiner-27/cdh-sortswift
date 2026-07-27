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
    assert ids(cost_max=1.0) == {cheap_old.id, no_history.id}  # no cost basis reads as $0
    assert ids(cost_min=0.25, cost_max=8.00) == {cheap_old.id, pricey_new.id}
    assert ids(age_max_days=30) == {pricey_new.id}   # unknown age matches neither bound
    assert ids(age_min_days=30) == {cheap_old.id}
    assert ids(age_min_days=1, age_max_days=300) == {cheap_old.id, pricey_new.id}
