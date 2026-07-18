"""Inventory service: aggregation, clamping, FIFO, oversell controls."""
from datetime import datetime, timedelta, timezone

from app.models import InventoryLog, Order, OrderItem
from app.services import inventory as inv


def test_same_identity_same_bin_aggregates(db, card):
    a = inv.find_or_create_item(db, catalog_card_id=card.id, condition="NM",
                                printing="normal", bin="A")
    b = inv.find_or_create_item(db, catalog_card_id=card.id, condition="NM",
                                printing="normal", bin="A")
    assert a.id == b.id
    # different bin -> different record
    c = inv.find_or_create_item(db, catalog_card_id=card.id, condition="NM",
                                printing="normal", bin="B")
    assert c.id != a.id


def test_deduction_clamps_at_zero_and_logs(db, card):
    item = inv.find_or_create_item(db, catalog_card_id=card.id, bin="A")
    inv.add_stock(db, item, 3, 1.50)
    applied = inv.apply_delta(db, item, -10)
    assert applied == -3
    assert item.quantity == 0
    logs = db.query(InventoryLog).all()
    assert {l.type for l in logs} == {"addition", "deduction"}


def test_fifo_consumes_oldest_first(db, card):
    item = inv.find_or_create_item(db, catalog_card_id=card.id, bin="A")
    old = datetime.now(timezone.utc) - timedelta(days=100)
    inv.add_stock(db, item, 2, 1.00, acquired_at=old)
    inv.add_stock(db, item, 2, 5.00)
    # sell 3: 2 @ $1 + 1 @ $5
    cogs = inv.consume_fifo(db, item, 3)
    assert cogs == 2 * 1.00 + 1 * 5.00
    assert inv.fifo_unit_cost(db, item) == 5.00
    assert inv.inventory_age_days(db, item) == 0  # oldest remaining batch is new


def test_fifo_restore_on_order_reversal(db, card):
    item = inv.find_or_create_item(db, catalog_card_id=card.id, bin="A")
    inv.add_stock(db, item, 5, 2.00)
    order = Order(marketplace="ebay", external_order_id="X1")
    db.add(order)
    db.flush()
    cogs = inv.consume_fifo(db, item, 4, order_id=order.id)
    assert cogs == 8.00
    inv.restore_fifo(db, order.id)
    # all 5 units' cost restored
    assert inv.fifo_unit_cost(db, item) == 2.00
    cogs2 = inv.consume_fifo(db, item, 5)
    assert cogs2 == 10.00


def test_effective_quantity_reserves_and_caps(db, card):
    item = inv.find_or_create_item(db, catalog_card_id=card.id, bin="A")
    inv.add_stock(db, item, 10, 1.00)
    ebay = inv.get_or_create_listing(db, item, "ebay")
    tcg = inv.get_or_create_listing(db, item, "tcgplayer")
    db.commit()
    # no controls: full quantity both sides
    assert inv.effective_quantity(item, "ebay") == 10
    assert inv.effective_quantity(item, "tcgplayer") == 10
    # reserve 3 for tcgplayer -> ebay sees 7
    tcg.reserve_quantity = 3
    assert inv.effective_quantity(item, "ebay") == 7
    assert inv.effective_quantity(item, "tcgplayer") == 10
    # cap ebay at 2
    ebay.listing_cap = 2
    assert inv.effective_quantity(item, "ebay") == 2
    # cap 0 = excluded entirely
    ebay.listing_cap = 0
    assert inv.effective_quantity(item, "ebay") == 0


def test_transfer_logs_bin_before_after(db, card):
    item = inv.find_or_create_item(db, catalog_card_id=card.id, bin="A")
    inv.add_stock(db, item, 1, None)
    inv.transfer_bin(db, item, "B")
    log = db.query(InventoryLog).filter_by(type="transfer").one()
    assert log.bin_before == "A" and log.bin_after == "B"
    assert item.bin == "B"


def test_order_deduction_replay_safe(db, card):
    from app.services.marketplaces.sync import apply_order_deduction
    item = inv.find_or_create_item(db, catalog_card_id=card.id, bin="A")
    inv.add_stock(db, item, 5, 2.00)
    order = Order(marketplace="ebay", external_order_id="R1")
    db.add(order)
    db.flush()
    db.add(OrderItem(order_id=order.id, inventory_id=item.id,
                     description="Test Bolt", quantity=2, unit_price=10.0))
    db.flush()
    db.refresh(order)
    apply_order_deduction(db, order)
    assert item.quantity == 3
    # replaying the same order must NOT double-deduct
    apply_order_deduction(db, order)
    assert item.quantity == 3
    assert order.items[0].cogs == 4.00
