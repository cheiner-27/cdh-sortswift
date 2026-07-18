"""The four refund forms + sale-expense accounting.

- customer partial refund  → net revenue down, no inventory change
- customer full refund, returned → restock (+qty), COGS backed out
- customer full refund, not returned → write-off (qty stays, COGS = loss)
- supplier full refund → return goods (−qty) + recover cost, no P&L hit
- supplier partial refund → lower cost basis (future COGS drops)
"""
from app.services import inventory as inv
from app.services import orders as order_svc
from app.services import reports as report_svc


def stock(db, card, qty, cost):
    item = inv.find_or_create_item(db, catalog_card_id=card.id, condition="NM", bin="A")
    inv.add_stock(db, item, qty, cost)
    db.commit()
    return item


def sell(db, item, qty=1, price=20.0):
    return order_svc.create_manual_order(
        db, buyer_name="t",
        items=[{"inventory_id": item.id, "quantity": qty, "unit_price": price}])


def test_customer_partial_refund(db, card):
    item = stock(db, card, 3, 5.0)
    o = sell(db, item, 1, 20.0)
    order_svc.refund_sale(db, o, mode="partial", amount=5.0)
    assert o.amount_refunded == 5.0
    assert o.status == "partially_refunded"
    assert item.quantity == 2  # inventory untouched by a partial refund


def test_partial_refund_cannot_exceed_total(db, card):
    item = stock(db, card, 3, 5.0)
    o = sell(db, item, 1, 20.0)
    import pytest
    with pytest.raises(ValueError):
        order_svc.refund_sale(db, o, mode="partial", amount=25.0)


def test_customer_full_refund_returned_restocks(db, card):
    item = stock(db, card, 3, 5.0)
    o = sell(db, item, 1, 20.0)
    assert item.quantity == 2
    order_svc.refund_sale(db, o, mode="full", returned=True, return_shipping=1.5)
    assert item.quantity == 3                 # card came back
    assert o.status == "refunded"
    assert o.amount_refunded == o.order_total
    assert o.return_shipping_cost == 1.5
    assert all(li.cogs == 0.0 for li in o.items)  # COGS backed out


def test_customer_full_refund_not_returned_is_writeoff(db, card):
    item = stock(db, card, 3, 5.0)
    o = sell(db, item, 1, 20.0)
    assert sum(li.cogs for li in o.items) == 5.0
    order_svc.refund_sale(db, o, mode="full", returned=False)
    assert item.quantity == 2                 # NOT restocked
    assert sum(li.cogs for li in o.items) == 5.0  # cost stays a real loss


def test_supplier_full_return(db, card):
    item = stock(db, card, 4, 5.0)
    r = inv.return_to_supplier(db, item, 2)
    db.commit()
    assert item.quantity == 2
    assert r["cost_recovered"] == 10.0
    assert inv.fifo_unit_cost(db, item) == 5.0  # remaining basis intact


def test_supplier_partial_refund_lowers_cost_basis(db, card):
    item = stock(db, card, 4, 5.0)              # $20 basis, one batch
    r = inv.reduce_cost_basis(db, item, 8.0)    # −$8 off the batch → $12/4 = $3
    db.commit()
    assert inv.fifo_unit_cost(db, item) == 3.0
    assert r["applied"] == 8.0 and r["unapplied"] == 0.0


def test_supplier_partial_refund_is_fifo_not_averaged(db, card):
    item = inv.find_or_create_item(db, catalog_card_id=card.id, condition="NM", bin="A")
    inv.add_stock(db, item, 2, 5.0)             # batch A (older): 2 @ $5 = $10
    inv.add_stock(db, item, 2, 8.0)             # batch B (newer): 2 @ $8 = $16
    db.commit()
    inv.reduce_cost_basis(db, item, 4.0)        # FIFO: all $4 off batch A → $6/2 = $3
    db.commit()
    assert inv.fifo_unit_cost(db, item) == 3.0  # oldest batch reduced
    inv.consume_fifo(db, item, 2)               # sell the 2 oldest ($3) units
    db.commit()
    assert inv.fifo_unit_cost(db, item) == 8.0  # batch B untouched (not averaged)


def test_supplier_partial_refund_caps_at_cost_basis(db, card):
    item = stock(db, card, 2, 5.0)              # $10 total basis
    r = inv.reduce_cost_basis(db, item, 15.0)   # only $10 of cost to recover
    db.commit()
    assert r["applied"] == 10.0 and r["unapplied"] == 5.0
    assert inv.fifo_unit_cost(db, item) == 0.0


def test_pnl_nets_refund(db, card):
    item = stock(db, card, 3, 5.0)
    o = sell(db, item, 1, 20.0)
    order_svc.refund_sale(db, o, mode="partial", amount=6.0)  # → partially_refunded
    row = report_svc.realized_pnl(db, group_by="month")[0]
    assert row["revenue"] == 20.0
    assert row["refunds"] == 6.0
    assert row["cogs"] == 5.0
    assert row["profit"] == 9.0  # 20 − 6 − 5
