"""Bulk piles: FIFO buy/sell and pulling a card OUT of a pile.

A bulk pile is a CustomProduct(item_type="bulk") + CustomSku + InventoryItem
(quantity = card count). Buys are FIFO batches; sells book COGS oldest-first;
pulling a card into tracked inventory conserves total cost basis and carries the
pile's acquisition age (never resets to "now").
"""
from datetime import datetime, timezone

from sqlalchemy import select

from app.models import AcquisitionLog, CustomProduct, InventoryItem, Order, StagingItem
from app.routers import bulk as bulk_router
from app.services import inventory as inv
from app.services import staging as staging_svc


def _make_pile(db, name="MTG Bulk Commons"):
    d = bulk_router.create_pile({"name": name, "game": "Magic"}, db)
    return db.get(CustomProduct, d["id"])


def _pile_item(db, product):
    sku = product.skus[0]
    return db.execute(select(InventoryItem).where(
        InventoryItem.custom_sku_id == sku.id)).scalars().first()


def _total_basis(db):
    return sum(b.quantity_remaining * b.unit_cost
               for b in db.execute(select(AcquisitionLog)).scalars().all())


def test_bulk_purchase_records_fifo_batches(db):
    product = _make_pile(db)
    bulk_router.record_purchase(product.id, {"quantity": 500, "unit_cost": 0.05,
                                             "acquired_at": "2025-01-01"}, db)
    bulk_router.record_purchase(product.id, {"quantity": 1000, "unit_cost": 0.06,
                                             "acquired_at": "2025-02-01"}, db)
    item = _pile_item(db, product)
    assert item.quantity == 1500
    assert inv.fifo_unit_cost(db, item) == 0.05   # oldest (5c) batch sells first


def test_total_cost_splits_into_per_card(db):
    product = _make_pile(db)
    bulk_router.record_purchase(product.id, {"quantity": 200, "total_cost": 10.0}, db)
    item = _pile_item(db, product)
    assert item.quantity == 200
    assert inv.fifo_unit_cost(db, item) == 0.05   # $10 / 200 cards


def test_bulk_sale_books_fifo_cogs(db):
    product = _make_pile(db)
    bulk_router.record_purchase(product.id, {"quantity": 500, "unit_cost": 0.05,
                                             "acquired_at": "2025-01-01"}, db)
    bulk_router.record_purchase(product.id, {"quantity": 1000, "unit_cost": 0.06,
                                             "acquired_at": "2025-02-01"}, db)
    res = bulk_router.sell_bulk(product.id, {"quantity": 800, "total_price": 40.0}, db)

    item = _pile_item(db, product)
    assert item.quantity == 700                    # 1500 - 800
    order = db.get(Order, res["order_id"])
    cogs = sum(li.cogs or 0 for li in order.items)
    assert round(cogs, 2) == 43.00                 # 500×0.05 + 300×0.06 = 25 + 18


def test_pull_from_bulk_conserves_cost_and_age(db, card):
    product = _make_pile(db)
    when = datetime(2025, 1, 1, tzinfo=timezone.utc)
    bulk_router.record_purchase(product.id, {"quantity": 1000, "unit_cost": 0.05,
                                             "acquired_at": "2025-01-01"}, db)
    pile = _pile_item(db, product)
    before = _total_basis(db)

    # Pull one card out via the real staging-approve path (source_bulk_id set).
    row = StagingItem(source="scan", catalog_card_id=card.id, condition="NM",
                      printing="normal", language="en", bin="A", quantity=1,
                      source_bulk_id=pile.id)
    db.add(row)
    db.commit()
    staging_svc.approve_staging_rows(db, [row])

    pile = _pile_item(db, product)
    tgt = inv.find_or_create_item(db, catalog_card_id=card.id, condition="NM", bin="A")
    assert pile.quantity == 999                    # pile decremented
    assert tgt.quantity == 1                        # card graduated to tracked inventory
    assert inv.fifo_unit_cost(db, tgt) == 0.05      # carried the pile's per-card cost
    assert inv.oldest_acquisition_date(db, tgt).date() == when.date()  # pile's age, not now
    assert round(_total_basis(db), 4) == round(before, 4)   # total cost basis conserved


def test_multiple_purchases_stay_one_record(db):
    product = _make_pile(db, "One Row")
    bulk_router.record_purchase(product.id, {"quantity": 100, "unit_cost": 0.05, "bin": "BULK-A"}, db)
    bulk_router.record_purchase(product.id, {"quantity": 200, "unit_cost": 0.06, "bin": "ignored"}, db)
    items = db.execute(select(InventoryItem).where(
        InventoryItem.custom_sku_id == product.skus[0].id,
        InventoryItem.deleted == False)).scalars().all()  # noqa: E712
    assert len(items) == 1                 # not split across bins
    assert items[0].quantity == 300
    assert items[0].bin == "BULK-A"        # 2nd buy reused the 1st bin


