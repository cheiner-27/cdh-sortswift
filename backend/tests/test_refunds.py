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
        db, platform="t",
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


# --- fee refunds, shipping charged, and the cost-follows-the-card rule ---------

def test_full_refund_credits_fees_back(db, card):
    item = stock(db, card, 1, 40.0)
    o = order_svc.create_manual_order(
        db, platform="t", marketplace_fees=13.25, shipping_cost=1.0,
        items=[{"inventory_id": item.id, "quantity": 1, "unit_price": 100.0}])
    order_svc.mark_shipped(db, o)
    order_svc.refund_sale(db, o, mode="full", returned=True, return_shipping=1.0)
    assert o.fees_refunded == 13.25            # TCGplayer hands the fee back
    row = report_svc.realized_pnl(db, group_by="month")[0]
    assert row["fees"] == 0.0                  # charged 13.25, credited 13.25
    assert row["cogs"] == 0.0                  # cost follows the card
    # only the shipping we ate is left: 1.00 out + 1.00 return
    assert row["profit"] == -2.0


def test_full_refund_can_keep_part_of_the_fee(db, card):
    item = stock(db, card, 1, 40.0)
    o = order_svc.create_manual_order(
        db, platform="t", marketplace_fees=13.25,
        items=[{"inventory_id": item.id, "quantity": 1, "unit_price": 100.0}])
    order_svc.mark_shipped(db, o)
    order_svc.refund_sale(db, o, mode="full", returned=True, fees_refunded=10.00)
    assert o.fees_refunded == 10.0
    assert report_svc.realized_pnl(db, group_by="month")[0]["profit"] == -3.25


def test_full_refund_returns_shipping_the_buyer_paid(db, card):
    item = stock(db, card, 1, 5.0)
    o = order_svc.create_manual_order(
        db, platform="t", shipping_charged=4.99,
        items=[{"inventory_id": item.id, "quantity": 1, "unit_price": 20.0}])
    order_svc.mark_shipped(db, o)
    order_svc.refund_sale(db, o, mode="full", returned=True)
    assert o.amount_refunded == 24.99          # items + the shipping they paid
    row = report_svc.realized_pnl(db, group_by="month")[0]
    assert row["revenue"] == 24.99 and row["refunds"] == 24.99
    assert row["profit"] == 0.0                # a fully refunded sale is a wash


def test_partial_refund_credits_fees_pro_rata(db, card):
    item = stock(db, card, 3, 5.0)
    o = order_svc.create_manual_order(
        db, platform="t", marketplace_fees=10.0,
        items=[{"inventory_id": item.id, "quantity": 1, "unit_price": 100.0}])
    order_svc.mark_shipped(db, o)
    order_svc.refund_sale(db, o, mode="partial", amount=25.0)
    assert o.fees_refunded == 2.5              # 25% refunded → 25% of the fee back
    assert report_svc.realized_pnl(db, group_by="month")[0]["fees"] == 7.5


def test_partial_refund_cap_includes_shipping_charged(db, card):
    item = stock(db, card, 3, 5.0)
    o = order_svc.create_manual_order(
        db, platform="t", shipping_charged=5.0,
        items=[{"inventory_id": item.id, "quantity": 1, "unit_price": 20.0}])
    order_svc.refund_sale(db, o, mode="partial", amount=24.0)  # ≤ 20 + 5
    assert o.amount_refunded == 24.0


def test_refund_backs_out_cogs_on_migrated_line_with_no_inventory(db, card):
    """Migrated/historical sales carry COGS on the line with no inventory record
    behind them. The card still came back, so its cost must not stay expensed —
    otherwise it is double-counted when the card is re-added and re-sold."""
    from app.models import OrderItem
    o = order_svc.create_manual_order(db, platform="t", items=[], total=171.0,
                                      marketplace_fees=23.21, shipping_cost=5.28)
    db.add(OrderItem(order_id=o.id, catalog_card_id=card.id, description="Eevee & Snorlax GX",
                     quantity=1, unit_price=171.0, cogs=121.16))
    o.deduction_applied = True
    db.commit()
    db.refresh(o)
    order_svc.mark_shipped(db, o)

    r = order_svc.refund_sale(db, o, mode="full", returned=True, return_shipping=5.28)
    assert r["unlinked_lines"] == ["Eevee & Snorlax GX"]  # flagged: no auto-restock
    assert all(li.cogs == 0.0 for li in o.items)          # cost no longer expensed here
    row = report_svc.realized_pnl(db, group_by="month")[0]
    assert row["cogs"] == 0.0
    assert row["profit"] == -10.56                        # 5.28 out + 5.28 back


def test_writeoff_keeps_cogs_on_migrated_line(db, card):
    """The not-returned path is unchanged: an unlinked line keeps its COGS."""
    from app.models import OrderItem
    o = order_svc.create_manual_order(db, platform="t", items=[], total=50.0)
    db.add(OrderItem(order_id=o.id, catalog_card_id=card.id, description="Scyther",
                     quantity=1, unit_price=50.0, cogs=31.21))
    db.commit()
    db.refresh(o)
    order_svc.refund_sale(db, o, mode="full", returned=False)
    assert sum(li.cogs for li in o.items) == 31.21


def test_refund_then_resell_books_cogs_once(db, card):
    """The whole point of backing the COGS out: sell → refund → resell should
    expense the card's cost exactly once, on the sale that stuck."""
    item = stock(db, card, 1, 40.0)
    o1 = order_svc.create_manual_order(
        db, platform="b1", marketplace_fees=13.25,
        items=[{"inventory_id": item.id, "quantity": 1, "unit_price": 100.0}])
    order_svc.mark_shipped(db, o1)
    order_svc.refund_sale(db, o1, mode="full", returned=True)
    assert item.quantity == 1                  # restocked (line was inventory-linked)

    o2 = order_svc.create_manual_order(
        db, platform="b2", marketplace_fees=14.0,
        items=[{"inventory_id": item.id, "quantity": 1, "unit_price": 110.0}])
    order_svc.mark_shipped(db, o2)
    row = report_svc.realized_pnl(db, group_by="month")[0]
    assert row["cogs"] == 40.0                 # counted once, not twice
    assert row["fees"] == 14.0                 # first sale's fee was credited back
    assert row["profit"] == 56.0               # 210 − 100 refunded − 40 − 14
