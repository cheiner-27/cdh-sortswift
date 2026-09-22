"""Reviewed repairs must be reversible previews and atomic, evidenced writes."""
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models import AcquisitionLog, FifoConsumption, InventoryItem, InventoryLog, InventoryOperation, Order, OrderItem
from app.services import inventory as inv, repairs, reports
from app.services.reconciliation import audit
from app.services.marketplaces.sync import apply_order_deduction, reverse_order_deduction


@pytest.fixture(autouse=True)
def production_session(db):
    db.autoflush = False


def stock(db, card, quantity=2, cost=3, **identity):
    item = inv.find_or_create_item(db, catalog_card_id=card.id, **identity)
    inv.add_stock(db, item, quantity, cost)
    db.commit()
    return item


def execute(db, action, **values):
    payload = {"action": action, "reason": "Reviewed against source records", **values}
    before = repairs.snapshot(db)
    operations_before = db.query(InventoryOperation).count()
    ticket = repairs.preview(db, payload)
    assert repairs.snapshot(db) == before, "A preview must leave every record unchanged"
    assert db.query(InventoryOperation).count() == operations_before
    result = repairs.apply(db, payload, ticket["token"], ticket["request_id"])
    saved = db.get(InventoryOperation, result["operation_id"])
    assert saved.details["before"] == ticket["before"]
    assert saved.details["after"] == ticket["after"]
    assert saved.reason == payload["reason"]
    assert repairs.apply(db, payload, ticket["token"], ticket["request_id"])["already_applied"]
    return ticket


def test_phantom_lot_repair_preserves_stock_purchase_and_sale_history(db, card):
    item = stock(db, card)
    phantom = inv.record_acquisition(db, item, 2, None, origin_kind="adjustment")
    db.commit()
    ticket = execute(db, "lot_balance", lots=[{"id": phantom.id, "remaining": 0}])
    assert ticket["before"]["lot_units"] == 4 and ticket["after"]["lot_units"] == 2
    assert ticket["before"]["stock_units"] == ticket["after"]["stock_units"] == 2
    assert ticket["before"]["purchase_total"] == ticket["after"]["purchase_total"] == 6
    assert audit(db)["summary"]["errors"] == 0


def test_stale_preview_rejected_without_changes(db, card):
    item = stock(db, card)
    phantom = inv.record_acquisition(db, item, 1, None)
    db.commit()
    payload = {"action": "lot_balance", "reason": "Duplicate historical adjustment", "lots": [{"id": phantom.id, "remaining": 0}]}
    ticket = repairs.preview(db, payload)
    inv.log_mutation(db, item, "adjustment", 0, comment="New evidence")
    db.commit()
    before = repairs.snapshot(db)
    with pytest.raises(HTTPException, match="Records changed"):
        repairs.apply(db, payload, ticket["token"], ticket["request_id"])
    assert repairs.snapshot(db) == before
    assert not db.query(InventoryOperation).all()


def test_failed_preview_rolls_back_earlier_lot_edits(db, card):
    item = stock(db, card)
    first = db.query(AcquisitionLog).one()
    second = inv.record_acquisition(db, item, 1, None)
    db.commit()
    before = repairs.snapshot(db)
    with pytest.raises(HTTPException):
        repairs.preview(db, {"action": "lot_balance", "reason": "Invalid proposal must roll back",
                             "lots": [{"id": second.id, "remaining": 0}, {"id": first.id, "remaining": 100}]})
    assert repairs.snapshot(db) == before


def test_move_cost_repairs_variant_without_inflating_purchases(db, card):
    item = stock(db, card)
    lot = db.query(AcquisitionLog).one()
    item.printing = "foil"
    db.commit()
    ticket = execute(db, "move_cost", lot_id=lot.id, inventory_id=item.id, quantity=2)
    assert ticket["before"] == ticket["after"]
    assert inv.pool_balance(db, item) == (2, 2)
    assert audit(db)["summary"]["errors"] == 0
    assert db.query(AcquisitionLog).filter_by(source_acquisition_id=lot.id).one().unit_cost == 3


def test_missing_stock_cost_can_remain_explicitly_unknown(db, card):
    item = inv.find_or_create_item(db, catalog_card_id=card.id)
    inv.apply_delta(db, item, 2)
    db.commit()
    execute(db, "add_cost_lot", inventory_id=item.id, quantity=2, cost_status="unknown", acquired_at="2026-06-01")
    assert db.query(AcquisitionLog).one().cost_status == "unknown"
    assert reports.purchase_lots(db) == []
    assert {i["code"] for i in audit(db)["issues"]} == {"zero_cost_review"}