def test_delete_unstocked_pile_removed(db):
    product = _make_pile(db, "Empty Pile")
    res = bulk_router.delete_pile(product.id, db)
    assert res["deleted"] is True
    assert db.get(CustomProduct, product.id) is None
    assert bulk_router.list_piles(db) == []


def test_delete_stocked_pile_hidden_history_kept(db):
    product = _make_pile(db, "Stocked Pile")
    bulk_router.record_purchase(product.id, {"quantity": 100, "unit_cost": 0.05}, db)
    res = bulk_router.delete_pile(product.id, db)
    assert res["soft_deleted"] is True
    assert all(p["id"] != product.id for p in bulk_router.list_piles(db))  # hidden
    assert db.get(CustomProduct, product.id) is not None                   # product kept
    item = bulk_router._pile_item(db, product.skus[0], include_deleted=True)
    assert item.deleted is True


def test_pull_clamps_at_pile_stock(db, card):
    product = _make_pile(db)
    bulk_router.record_purchase(product.id, {"quantity": 1, "unit_cost": 0.05}, db)
    pile = _pile_item(db, product)
    row = StagingItem(source="scan", catalog_card_id=card.id, condition="NM",
                      printing="normal", language="en", bin="A", quantity=5,
                      source_bulk_id=pile.id)
    db.add(row)
    db.commit()
    staging_svc.approve_staging_rows(db, [row])

    pile = _pile_item(db, product)
    tgt = inv.find_or_create_item(db, catalog_card_id=card.id, condition="NM", bin="A")
    assert pile.quantity == 0        # clamped, never negative
    assert tgt.quantity == 1         # only the 1 available card moved


# --- source_bulk_id must be the pile's INVENTORY id -------------------------
# The pile id and inventory id spaces overlap, so sending the wrong one used to
# resolve to whatever unrelated card held that inventory id and "pull" from it:
# 0 units moved, the staging row deleted anyway, and a success message. These
# pin the id contract and the refusal.

def test_pull_refuses_a_non_pile_inventory_id(db, card):
    """A plain card's inventory id is not a bulk pile — refuse, keep the row."""
    product = _make_pile(db)
    bulk_router.record_purchase(product.id, {"quantity": 100, "unit_cost": 0.05}, db)
    decoy = inv.find_or_create_item(db, catalog_card_id=card.id, condition="LP")
    inv.add_stock(db, decoy, 3, 1.0)
    db.commit()

    row = StagingItem(source="scan", catalog_card_id=card.id, condition="NM",
                      printing="normal", language="en", bin="A", quantity=1,
                      source_bulk_id=decoy.id)   # wrong id space
    db.add(row)
    db.commit()
    res = staging_svc.approve_staging_rows(db, [row])

    assert res["approved"] == 0
    assert res["skipped"][0]["id"] == row.id
    assert "not a bulk pile" in res["skipped"][0]["reason"]
    assert db.get(StagingItem, row.id) is not None   # kept, not discarded
    assert decoy.quantity == 3                       # unrelated card untouched
    assert _pile_item(db, product).quantity == 100   # real pile untouched


def test_pull_refuses_an_empty_pile_and_keeps_the_row(db, card):
    """The exact live failure: pile resolves but holds 0, so 0 units moved."""
    product = _make_pile(db)
    bulk_router.record_purchase(product.id, {"quantity": 1, "unit_cost": 0.05}, db)
    pile = _pile_item(db, product)
    bulk_router.sell_bulk(product.id, {"quantity": 1, "total_price": 1.0}, db)
    assert _pile_item(db, product).quantity == 0

    row = StagingItem(source="scan", catalog_card_id=card.id, condition="NM",
                      printing="normal", language="en", bin="A", quantity=1,
                      source_bulk_id=pile.id)
    db.add(row)
    db.commit()
    res = staging_svc.approve_staging_rows(db, [row])

    assert res["approved"] == 0
    assert "no cards on hand" in res["skipped"][0]["reason"]
    assert db.get(StagingItem, row.id) is not None
    # and no empty inventory record was created as a side effect
    assert db.execute(select(InventoryItem).where(
        InventoryItem.catalog_card_id == card.id)).scalars().first() is None


def test_pull_refuses_a_missing_pile(db, card):
    row = StagingItem(source="scan", catalog_card_id=card.id, condition="NM",
                      printing="normal", language="en", quantity=1,
                      source_bulk_id=99999)
    db.add(row)
    db.commit()
    res = staging_svc.approve_staging_rows(db, [row])
    assert res["approved"] == 0
    assert "does not exist" in res["skipped"][0]["reason"]


