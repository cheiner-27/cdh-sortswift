"""Reviewed, atomic, stale-safe repairs. Preview and apply execute the same code."""
from datetime import datetime, timezone
import hashlib
import json
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import Float, select

from ..models import AcquisitionLog, CatalogCard, FifoConsumption, InventoryItem, InventoryLog, InventoryOperation, OrderItem, Order
from ..validate import whole, money, choice
from . import inventory as inv
from . import reports


def get(db, model, identifier):
    identifier = whole(identifier, "record ID", min_value=1)
    record = db.get(model, identifier)
    if record is None:
        raise HTTPException(404, "The selected record no longer exists")
    return record


def stamp(value):
    return inv._as_utc(value).isoformat() if isinstance(value, datetime) else value


def snapshot(db):
    db.flush()
    return {model.__tablename__: {
        str(row.id): {column.name: (float(getattr(row, column.name))
            if isinstance(column.type, Float) and getattr(row, column.name) is not None
            else stamp(getattr(row, column.name))) for column in model.__table__.columns}
        for row in db.execute(select(model).order_by(model.id)).scalars()}
        for model in (InventoryItem, AcquisitionLog, FifoConsumption, OrderItem, InventoryLog)}


def fingerprint(state, payload):
    return hashlib.sha256(json.dumps([state, payload], sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def summary(db):
    return {
        "stock_units": sum(i.quantity for i in db.execute(select(InventoryItem)).scalars()),
        "lot_units": sum(b.quantity_remaining for b in db.execute(select(AcquisitionLog)).scalars()),
        "remaining_cost": round(sum(b.quantity_remaining * b.unit_cost for b in db.execute(select(AcquisitionLog)).scalars()), 4),
        "sale_cogs": round(sum(l.cogs for l in db.execute(select(OrderItem)).scalars()), 4),
        "purchase_total": round(sum(l["paid"] for l in reports.purchase_lots(db)), 2),
    }


def difference(before, after):
    out = []
    for table in before:
        for identifier in before[table].keys() | after[table].keys():
            old, new = before[table].get(identifier), after[table].get(identifier)
            if old != new:
                out.append({"table": table, "id": int(identifier), "before": old, "after": new})
    return sorted(out, key=lambda c: (c["table"], c["id"]))


def _line_allocations(db, line):
    rows = db.execute(select(FifoConsumption).where(FifoConsumption.order_id == line.order_id)).scalars().all()
    item = db.get(InventoryItem, line.inventory_id) if line.inventory_id else None
    same = [l for l in line.order.items if l.inventory_id and item and
            inv.pool_key(get(db, InventoryItem, l.inventory_id)) == inv.pool_key(item)]
    out = []
    for row in rows:
        if row.order_item_id == line.id:
            out.append(row)
        elif row.order_item_id is None and item:
            batch = get(db, AcquisitionLog, row.acquisition_id)
            if inv.pool_key(batch) == inv.pool_key(item):
                if len(same) != 1:
                    raise HTTPException(409, "Several sale lines share this cost pool; allocations need an explicit line assignment before changing COGS.")
                out.append(row)
    return out


def _recalculate_line(db, line):
    allocations = _line_allocations(db, line)
    if sum(a.quantity for a in allocations) != line.quantity:
        raise HTTPException(409, "Cost allocations do not cover the sold quantity. Resolve missing sale costs first.")
    for allocation in allocations:
        allocation.order_item_id = line.id
    line.cogs = round(sum(a.quantity * a.unit_cost for a in allocations), 4)


def _date(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        raise HTTPException(400, "Enter the acquisition date as YYYY-MM-DD")


def _execute(db, payload):
    action = payload.get("action")
    reason = str(payload.get("reason") or "").strip()
    if len(reason) < 5:
        raise HTTPException(400, "Record a short explanation or source reference for this correction")
    touched = set()
    if action == "lot_balance":
        changes = payload.get("lots", [])
        if not changes:
            raise HTTPException(400, "Choose the lot balances to correct")
        keys = set()
        for change in changes:
            batch = get(db, AcquisitionLog, change.get("id"))
            keys.add(inv.pool_key(batch))
            remaining = whole(change.get("remaining"), "remaining units")
            consumed = sum(c.quantity for c in db.execute(select(FifoConsumption).where(
                FifoConsumption.acquisition_id == batch.id)).scalars())
            children = sum(b.quantity for b in db.execute(select(AcquisitionLog).where(
                AcquisitionLog.source_acquisition_id == batch.id)).scalars())
            if remaining > batch.quantity - consumed - children:
                raise HTTPException(409, "Remaining units would overlap units already sold or transferred")
            batch.quantity_remaining = remaining
        db.flush()
        for key in keys:
            items = [i for i in db.execute(select(InventoryItem)).scalars() if inv.pool_key(i) == key]
            lots = [b for b in db.execute(select(AcquisitionLog)).scalars() if inv.pool_key(b) == key]
            if sum(i.quantity for i in items) != sum(b.quantity_remaining for b in lots):
                raise HTTPException(400, "The proposed remaining lot units must equal stock for each affected card/condition/printing")
            touched.update(i.id for i in items)
    elif action == "stock_from_count":
        item = get(db, InventoryItem, payload.get("inventory_id"))
        counted = whole(payload.get("quantity"), "physically counted quantity")
        stock, remaining = inv.pool_balance(db, item)
        if stock - item.quantity + counted != remaining:
            raise HTTPException(400, "This stock correction must reconcile with the existing cost lots. Otherwise correct the lot breakdown first.")
        inv.apply_delta(db, item, counted - item.quantity, cause="reconciliation", comment=reason)
        touched.add(item.id)
    elif action == "move_cost":
        source = get(db, AcquisitionLog, payload.get("lot_id"))
        target = get(db, InventoryItem, payload.get("inventory_id"))
        qty = whole(payload.get("quantity"), "units to move", min_value=1)
        if target.deleted or inv.pool_key(source) == inv.pool_key(target):
            raise HTTPException(400, "Choose a different active card variant as the destination")
        if (source.catalog_card_id, source.custom_sku_id) != (target.catalog_card_id, target.custom_sku_id):
            raise HTTPException(400, "Printing/condition repairs must remain on the same card")
        source_stock = sum(i.quantity for i in db.execute(select(InventoryItem)).scalars() if inv.pool_key(i) == inv.pool_key(source))
        source_remaining = sum(b.quantity_remaining for b in db.execute(select(AcquisitionLog)).scalars() if inv.pool_key(b) == inv.pool_key(source))
        target_stock, target_remaining = inv.pool_balance(db, target)
        if qty > min(source.quantity_remaining, source_remaining - source_stock, target_stock - target_remaining):
            raise HTTPException(409, "Move only excess costed units into an existing stock shortfall")
        inv.transfer_cost_slice(db, source, target, qty)
        touched.add(target.id)
    elif action == "add_cost_lot":
        item = get(db, InventoryItem, payload.get("inventory_id"))
        stock, remaining = inv.pool_balance(db, item)
        qty = whole(payload.get("quantity"), "missing cost units", min_value=1)
        if qty != stock - remaining:
            raise HTTPException(409, "The new cost lot must cover exactly the existing stock shortfall")
        status = choice(payload.get("cost_status"), "cost status", ["known", "estimated", "unknown"])
        cost = None if status == "unknown" else money(payload.get("unit_cost"), "unit cost")
        inv.record_acquisition(db, item, qty, cost, _date(payload.get("acquired_at")),
                               origin_kind="opening_balance", cost_status=status)
        touched.add(item.id)
    elif action == "sale_costs":
        order = get(db, Order, payload.get("order_id"))
        if not order.deduction_applied:
            raise HTTPException(409, "This sale is reversed; do not apply COGS to it")
        for line in order.items:
            if line.inventory_id:
                _recalculate_line(db, line)
                touched.add(line.inventory_id)
    elif action == "sale_allocation":
        line = get(db, OrderItem, payload.get("line_id"))
        item = get(db, InventoryItem, line.inventory_id)
        if not line.order.deduction_applied:
            raise HTTPException(409, "This sale is reversed")
        existing = _line_allocations(db, line)
        qty = whole(payload.get("quantity"), "missing sold units", min_value=1)
        if qty != line.quantity - sum(a.quantity for a in existing):
            raise HTTPException(409, "Cover exactly the sale's missing cost units")
        if payload.get("source") == "existing":
            batch = get(db, AcquisitionLog, payload.get("lot_id"))
            if (batch.catalog_card_id, batch.custom_sku_id) != (item.catalog_card_id, item.custom_sku_id):
                raise HTTPException(400, "The cost lot must belong to the sold card")
            reclassify = inv.pool_key(batch) != inv.pool_key(item)
            if reclassify:
                if not payload.get("correct_printing"):
                    raise HTTPException(400, "Confirm the lot's printing/condition correction explicitly")
            stock = sum(i.quantity for i in db.execute(select(InventoryItem)).scalars() if inv.pool_key(i) == inv.pool_key(batch))
            remaining = sum(b.quantity_remaining for b in db.execute(select(AcquisitionLog)).scalars() if inv.pool_key(b) == inv.pool_key(batch))
            if qty > batch.quantity_remaining or qty > remaining - stock:
                raise HTTPException(409, "These lot units already support on-hand stock; they cannot also cost a past sale")
            if reclassify:
                batch = inv.transfer_cost_slice(db, batch, item, qty)
        elif payload.get("source") == "missing":
            status = choice(payload.get("cost_status"), "cost status", ["known", "estimated", "unknown"])
            cost = None if status == "unknown" else money(payload.get("unit_cost"), "unit cost")
            origin = choice(payload.get("origin_kind", "opening_balance"), "origin", ["purchase", "opening_balance"])
            if origin == "purchase" and status != "known":
                raise HTTPException(400, "A reconstructed purchase requires a known cost and source evidence in the explanation")
            batch = inv.record_acquisition(db, item, qty, cost, _date(payload.get("acquired_at")),
                                           origin_kind=origin, cost_status=status)
        else:
            raise HTTPException(400, "Choose an existing lot or reconstruct the missing cost")
        batch.quantity_remaining -= qty
        db.add(FifoConsumption(acquisition_id=batch.id, order_id=line.order_id, order_item_id=line.id,
                               quantity=qty, unit_cost=batch.unit_cost, kind="sale"))
        db.flush()
        _recalculate_line(db, line)
        # Missing legacy intake/deduction is recorded as a paired correction;
        # actual stock stays unchanged, and future return quantities are explicit.
        if line.deducted_quantity is None:
            history = [h for h in db.execute(select(InventoryLog).where(InventoryLog.inventory_id == item.id)).scalars()
                       if line.order.external_order_id in (h.comment or "")]
            actual = max(0, -sum(h.quantity_delta for h in history))
        else:
            actual = line.deducted_quantity
        missing_stock_events = max(0, line.quantity - actual)
        if missing_stock_events:
            inv.log_mutation(db, item, "adjustment", missing_stock_events, cause="reconciliation",
                             comment=f"Reconstructed historical intake for {line.order.external_order_id}: {reason}")
            inv.log_mutation(db, item, "adjustment", -missing_stock_events, cause="reconciliation",
                             comment=f"Reconstructed historical sale for {line.order.external_order_id}: {reason}")
        line.deducted_quantity = line.quantity
        touched.add(item.id)
    elif action == "cost_review":
        batch = get(db, AcquisitionLog, payload.get("lot_id"))
        status = choice(payload.get("cost_status"), "cost status", ["known", "estimated", "unknown"])
        cost = None if status == "unknown" else money(payload.get("unit_cost"), "unit cost")
        allocations = db.execute(select(FifoConsumption).where(FifoConsumption.acquisition_id == batch.id)).scalars().all()
        if allocations and not payload.get("include_sales"):
            raise HTTPException(409, "This lot has past allocations. Confirm their correction explicitly or choose an unconsumed lot.")
        old = batch.unit_cost
        if batch.original_unit_cost is None:
            batch.original_unit_cost = old
        batch.unit_cost, batch.cost_status = cost or 0.0, status
        if payload.get("correct_purchase"):
            if batch.source_acquisition_id:
                raise HTTPException(409, "Correct the original purchase lot, not a transferred slice")
            if status != "known":
                raise HTTPException(400, "An original purchase correction requires a verified cost")
            batch.original_unit_cost = cost
        for allocation in allocations:
            allocation.unit_cost = batch.unit_cost
        db.flush()
        for order_id in {a.order_id for a in allocations if a.order_id}:
            for line in get(db, Order, order_id).items:
                if line.inventory_id:
                    _recalculate_line(db, line)
                    touched.add(line.inventory_id)
    elif action == "origin_review":
        purchase_ids = set(payload.get("purchase_ids", []))
        if purchase_ids & {link.get("lot_id") for link in payload.get("links", [])}:
            raise HTTPException(400, "A lot cannot be both a purchase and a transfer")
        for identifier in purchase_ids:
            batch = get(db, AcquisitionLog, identifier)
            if batch.source_acquisition_id or batch.origin_kind != "legacy":
                raise HTTPException(409, "Only an unclassified original lot can be verified as a purchase")
            batch.origin_kind = "purchase"
            if batch.original_unit_cost is None:
                batch.original_unit_cost = batch.unit_cost
        for link in payload.get("links", []):
            batch = get(db, AcquisitionLog, link.get("lot_id"))
            parent = get(db, AcquisitionLog, link.get("parent_id"))
            if parent.id >= batch.id or parent.unit_cost != batch.unit_cost or parent.acquired_at != batch.acquired_at:
                raise HTTPException(409, "A transfer link must reference an earlier lot with the same acquisition date and unit cost")
            if inv.pool_key(batch) == inv.pool_key(parent):
                raise HTTPException(400, "Choose the original source holding, not the same card pool")
            if batch.origin_kind not in ("legacy", "transfer"):
                raise HTTPException(409, "This lot already has a different documented origin")
            allocated = sum(b.quantity for b in db.execute(select(AcquisitionLog).where(
                AcquisitionLog.source_acquisition_id == parent.id, AcquisitionLog.id != batch.id)).scalars())
            sold = sum(a.quantity for a in db.execute(select(FifoConsumption).where(FifoConsumption.acquisition_id == parent.id)).scalars())
            if batch.quantity + allocated + sold + parent.quantity_remaining > parent.quantity:
                raise HTTPException(409, "The source lot has insufficient historical transferred units for this link")
            batch.source_acquisition_id, batch.origin_kind = parent.id, "transfer"
            db.flush()
    elif action == "archive":
        item = get(db, InventoryItem, payload.get("inventory_id"))
        if not item.deleted:
            raise HTTPException(409, "This row is already active")
        if payload.get("restore"):
            item.deleted = False
        else:
            inv.adjust_stock(db, item, -item.quantity, cause="reconciliation", comment=reason)
        touched.add(item.id)
    elif action == "classify_outflow":
        kind = choice(payload.get("kind"), "outflow type", ["adjustment", "supplier_return", "sale_unlinked"])
        for identifier in payload.get("consumption_ids", []):
            allocation = get(db, FifoConsumption, identifier)
            if allocation.order_id:
                raise HTTPException(409, "This allocation is linked to an order")
            allocation.kind = kind
    elif action == "journal_opening":
        item = get(db, InventoryItem, payload.get("inventory_id"))
        net = sum(h.quantity_delta for h in db.execute(select(InventoryLog).where(InventoryLog.inventory_id == item.id)).scalars())
        inv.log_mutation(db, item, "adjustment", item.quantity - net, cause="reconciliation",
                         comment=f"Documented opening/history correction, stock unchanged: {reason}")
    else:
        raise HTTPException(400, "Choose a supported repair action")
    for identifier in touched:
        inv.log_mutation(db, get(db, InventoryItem, identifier), "adjustment", 0,
                         cause="reconciliation", comment=reason)
    db.flush()


def preview(db, payload):
    before = snapshot(db)
    before_summary = summary(db)
    token = fingerprint(before, payload)
    savepoint = db.begin_nested()
    try:
        _execute(db, payload)
        after = snapshot(db)
        result = {"before": before_summary, "after": summary(db), "changes": difference(before, after)}
    finally:
        savepoint.rollback()
        db.expire_all()
    if not result["changes"]:
        raise HTTPException(400, "This proposal changes nothing")
    return {**result, "token": token, "request_id": str(uuid4()), "payload": payload}


def apply(db, payload, token, request_id):
    if not isinstance(request_id, str) or len(request_id) != 36:
        raise HTTPException(400, "Preview this repair before applying it")
    previous = db.execute(select(InventoryOperation).where(InventoryOperation.request_id == request_id)).scalar_one_or_none()
    if previous:
        if previous.details.get("payload") != payload:
            raise HTTPException(409, "This repair identifier was already used for a different proposal")
        return {"applied": True, "already_applied": True, "operation_id": previous.id}
    before = snapshot(db)
    if fingerprint(before, payload) != token:
        raise HTTPException(409, "Records changed since the preview. Preview again before applying.")
    with db.begin_nested():
        before_summary = summary(db)
        _execute(db, payload)
        details = {"payload": payload, "before": before_summary, "after": summary(db),
                   "changes": difference(before, snapshot(db))}
        event = InventoryOperation(request_id=request_id, kind="reconciliation",
                                   reason=payload["reason"], details=details)
        db.add(event)
        db.flush()
    db.commit()
    return {"applied": True, "already_applied": False, "operation_id": event.id}


def records(db):
    items = db.execute(select(InventoryItem).order_by(InventoryItem.id)).scalars().all()
    batches = db.execute(select(AcquisitionLog).order_by(AcquisitionLog.id)).scalars().all()
    histories = db.execute(select(InventoryLog).where(InventoryLog.cause == "pull_from_bulk")).scalars().all()
    transfer_keys = {inv.pool_key(i) for i in items if i.id in {h.inventory_id for h in histories}}
    labels = {inv.pool_key(i): inv.item_description(i) for i in items}
    missing_cards = {b.catalog_card_id for b in batches if b.catalog_card_id and inv.pool_key(b) not in labels}
    cards = {c.id: c for c in db.execute(select(CatalogCard).where(CatalogCard.id.in_(missing_cards))).scalars()}
    for batch in batches:
        card = cards.get(batch.catalog_card_id)
        if card:
            labels[inv.pool_key(batch)] = f"{card.name} [{card.set_code} {card.collector_number}] {batch.condition} {batch.printing}"
    candidates = []
    for batch in batches:
        if batch.origin_kind != "legacy" or inv.pool_key(batch) not in transfer_keys:
            continue
        parents = [b.id for b in batches if b.custom_sku_id and b.id < batch.id
                   and b.acquired_at == batch.acquired_at and b.unit_cost == batch.unit_cost
                   and b.quantity >= batch.quantity and inv.pool_key(b) != inv.pool_key(batch)]
        if parents:
            candidates.append({"lot_id": batch.id, "parent_ids": parents})
    order_rows = []
    for order in db.execute(select(Order).order_by(Order.id)).scalars():
        for line in order.items:
            if not line.inventory_id:
                continue
            try:
                allocations = _line_allocations(db, line)
                allocated = sum(a.quantity for a in allocations)
            except HTTPException:
                allocated = None
            order_rows.append({"id": line.id, "order_id": order.id, "inventory_id": line.inventory_id,
                               "label": line.description or f"Sale line {line.id}", "quantity": line.quantity,
                               "cogs": line.cogs, "allocated": allocated,
                               "allocation_lot_ids": [a.acquisition_id for a in allocations] if allocated is not None else [],
                               "date": order.ordered_at.date().isoformat(), "active": order.deduction_applied})
    return {
        "items": [{"id": i.id, "label": inv.item_description(i), "quantity": i.quantity,
                   "identity": list(inv.pool_key(i)), "deleted": i.deleted} for i in items],
        "lots": [{"id": b.id, "label": labels.get(inv.pool_key(b), f"Card {b.catalog_card_id or b.custom_sku_id} {b.condition} {b.printing}"),
                  "identity": list(inv.pool_key(b)), "quantity": b.quantity, "remaining": b.quantity_remaining,
                  "unit_cost": b.unit_cost, "date": b.acquired_at.date().isoformat(), "origin_kind": b.origin_kind,
                  "parent_id": b.source_acquisition_id, "cost_status": b.cost_status} for b in batches],
        "sale_lines": order_rows, "transfer_candidates": candidates,
        "outflows": [{"id": a.id, "lot_id": a.acquisition_id, "quantity": a.quantity, "unit_cost": a.unit_cost,
                      "date": a.consumed_at.isoformat()} for a in db.execute(select(FifoConsumption).where(FifoConsumption.order_id == None)).scalars()],
        "repairs": [{"id": e.id, "reason": e.reason, "date": e.created_at.isoformat(),
                     "details": e.details} for e in db.execute(select(InventoryOperation).where(
                         InventoryOperation.kind == "reconciliation").order_by(InventoryOperation.id.desc()).limit(100)).scalars()],
    }
