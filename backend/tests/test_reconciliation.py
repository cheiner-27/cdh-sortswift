"""Audit checks must expose mismatches without correcting or rewriting them."""
from app.models import AcquisitionLog, FifoConsumption, InventoryLog, Order, OrderItem
from app.routers.inventory import detail, merge_duplicates
from app.services import inventory as inv
from app.services.reconciliation import audit


def stock(db, card, qty=2, cost=3.0, **identity):
    item = inv.find_or_create_item(db, catalog_card_id=card.id, **identity)
    inv.add_stock(db, item, qty, cost)
    db.commit()
    return item


def codes(report):
    return {i["code"] for i in report["issues"]}


def test_balanced_inventory_audit_is_read_only(db, card):
    item = stock(db, card)
    before = db.connection().connection.driver_connection.total_changes
    report = audit(db)
    assert report["summary"]["errors"] == 0
    assert report["summary"]["active_units"] == 2
    assert report["summary"]["on_hand_cost"] == 6
    assert db.connection().connection.driver_connection.total_changes == before
    assert not db.new and not db.dirty and not db.deleted
    assert item.quantity == 2


def test_detects_blood_crypt_style_phantom_lots_despite_balanced_history(db, card):
    item = stock(db, card)
    # Historical faulty adjustment: journal and stock move, FIFO does not.
    inv.apply_delta(db, item, -1, type="adjustment")
    inv.add_stock(db, item, 1, None)
    db.commit()
    report = audit(db)
    assert report["summary"]["history_mismatches"] == 0
    assert report["summary"]["fifo_mismatches"] == 1
    issue = next(i for i in report["issues"] if i["code"] == "fifo_balance")
    assert (issue["stock"], issue["lot_remaining"], issue["difference"]) == (2, 3, 1)
    assert item.quantity == 2
    assert sum(b.quantity_remaining for b in db.query(AcquisitionLog)) == 3


def test_detects_missing_history_separately(db, card):
    item = stock(db, card)
    item.quantity += 1
    db.commit()
    report = audit(db)
    assert {"stock_history", "fifo_balance"} <= codes(report)


def test_archived_stock_is_not_mistaken_for_phantom_cost(db, card):
    item = stock(db, card)
    item.deleted = True
    db.commit()
    report = audit(db)
    assert report["summary"]["errors"] == 0
    assert "archived_stock" in codes(report)
    assert report["pools"][0]["archived_stock"] == 2


def test_old_printing_lot_is_visible_without_inventory_row(db, card):
    item = stock(db, card)
    item.printing = "foil"  # emulate pre-fix reclassification
    db.commit()
    report = audit(db)
    assert report["summary"]["fifo_mismatches"] == 2
    assert sorted(p["difference"] for p in report["pools"]) == [-2, 2]
    assert all("Test Bolt" in p["label"] for p in report["pools"])


def test_sale_cogs_conflict_is_separate_from_migrated_cogs(db, card):
    item = stock(db, card)
    order = Order(marketplace="manual", external_order_id="native", deduction_applied=True)
    migrated = Order(marketplace="tcgplayer", external_order_id="airtable-old", deduction_applied=True)
    db.add_all([order, migrated]); db.flush()
    db.add(OrderItem(order_id=order.id, inventory_id=item.id, quantity=1, cogs=2.0))
    db.add(OrderItem(order_id=migrated.id, quantity=1, cogs=19.0))
    inv.apply_delta(db, item, -1, cause="sale")
    inv.consume_fifo(db, item, 1, order_id=order.id)
    db.commit()
    report = audit(db)
    assert report["summary"]["migrated_orders_without_lot_links"] == 1
    assert report["summary"]["migrated_cogs"] == 19
    errors = [i for i in report["issues"] if i["severity"] == "error"]
    assert len(errors) == 1 and errors[0]["code"] == "order_cogs"
    assert errors[0]["line_cogs"] == 2 and errors[0]["fifo_cogs"] == 3


def test_lot_overallocation_and_invalid_bounds(db, card):
    stock(db, card)
    batch = db.query(AcquisitionLog).one()
    batch.quantity_remaining = 3
    db.add(FifoConsumption(acquisition_id=batch.id, quantity=1, unit_cost=3))
    db.commit()
    report = audit(db)
    assert {"invalid_lot", "lot_overallocated", "fifo_balance"} <= codes(report)


def test_detail_distinguishes_row_history_from_shared_pool(db, card):
    first = stock(db, card, bin="A")
    second = stock(db, card, qty=1, bin="B")
    d = detail(first.id, db)
    assert d["reconciliation"]["history_net"] == first.quantity == 2
    assert d["reconciliation"]["history_matches"]
    assert d["reconciliation"]["lot_remaining"] == 3
    assert d["reconciliation"]["pool_quantity"] == 3
    assert len(d["reconciliation"]["pool_rows"]) == 2
    assert sum(h.quantity_delta for h in db.query(InventoryLog).filter_by(inventory_id=second.id)) == 1


def test_merge_retains_balanced_journals_for_both_rows(db, card):
    first = stock(db, card)
    second = stock(db, card, qty=1, bin="B")
    second.bin = ""
    db.commit()
    assert merge_duplicates({}, db)["merged_rows"] == 1
    report = audit(db)
    assert report["summary"]["errors"] == 0
    assert detail(first.id, db)["reconciliation"]["history_net"] == 3
    assert detail(second.id, db)["reconciliation"]["history_net"] == 0