def test_one_bad_row_does_not_block_the_good_ones(db, card):
    product = _make_pile(db)
    bulk_router.record_purchase(product.id, {"quantity": 100, "unit_cost": 0.05}, db)
    pile = _pile_item(db, product)
    good = StagingItem(source="scan", catalog_card_id=card.id, condition="NM",
                       printing="normal", language="en", bin="A", quantity=1,
                       source_bulk_id=pile.id)
    bad = StagingItem(source="scan", catalog_card_id=card.id, condition="LP",
                      printing="normal", language="en", bin="B", quantity=1,
                      source_bulk_id=99999)
    db.add_all([good, bad])
    db.commit()
    res = staging_svc.approve_staging_rows(db, [good, bad])

    assert res["approved"] == 1 and len(res["skipped"]) == 1
    assert db.get(StagingItem, good.id) is None      # approved and consumed
    assert db.get(StagingItem, bad.id) is not None   # still staged for a retry


def test_partial_pull_is_reported(db, card):
    product = _make_pile(db)
    bulk_router.record_purchase(product.id, {"quantity": 2, "unit_cost": 0.05}, db)
    pile = _pile_item(db, product)
    row = StagingItem(source="scan", catalog_card_id=card.id, condition="NM",
                      printing="normal", language="en", bin="A", quantity=5,
                      source_bulk_id=pile.id)
    db.add(row)
    db.commit()
    res = staging_svc.approve_staging_rows(db, [row])
    assert res["approved"] == 1
    assert res["partial"] == [{"id": row.id, "wanted": 5, "moved": 2}]


def test_direct_add_rejects_a_bad_pile(db, card):
    import pytest
    with pytest.raises(ValueError, match="does not exist"):
        staging_svc.add_direct(db, catalog_card_id=card.id, quantity=1,
                               source_bulk_id=99999)


# --- the pile's purchase date ----------------------------------------------

def test_pile_reports_the_date_pulled_cards_inherit(db):
    product = _make_pile(db)
    bulk_router.record_purchase(product.id, {"quantity": 500, "unit_cost": 0.05,
                                             "acquired_at": "2026-06-20"}, db)
    d = bulk_router.pile_dict(db, product)
    assert d["acquired_at"].startswith("2026-06-20")
    assert d["batch_count"] == 1
    assert d["inventory_id"] == _pile_item(db, product).id   # the id to pull with


def test_pile_date_is_the_oldest_unexhausted_buy(db):
    """FIFO spends the oldest batch next, so that is the date carried out."""
    product = _make_pile(db)
    bulk_router.record_purchase(product.id, {"quantity": 10, "unit_cost": 0.05,
                                             "acquired_at": "2025-01-01"}, db)
    bulk_router.record_purchase(product.id, {"quantity": 10, "unit_cost": 0.06,
                                             "acquired_at": "2025-06-01"}, db)
    assert bulk_router.pile_dict(db, product)["acquired_at"].startswith("2025-01-01")
    assert bulk_router.pile_dict(db, product)["batch_count"] == 2
    bulk_router.sell_bulk(product.id, {"quantity": 10, "total_price": 5.0}, db)
    # oldest batch spent → the next cards out now carry the second buy's date
    assert bulk_router.pile_dict(db, product)["acquired_at"].startswith("2025-06-01")


def test_correcting_a_pile_date_moves_the_pulled_cards_age(db, card):
    product = _make_pile(db)
    bulk_router.record_purchase(product.id, {"quantity": 100, "unit_cost": 0.05}, db)
    bulk_router.update_pile(product.id, {"acquired_at": "2026-06-20"}, db)
    assert bulk_router.pile_dict(db, product)["acquired_at"].startswith("2026-06-20")

    pile = _pile_item(db, product)
    row = StagingItem(source="scan", catalog_card_id=card.id, condition="NM",
                      printing="normal", language="en", bin="A", quantity=1,
                      source_bulk_id=pile.id)
    db.add(row)
    db.commit()
    staging_svc.approve_staging_rows(db, [row])
    tgt = inv.find_or_create_item(db, catalog_card_id=card.id, condition="NM", bin="A")
    assert inv.oldest_acquisition_date(db, tgt).date() == datetime(2026, 6, 20).date()


def test_multi_buy_pile_refuses_an_ambiguous_date_edit(db):
    import pytest
    from fastapi import HTTPException
    product = _make_pile(db)
    bulk_router.record_purchase(product.id, {"quantity": 10, "unit_cost": 0.05,
                                             "acquired_at": "2025-01-01"}, db)
    bulk_router.record_purchase(product.id, {"quantity": 10, "unit_cost": 0.06,
                                             "acquired_at": "2025-06-01"}, db)
    with pytest.raises(HTTPException) as e:
        bulk_router.update_pile(product.id, {"acquired_at": "2025-03-03"}, db)
    assert e.value.status_code == 400
    assert "2 separate buys" in e.value.detail