def test_existing_lot_costs_a_past_sale_under_wrong_variant(db, card):
    item = stock(db, card, quantity=1)
    lot = db.query(AcquisitionLog).one()
    order = Order(marketplace="manual", external_order_id="old-sale", deduction_applied=True)
    db.add(order); db.flush()
    line = OrderItem(order_id=order.id, inventory_id=item.id, quantity=1, cogs=0)
    db.add(line)
    item.printing = "foil"
    inv.apply_delta(db, item, -1, cause="sale", comment="old-sale")
    db.commit()
    execute(db, "sale_allocation", line_id=line.id, source="existing", lot_id=lot.id, quantity=1, correct_printing=True)
    assert item.quantity == 0 and lot.quantity_remaining == 0 and line.cogs == 3
    assert audit(db)["summary"]["errors"] == 0
    reverse_order_deduction(db, order)
    db.commit()
    assert inv.pool_balance(db, item) == (1, 1)


def test_missing_oversold_intake_can_be_reconstructed_without_changing_stock(db, card):
    item = stock(db, card)
    order = Order(marketplace="manual", external_order_id="oversold", deduction_applied=False)
    db.add(order); db.flush()
    line = OrderItem(order_id=order.id, inventory_id=item.id, quantity=4)
    db.add(line); db.flush()
    apply_order_deduction(db, order)
    db.commit()
    ticket = execute(db, "sale_allocation", line_id=line.id, source="missing", quantity=2,
                     cost_status="unknown", acquired_at="2026-06-01", origin_kind="opening_balance")
    assert ticket["before"]["stock_units"] == ticket["after"]["stock_units"] == 0
    assert line.deducted_quantity == 4
    assert {i["code"] for i in audit(db)["issues"]} == {"zero_cost_review"}
    reverse_order_deduction(db, order); db.commit()
    assert inv.pool_balance(db, item) == (4, 4)
    assert audit(db)["summary"]["history_mismatches"] == 0


def test_cogs_repair_and_cost_verification_have_explicit_financial_effects(db, card):
    item = stock(db, card)
    lot = db.query(AcquisitionLog).one()
    order = Order(marketplace="manual", external_order_id="sale-cost")
    db.add(order); db.flush()
    line = OrderItem(order_id=order.id, inventory_id=item.id, quantity=1)
    db.add(line); db.flush()
    apply_order_deduction(db, order); db.commit()
    line.cogs = 1; db.commit()
    ticket = execute(db, "sale_costs", order_id=order.id)
    assert ticket["after"]["sale_cogs"] - ticket["before"]["sale_cogs"] == 2
    assert line.cogs == 3
    ticket = execute(db, "cost_review", lot_id=lot.id, unit_cost=4, cost_status="known", include_sales=True, correct_purchase=True)
    assert (ticket["after"]["purchase_total"], ticket["after"]["sale_cogs"], ticket["after"]["remaining_cost"]) == (8, 4, 4)
    assert audit(db)["summary"]["errors"] == 0


def test_archive_disposal_is_not_a_sale(db, card):
    item = stock(db, card)
    item.deleted = True; db.commit()
    execute(db, "archive", inventory_id=item.id, restore=False)
    assert inv.pool_balance(db, item) == (0, 0)
    assert reports.purchase_lots(db)[0]["sold"] == 0
    assert db.query(FifoConsumption).one().kind == "adjustment"


def test_records_include_sale_cost_sources(db, card):
    stock(db, card)
    assert repairs.records(db)["lots"][0]["cost_status"] == "known"


def test_all_previously_reported_workflow_regressions():
    from diagnose_reconciliation import run
    failures = [r for r in run() if not r["reconciles"]]
    assert failures == []


def test_split_pool_cost_edit_cannot_silently_reprice_a_different_bin(db, card):
    first = stock(db, card, bin="A")
    stock(db, card, bin="B")
    before = repairs.snapshot(db)
    with pytest.raises(HTTPException, match="shared across"):
        inv.set_remaining_cost(db, first, 10)
    assert repairs.snapshot(db) == before


def test_reports_keep_fractional_cost_until_aggregation(db, card):
    items = [stock(db, card, quantity=1, cost=0.0946, bin=str(n)) for n in range(20)]
    assert round(sum(r["cost_basis"] for r in inv.fifo_rollup(db, items).values()), 2) == 1.89
    assert reports.aging_report(db)["total_at_cost"] == audit(db)["summary"]["on_hand_cost"] == 1.89


