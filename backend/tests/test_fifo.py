"""FIFO integrity across split, import undo, and staging date preservation.

Core invariant: for a FIFO pool (card/sku + condition + printing), the sum of
acquisition quantity_remaining must equal the total on-hand item quantity — no
phantom cost basis, no deficit.
"""
from datetime import datetime, timezone

from sqlalchemy import select

from app.models import AcquisitionLog, StagingItem
from app.services import inventory as inv
from app.services import staging as staging_svc


def pool_remaining(db, catalog_card_id, condition, printing):
    rows = db.execute(select(AcquisitionLog).where(
        AcquisitionLog.catalog_card_id == catalog_card_id,
        AcquisitionLog.condition == condition,
        AcquisitionLog.printing == printing)).scalars().all()
    return sum(r.quantity_remaining for r in rows)


def test_split_moves_cost_and_date_no_phantom(db, card):
    when = datetime(2025, 6, 1, tzinfo=timezone.utc)
    src = inv.find_or_create_item(db, catalog_card_id=card.id, condition="NM", bin="A")
    inv.add_stock(db, src, 4, 5.0, acquired_at=when)
    db.commit()

    # split 2 units into a different condition (mirrors the /split endpoint)
    tgt = inv.find_or_create_item(db, catalog_card_id=card.id, condition="LP", bin="A")
    moved = inv.split_cost_basis(db, src, tgt, 2)
    inv.apply_delta(db, src, -2, type="adjustment")
    inv.apply_delta(db, tgt, 2, type="addition")
    db.commit()

    assert moved == 10.0                       # 2 units × $5 cost moved, not duplicated
    assert src.quantity == 2 and tgt.quantity == 2
    # cost basis carried, not reset or duplicated
    assert inv.fifo_unit_cost(db, tgt) == 5.0
    # acquisition DATE carried across (age comes from the original purchase)
    assert inv.oldest_acquisition_date(db, tgt).date() == when.date()
    # invariant holds on both pools: remaining == quantity, no phantom units
    assert pool_remaining(db, card.id, "NM", "normal") == 2
    assert pool_remaining(db, card.id, "LP", "normal") == 2


def test_unrecord_acquisition_is_newest_first(db, card):
    item = inv.find_or_create_item(db, catalog_card_id=card.id, condition="NM", bin="A")
    inv.add_stock(db, item, 3, 5.0, acquired_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
    inv.add_stock(db, item, 2, 8.0, acquired_at=datetime(2025, 2, 1, tzinfo=timezone.utc))
    db.commit()
    removed = inv.unrecord_acquisition(db, item, 2)   # should peel the newest ($8) lot
    db.commit()
    assert removed == 2
    assert inv.fifo_unit_cost(db, item) == 5.0        # oldest lot untouched
    assert pool_remaining(db, card.id, "NM", "normal") == 3


def test_rekey_cost_basis_follows_printing_change(db, card):
    """Changing printing (part of the FIFO pool key) must carry the acquisition
    batches with it — cost, age and lots must not orphan (regression)."""
    when = datetime(2025, 4, 1, tzinfo=timezone.utc)
    item = inv.find_or_create_item(db, catalog_card_id=card.id, condition="NM",
                                   printing="normal", bin="A")
    inv.add_stock(db, item, 3, 2.0, acquired_at=when)
    db.commit()

    item.printing = "foil"
    inv.rekey_cost_basis(db, item, old_condition="NM", old_printing="normal")
    db.commit()

    assert inv.fifo_unit_cost(db, item) == 2.0                       # cost survived
    assert inv.oldest_acquisition_date(db, item).date() == when.date()  # age survived
    assert pool_remaining(db, card.id, "NM", "normal") == 0          # old pool emptied
    assert pool_remaining(db, card.id, "NM", "foil") == 3            # moved to new pool


def test_rekey_keeps_other_item_in_shared_pool_consistent(db, card):
    """When two items share a pool (differ only by bin), reclassifying one moves
    exactly its on-hand units, leaving the other's basis intact."""
    when = datetime(2025, 4, 1, tzinfo=timezone.utc)
    a = inv.find_or_create_item(db, catalog_card_id=card.id, condition="LP",
                                printing="normal", bin="B1")
    inv.add_stock(db, a, 3, 1.0, acquired_at=when)
    b = inv.find_or_create_item(db, catalog_card_id=card.id, condition="LP",
                                printing="normal", bin="B2")
    inv.add_stock(db, b, 2, 1.0, acquired_at=when)
    db.commit()

    a.printing = "foil"
    inv.rekey_cost_basis(db, a, old_condition="LP", old_printing="normal")
    db.commit()

    assert pool_remaining(db, card.id, "LP", "foil") == 3     # A's 3 units moved
    assert pool_remaining(db, card.id, "LP", "normal") == 2   # B's 2 units stay
    assert inv.fifo_unit_cost(db, b) == 1.0


def test_staging_approve_preserves_acquired_date(db, card):
    when = datetime(2025, 3, 15, tzinfo=timezone.utc)
    row = StagingItem(source="csv", catalog_card_id=card.id, condition="NM",
                      printing="normal", language="en", bin="A", quantity=3,
                      cost=4.0, acquired_at=when)
    db.add(row)
    db.commit()
    staging_svc.approve_staging_rows(db, [row])
    item = inv.find_or_create_item(db, catalog_card_id=card.id, condition="NM", bin="A")
    assert item.quantity == 3
    assert inv.oldest_acquisition_date(db, item).date() == when.date()   # date survived staging
    assert inv.fifo_unit_cost(db, item) == 4.0
