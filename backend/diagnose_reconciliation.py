"""Exercise reconciliation invariants in disposable in-memory databases.

No production database is opened and no migrations run. False checks reproduce
known defects; they are not passing regression tests or historical repairs.
Run: python diagnose_reconciliation.py --output ../audit-output/workflows.json
"""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import AcquisitionLog, CatalogCard, CycleCountLine, InventoryItem
from app.routers import inventory as router
from app.services import importing, inventory as inv, orders, reports
from app.services.marketplaces.sync import reverse_order_deduction


@contextmanager
def scenario():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, autoflush=False) as db:
        card = CatalogCard(game="mtg", external_id="reconciliation-test", set_code="TEST",
                           name="Diagnostic card", collector_number="1")
        db.add(card); db.flush()
        item = inv.find_or_create_item(db, catalog_card_id=card.id)
        inv.add_stock(db, item, 2, 3.0)
        db.commit()
        yield db, card, item
    engine.dispose()


def balance(db):
    return {"stock": sum(i.quantity for i in db.query(InventoryItem)),
            "fifo_remaining": sum(b.quantity_remaining for b in db.query(AcquisitionLog))}


def run():
    out = []
    def check(name, expected, observed):
        out.append({"workflow": name, "expected": expected, "observed": observed,
                    "reconciles": expected == observed})
    for name, action in [
        ("manual decrease", lambda db, item: router.adjust({"adjustments": [{"inventory_id": item.id, "delta": -1}]}, db)),
        ("bulk quantity decrease", lambda db, item: router.bulk_edit({"filter": {"ids": [item.id]}, "set": {"quantity": {"subtract": 1}}}, db)),
    ]:
        with scenario() as (db, card, item):
            action(db, item)
            check(name, {"stock": 1, "fifo_remaining": 1}, balance(db))
    with scenario() as (db, card, item):
        count = router.start_cycle_count({"bin": ""}, db)
        line = db.query(CycleCountLine).filter_by(count_id=count["count_id"]).one()
        router.update_count_line(line.id, {"counted": 1}, db)
        router.approve_cycle_count(count["count_id"], db)
        check("physical count decrease", {"stock": 1, "fifo_remaining": 1}, balance(db))
    def csv_import(db, qty, mode="overwrite"):
        return importing.run_import(db, filename="diagnostic.csv",
            content=f"ID,Qty\nreconciliation-test,{qty}\n".encode(),
            mapping={"ID": "external_id", "Qty": "quantity"}, value_maps=None,
            mode=mode, to_staging=False)
    with scenario() as (db, card, item):
        csv_import(db, 1)
        check("CSV overwrite decrease", {"stock": 1, "fifo_remaining": 1}, balance(db))
    with scenario() as (db, card, item):
        batch = csv_import(db, 1, "deduction")
        importing.undo_import(db, batch)
        check("CSV deduction undo", {"stock": 2, "fifo_remaining": 2}, balance(db))
    with scenario() as (db, card, item):
        router.bulk_edit({"filter": {"ids": [item.id]}, "set": {"cost": 5, "cost_overwrite": True}}, db)
        check("bulk cost overwrite", {"stock": 2, "fifo_remaining": 2, "next_cost": 5},
              {**balance(db), "next_cost": inv.fifo_unit_cost(db, item)})
    with scenario() as (db, card, item):
        order = orders.create_manual_order(db, platform="manual",
            items=[{"inventory_id": item.id, "quantity": 4, "unit_price": 10}])
        reverse_order_deduction(db, order)
        db.commit()
        check("oversold order reversal", {"stock": 2, "fifo_remaining": 2}, balance(db))
    with scenario() as (db, card, item):
        target = inv.find_or_create_item(db, catalog_card_id=card.id, condition="LP")
        inv.pull_from_bulk(db, target, item, 1)
        db.commit()
        check("internal transfer purchase total", 6, sum(l["paid"] for l in reports.purchase_lots(db)))
    with scenario() as (db, card, item):
        router.update(item.id, {"printing": "foil"}, db)
        check("printing reclassification purchase total", 6, sum(l["paid"] for l in reports.purchase_lots(db)))
    with scenario() as (db, card, item):
        inv.add_stock(db, item, 1, 9)
        db.commit()
        check("aging report mixed-cost value", 15, reports.aging_report(db)["total_at_cost"])
    with scenario() as (db, card, item):
        other = inv.find_or_create_item(db, catalog_card_id=card.id, bin="B")
        inv.add_stock(db, other, 1, 9)
        db.commit()
        full = inv.fifo_rollup(db, [item, other])[other.id]["cost_basis"]
        alone = inv.fifo_rollup(db, [other])[other.id]["cost_basis"]
        check("filtered row cost stability", full, alone)
    with scenario() as (db, card, item):
        item.language = "ja"
        db.commit()
        importing.run_import(db, filename="language.csv",
            content=b"ID,Qty,Lang\nreconciliation-test,1,en\n",
            mapping={"ID": "external_id", "Qty": "quantity", "Lang": "language"},
            value_maps=None, mode="deduction", to_staging=False)
        check("CSV deduction language isolation", 2, item.quantity)
    with scenario() as (db, card, item):
        other = inv.find_or_create_item(db, catalog_card_id=card.id, bin="B")
        inv.add_stock(db, other, 1, 9)
        db.commit()
        batch = csv_import(db, 3, "deduction")
        importing.undo_import(db, batch)
        check("CSV deduction undo across bins", {"first": 2, "second": 1, "fifo_remaining": 3},
              {"first": item.quantity, "second": other.quantity,
               "fifo_remaining": balance(db)["fifo_remaining"]})
    with scenario() as (db, card, item):
        batch = csv_import(db, "1.9", "add")
        check("fractional CSV quantity rejected", "error", batch.rows[0].status)
    with scenario() as (db, card, item):
        inv.apply_delta(db, item, -1, cause="sale")
        inv.consume_fifo(db, item, 1)
        inv.reduce_cost_basis(db, item, 1)
        db.commit()
        check("purchase total after partial supplier refund", 5,
              sum(l["paid"] for l in reports.purchase_lots(db)))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    for row in result:
        print(f"{'PASS' if row['reconciles'] else 'DEFECT'}: {row['workflow']}: {row['observed']} (expected {row['expected']})")
    print(f"{sum(not r['reconciles'] for r in result)}/{len(result)} workflows violate their invariant.")
    raise SystemExit(1 if any(not row["reconciles"] for row in result) else 0)


if __name__ == "__main__":
    main()