def test_history_opening_and_physical_count_repairs_are_distinct(db, card):
    item = stock(db, card)
    item.quantity = 3; db.commit()
    execute(db, "journal_opening", inventory_id=item.id)
    assert audit(db)["summary"]["history_mismatches"] == 0
    execute(db, "stock_from_count", inventory_id=item.id, quantity=2)
    assert audit(db)["summary"]["errors"] == 0


def test_legacy_lot_can_be_verified_as_separate_purchase(db, card):
    stock(db, card)
    lot = db.query(AcquisitionLog).one(); lot.origin_kind = "legacy"; db.commit()
    ticket = execute(db, "origin_review", purchase_ids=[lot.id], links=[])
    assert lot.origin_kind == "purchase"
    assert ticket["before"] == ticket["after"]


def test_confirmed_free_lot_clears_unknown_cost_flag(db, card):
    stock(db, card, cost=None)
    lot = db.query(AcquisitionLog).one()
    assert any(i["code"] == "zero_cost_review" for i in audit(db)["issues"])
    execute(db, "cost_review", lot_id=lot.id, cost_status="known", unit_cost=0)
    assert not audit(db)["issues"]


def test_unlinked_outflow_classification_does_not_create_sale_cogs(db, card):
    item = stock(db, card)
    inv.apply_delta(db, item, -1); inv.consume_fifo(db, item, 1); db.commit()
    allocation = db.query(FifoConsumption).one()
    execute(db, "classify_outflow", consumption_ids=[allocation.id], kind="adjustment")
    assert allocation.kind == "adjustment"
    assert reports.purchase_lots(db)[0]["sold"] == 0
    assert not audit(db)["issues"]


def test_staged_import_cannot_be_undone_after_approval(db, card):
    from app.models import StagingItem
    from app.services import importing, staging
    batch = importing.run_import(db, filename="test.csv", content=f"ID,Qty\n{card.external_id},2\n".encode(),
                                 mapping={"ID": "external_id", "Qty": "quantity"}, value_maps=None, mode="add", to_staging=True)
    staging.approve_staging_rows(db, db.query(StagingItem).all())
    before = repairs.snapshot(db)
    with pytest.raises(ValueError, match="already approved"):
        importing.undo_import(db, batch)
    assert repairs.snapshot(db) == before


def test_additive_metadata_migration_preserves_legacy_values(tmp_path):
    from sqlalchemy import create_engine, text
    from app.db import ensure_reconciliation_schema
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE acquisition_log (id INTEGER PRIMARY KEY, quantity INTEGER, quantity_remaining INTEGER, unit_cost FLOAT)"))
        conn.execute(text("INSERT INTO acquisition_log VALUES (1, 4, 2, 7.643)"))
    ensure_reconciliation_schema(engine)
    ensure_reconciliation_schema(engine)
    with engine.connect() as conn:
        row = conn.execute(text("SELECT * FROM acquisition_log")).mappings().one()
        assert (row['quantity'], row['quantity_remaining'], row['unit_cost']) == (4, 2, 7.643)
        assert row['origin_kind'] == row['cost_status'] == 'legacy'
        assert row['original_unit_cost'] is None and row['source_acquisition_id'] is None
    engine.dispose()


def test_http_preview_apply_uses_separate_transactions(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app import db as database
    from app.models import CatalogCard
    from app.routers import misc
    engine = create_engine(f"sqlite:///{tmp_path / 'http.db'}", connect_args={"check_same_thread": False})
    database.Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    with sessions() as session:
        c = CatalogCard(game="mtg", external_id="http", name="HTTP test", set_code="TEST", collector_number="1")
        session.add(c); session.flush()
        item = stock(session, c)
        extra = inv.record_acquisition(session, item, 1, None); session.commit()
        extra_id = extra.id
    app = FastAPI(); app.include_router(misc.router)
    with TestClient(app) as client:
        payload = {"action": "lot_balance", "reason": "Review duplicate adjustment", "lots": [{"id": extra_id, "remaining": 0}]}
        response = client.post('/api/reports/reconciliation/preview', json=payload)
        assert response.status_code == 200, response.text
        ticket = response.json()
        with sessions() as session:
            assert session.get(AcquisitionLog, extra_id).quantity_remaining == 1
        assert client.post('/api/reports/reconciliation/apply', json=ticket).status_code == 200
        assert client.post('/api/reports/reconciliation/apply', json=ticket).json()['already_applied']
        assert client.get('/api/reports/reconciliation').json()['summary']['errors'] == 0
        assert len(client.get('/api/reports/reconciliation/records').json()['repairs']) == 1
    engine.dispose()
