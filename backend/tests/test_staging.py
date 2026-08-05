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


def _stocked_pile(db, name="Commons", quantity=100, total_cost=50.0,
                  acquired_at="2026-06-20"):
    """A bulk pile holding stock, plus its InventoryItem (the id rows point at)."""
    pile = bulk_router.create_pile({"name": name, "game": "Magic"}, db)
    product = db.get(CustomProduct, pile["id"])
    bulk_router.record_purchase(product.id, {"quantity": quantity,
                                             "total_cost": total_cost,
                                             "acquired_at": acquired_at}, db)
    return product, db.query(InventoryItem).filter_by(
        custom_sku_id=product.skus[0].id).one()


def test_manual_add_can_pull_from_a_bulk_pile(db, card):
    """Sifting a pile by hand is the same job as sifting it with the scanner: the
    manual-add path carries source_bulk_id, so the card comes OUT of the pile."""
    _product, pile_item = _stocked_pile(db)
    staging_router.bulk_add({"rows": [
        # str id, bin and cost as the select/boxes send them
        {"catalog_card_id": card.id, "bin": "A", "cost": "9.99",
         "source_bulk_id": str(pile_item.id)},
        {"catalog_card_id": card.id, "bin": "B"},
    ]}, db)

    from_pile, fresh = db.query(StagingItem).order_by(StagingItem.id).all()
    assert from_pile.source_bulk_id == pile_item.id   # coerced to int, not "3"
    assert fresh.source_bulk_id is None

    res = staging_svc.approve_staging_rows(db, [from_pile])
    assert (res["approved"], res["skipped"]) == (1, [])
    assert db.get(InventoryItem, pile_item.id).quantity == 99   # pile decremented
    pulled = db.query(InventoryItem).filter_by(catalog_card_id=card.id, bin="A").one()
    assert inv.fifo_unit_cost(db, pulled) == 0.50               # pile's cost, not $9.99
    assert inv.oldest_acquisition_date(db, pulled).date().isoformat() == "2026-06-20"


def test_manual_direct_add_pulls_from_bulk_immediately(db, card):
    _product, pile_item = _stocked_pile(db, quantity=10, total_cost=1.0)
    staging_router.bulk_add({"direct": True, "rows": [
        {"catalog_card_id": card.id, "bin": "A", "quantity": 2,
         "source_bulk_id": pile_item.id},
    ]}, db)
    assert db.query(StagingItem).count() == 0                   # skipped staging
    assert db.get(InventoryItem, pile_item.id).quantity == 8
    pulled = db.query(InventoryItem).filter_by(catalog_card_id=card.id, bin="A").one()
    assert (pulled.quantity, inv.fifo_unit_cost(db, pulled)) == (2, 0.10)


def test_bulk_edit_sets_and_clears_the_bulk_source(db, card):
    """Setting a pile across a batch is a normal edit; clearing it back to fresh
    stock is the one case where an explicit null has to mean something."""
    _product, pile_item = _stocked_pile(db)
    a, b = (_stage(db, card, bin=f"B{i}") for i in range(2))

    staging_router.bulk_edit({"ids": [a.id, b.id], "set": {
        "source_bulk_id": pile_item.id, "cost": "2.00"}}, db)
    assert (a.source_bulk_id, b.source_bulk_id) == (pile_item.id, pile_item.id)
    assert a.cost is None and b.cost is None      # pile's cost wins, same edit

    res = staging_router.bulk_edit({"ids": [a.id], "set": {
        "source_bulk_id": None, "cost": "2.00"}}, db)
    assert res["updated"] == 1
    assert a.source_bulk_id is None and a.cost == 2.00   # fresh stock again
    assert b.source_bulk_id == pile_item.id             # unselected row untouched


def test_bulk_edit_skips_cost_and_date_on_bulk_pull_rows(db, card):
    """A row pulled from a bulk pile takes its cost AND its acquisition date
    from the pile at approve time, so neither may be stamped onto it by a
    batch-wide edit — that would show a date the approve then ignores."""
    _product, pile_item = _stocked_pile(db)
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
