"""Read-only reconciliation of stock, its journal, FIFO balances and sales.

An agreement here is an internal consistency check, not invoice verification.
Never repair a balance by silently changing quantities, historical costs or COGS.
"""
from collections import defaultdict
from datetime import datetime, timezone
import math

from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload

from ..models import (
    AcquisitionLog, CatalogCard, FifoConsumption, InventoryItem, InventoryLog,
    Order,
)
from .inventory import pool_key, item_description, fifo_rollup


def audit(db) -> dict:
    items = db.execute(select(InventoryItem).options(
        joinedload(InventoryItem.card))).scalars().all()
    batches = db.execute(select(AcquisitionLog)).scalars().all()
    logs = db.execute(select(InventoryLog).order_by(InventoryLog.id)).scalars().all()
    consumptions = db.execute(select(FifoConsumption)).scalars().all()
    orders = db.execute(select(Order).options(selectinload(Order.items))).scalars().all()
    items_by_id = {i.id: i for i in items}
    batches_by_id = {b.id: b for b in batches}
    order_ids = {o.id for o in orders}
    card_ids = {b.catalog_card_id for b in batches if b.catalog_card_id}
    cards = {c.id: c for c in db.execute(select(CatalogCard).where(
        CatalogCard.id.in_(card_ids))).scalars()} if card_ids else {}
    pools, pool_batches, journal = defaultdict(list), defaultdict(list), defaultdict(list)
    by_order, by_batch = defaultdict(list), defaultdict(list)
    issues = []

    def issue(code, message, *, severity="error", **facts):
        issues.append({"code": code, "severity": severity, "message": message, **facts})

    for log in logs:
        journal[log.inventory_id].append(log)
        if log.inventory_id is not None and log.inventory_id not in items_by_id:
            issue("orphan_history", f"Stock event #{log.id} refers to a missing row.",
                  log_ids=[log.id], inventory_ids=[log.inventory_id])
    for item in items:
        pools[pool_key(item)].append(item)
        net = sum(l.quantity_delta for l in journal[item.id])
        if item.quantity != net:
            issue("stock_history", f"{item_description(item)}: stock {item.quantity}, history net {net}.",
                  inventory_ids=[item.id], stock=item.quantity, history_net=net)
        if item.quantity < 0:
            issue("negative_stock", f"Inventory #{item.id} has negative stock.", inventory_ids=[item.id])
    for batch in batches:
        pool_batches[pool_key(batch)].append(batch)
        if (batch.quantity < 0 or batch.quantity_remaining < 0
                or batch.quantity_remaining > batch.quantity
                or not math.isfinite(batch.unit_cost) or batch.unit_cost < 0):
            issue("invalid_lot", f"Acquisition lot #{batch.id} has invalid quantity or cost.",
                  acquisition_ids=[batch.id])
    pool_rows = []
    for key in sorted(pools.keys() | pool_batches.keys(), key=str):
        rows, lots = pools[key], pool_batches[key]
        stock = sum(i.quantity for i in rows)
        active = sum(i.quantity for i in rows if not i.deleted)
        remaining = sum(b.quantity_remaining for b in lots)
        card = cards.get(key[0])
        label = (f"{card.name} [{card.set_code} {card.collector_number}] {key[2]} {key[3]}"
                 if card else item_description(rows[0]) if rows else f"Custom SKU #{key[1]} {key[2]} {key[3]}")
        record = {"identity": list(key), "label": label, "inventory_ids": [i.id for i in rows],
                  "active_stock": active, "archived_stock": stock - active, "stock": stock,
                  "lot_remaining": remaining, "difference": remaining - stock,
                  "remaining_cost": round(sum(b.quantity_remaining * b.unit_cost for b in lots), 4),
                  "acquisition_ids": [b.id for b in lots]}
        pool_rows.append(record)
        if remaining != stock:
            issue("fifo_balance", f"{label}: stock {stock}, FIFO remaining {remaining}.", **record)
        elif stock > active:
            issue("archived_stock", f"{label}: {stock - active} units are archived, with cost still retained.",
                  severity="warning", **record)
    for consumption in consumptions:
        by_order[consumption.order_id].append(consumption)
        by_batch[consumption.acquisition_id].append(consumption)
        if consumption.acquisition_id not in batches_by_id:
            issue("orphan_consumption", f"FIFO allocation #{consumption.id} has no acquisition lot.",
                  consumption_ids=[consumption.id])
        if consumption.order_id is not None and consumption.order_id not in order_ids:
            issue("orphan_order", f"FIFO allocation #{consumption.id} has no order.",
                  consumption_ids=[consumption.id], order_ids=[consumption.order_id])
    for batch in batches:
        used = sum(c.quantity for c in by_batch[batch.id])
        if used + batch.quantity_remaining > batch.quantity:
            issue("lot_overallocated", f"Lot #{batch.id}: remaining plus allocated exceeds its original quantity.",
                  acquisition_ids=[batch.id], quantity=batch.quantity,
                  remaining=batch.quantity_remaining, allocated=used)
    migrated, unlinked, uncovered = [], [], []
    for order in orders:
        allocations = by_order[order.id]
        line_cost = sum(l.cogs for l in order.items)
        fifo_cost = sum(c.quantity * c.unit_cost for c in allocations)
        if allocations and abs(line_cost - fifo_cost) > 0.005:
            issue("order_cogs", f"Order #{order.id}: line COGS ${line_cost:.4f}, FIFO COGS ${fifo_cost:.4f}.",
                  order_ids=[order.id], line_cogs=round(line_cost, 4), fifo_cogs=round(fifo_cost, 4))
        if not allocations and (order.external_order_id or "").startswith("airtable-"):
            migrated.append({"order_id": order.id, "cogs": round(line_cost, 4)})
        elif not allocations and line_cost:
            unlinked.append({"order_id": order.id, "cogs": round(line_cost, 4)})
        if not order.deduction_applied:
            if allocations:
                issue("reversed_order_allocations", f"Order #{order.id} is marked reversed but still consumes FIFO lots.",
                      order_ids=[order.id])
            continue
        # Migrated sales have authoritative imported COGS, not live stock deductions.
        if (order.external_order_id or "").startswith("airtable-"):
            continue
        expected, allocated = defaultdict(int), defaultdict(int)
        for line in order.items:
            item = items_by_id.get(line.inventory_id)
            if item:
                expected[pool_key(item)] += line.quantity
        for consumption in allocations:
            batch = batches_by_id.get(consumption.acquisition_id)
            if batch:
                allocated[pool_key(batch)] += consumption.quantity
        for key, qty in expected.items():
            if allocated[key] != qty:
                uncovered.append({"order_id": order.id, "identity": list(key),
                                  "sold_quantity": qty, "allocated_quantity": allocated[key]})
    if unlinked:
        issue("unlinked_cogs", f"{len(unlinked)} non-migrated orders have COGS without FIFO links.",
              severity="warning", orders=unlinked)
    if uncovered:
        issue("sale_allocation", f"{len(uncovered)} order/pool combinations do not match their sold quantities; investigate missing cost history or reclassification.",
              severity="warning", orders=uncovered)
    unattached = by_order[None]
    if unattached:
        issue("unclassified_outflow", f"{len(unattached)} FIFO outflows have no order link; they cannot prove a sale.",
              severity="warning", consumption_ids=[c.id for c in unattached],
              quantity=sum(c.quantity for c in unattached),
              cost=round(sum(c.quantity * c.unit_cost for c in unattached), 4))
    transfers = [l for l in logs if l.cause == "pull_from_bulk" and l.quantity_delta > 0]
    if transfers:
        issue("purchase_provenance", "Purchase totals are reconstructed from acquisition lots. Bulk-to-card transfers create additional lots without purchase ancestry, so those totals can double-count the same purchase.",
              severity="warning", transfer_events=len(transfers),
              transferred_units=sum(l.quantity_delta for l in transfers))
    zero = [b for b in batches if b.unit_cost == 0 and b.quantity_remaining > 0]
    if zero:
        issue("zero_cost_review", "Remaining $0 lots do not distinguish verified zero cost from an unknown cost default.",
              severity="warning", acquisition_ids=[b.id for b in zero],
              quantity=sum(b.quantity_remaining for b in zero))
    active = [i for i in items if not i.deleted]
    rollup = fifo_rollup(db, active)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "summary": {"inventory_rows": len(items), "active_rows": len(active),
                    "active_units": sum(i.quantity for i in active), "fifo_pools": len(pool_rows),
                    "history_mismatches": sum(i["code"] == "stock_history" for i in issues),
                    "fifo_mismatches": sum(i["code"] == "fifo_balance" for i in issues),
                    "errors": sum(i["severity"] == "error" for i in issues),
                    "warnings": sum(i["severity"] == "warning" for i in issues),
                    "on_hand_cost": round(sum(r["cost_basis"] for r in rollup.values()), 2),
                    "orders_checked": len(orders), "migrated_orders_without_lot_links": len(migrated),
                    "migrated_cogs": round(sum(o["cogs"] for o in migrated), 2)},
        "issues": issues, "pools": pool_rows,
        "limitations": ["Balances reconcile internal records; invoices, bank records and physical stock require independent verification.",
                        "FIFO pools are shared across bins and languages; archived stock is included in pool reconciliation.",
                        "Legacy records lack durable purchase/transfer lineage and cost-verification status.",
                        "Migrated historical sales without FIFO links are reported separately; their COGS is not reconstructed or changed."],
    }
