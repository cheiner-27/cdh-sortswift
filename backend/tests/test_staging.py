"""Staging bulk-set: stamping one purchase date / cost across a whole batch."""
import pytest
from fastapi import HTTPException

from app.models import CustomProduct, InventoryItem, StagingItem
from app.routers import bulk as bulk_router
from app.routers import staging as staging_router
from app.services import inventory as inv
from app.services import staging as staging_svc


def _stage(db, card, **kw):
    kw.setdefault("quantity", 1)
    row = StagingItem(source="manual", catalog_card_id=card.id, **kw)
    db.add(row)
    db.commit()
    return row


def test_bulk_edit_stamps_date_and_cost_on_selected_rows(db, card):
    a, b, c = (_stage(db, card, bin=f"B{i}") for i in range(3))
    res = staging_router.bulk_edit(
        {"ids": [a.id, b.id], "set": {"acquired_at": "2026-05-04", "cost": "1.25"}}, db)

    assert res["updated"] == 2
    assert [r["cost"] for r in res["rows"]] == [1.25, 1.25]
    for row in (a, b):
        assert row.cost == 1.25
        assert row.acquired_at.date().isoformat() == "2026-05-04"
    assert c.cost is None and c.acquired_at is None  # unselected row untouched


def test_bulk_edit_only_touches_supplied_fields(db, card):
    row = _stage(db, card, bin="OLD", cost=9.0, condition="NM", quantity=4)
    staging_router.bulk_edit({"ids": [row.id], "set": {
        "bin": "NEW", "cost": "", "condition": None, "price": 3.5}}, db)
    assert (row.bin, row.cost, row.condition, row.price) == ("NEW", 9.0, "NM", 3.5)
    assert row.quantity == 4


def test_bulk_edit_no_ids_or_no_fields_is_a_noop(db, card):
    row = _stage(db, card, cost=2.0)
    assert staging_router.bulk_edit({"ids": [], "set": {"cost": "5"}}, db)["updated"] == 0
    assert staging_router.bulk_edit({"ids": [row.id], "set": {}}, db)["updated"] == 0
    assert row.cost == 2.0


def test_bulk_edit_rejects_non_numeric_cost(db, card):
    row = _stage(db, card)
    with pytest.raises(HTTPException) as e:
        staging_router.bulk_edit({"ids": [row.id], "set": {"cost": "free"}}, db)
    assert e.value.status_code == 400


def test_bulk_edit_skips_cost_and_date_on_bulk_pull_rows(db, card):
    """A row pulled from a bulk pile takes its cost AND its acquisition date
    from the pile at approve time, so neither may be stamped onto it by a
    batch-wide edit — that would show a date the approve then ignores."""
    pile = bulk_router.create_pile({"name": "Commons", "game": "Magic"}, db)
    product = db.get(CustomProduct, pile["id"])
    bulk_router.record_purchase(product.id, {"quantity": 100, "total_cost": 50.0,
                                             "acquired_at": "2026-06-20"}, db)
    pile_item = db.query(InventoryItem).filter_by(
        custom_sku_id=product.skus[0].id).one()

    from_pile = _stage(db, card, source_bulk_id=pile_item.id)
    fresh = _stage(db, card, bin="A")
    staging_router.bulk_edit(
        {"ids": [from_pile.id, fresh.id],
         "set": {"cost": "2.00", "acquired_at": "2026-08-04", "bin": "A"}}, db)

    assert from_pile.cost is None and from_pile.bin == "A"  # other fields still apply
    assert from_pile.acquired_at is None                    # pile's date wins
    assert fresh.cost == 2.00
    assert fresh.acquired_at.date().isoformat() == "2026-08-04"
    # approving carries the pile's per-card cost ($0.50), not the batch cost
    staging_svc.approve_staging_rows(db, [from_pile])
    pulled = db.query(InventoryItem).filter_by(
        catalog_card_id=card.id, bin="A", quantity=1).one()
    assert inv.fifo_unit_cost(db, pulled) == 0.50
