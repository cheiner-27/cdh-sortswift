"""Inventory: filtering, adjustments, bulk edit, splits, merges, cycle counts."""
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    CatalogCard, CycleCount, CycleCountLine, InventoryItem, InventoryLog, utcnow,
)
from ..services import inventory as inv_svc
from ..services import reports as report_svc
from .serializers import inventory_dict

router = APIRouter(prefix="/api/inventory", tags=["inventory"])


def filter_items(db: Session, params: dict) -> list[InventoryItem]:
    q = select(InventoryItem)
    if not params.get("include_deleted"):
        q = q.where(InventoryItem.deleted == False)  # noqa: E712
    if params.get("deleted_only"):
        q = select(InventoryItem).where(InventoryItem.deleted == True)  # noqa: E712
    if params.get("condition"):
        q = q.where(InventoryItem.condition == params["condition"])
    if params.get("printing"):
        q = q.where(InventoryItem.printing == params["printing"])
    if params.get("bin") is not None and params.get("bin") != "":
        q = q.where(InventoryItem.bin == ("" if params["bin"] == "(unassigned)" else params["bin"]))
    if params.get("comment"):
        q = q.where(InventoryItem.comment.ilike(f"%{params['comment']}%"))
    if params.get("in_stock_only"):
        q = q.where(InventoryItem.quantity > 0)
    if params.get("ids"):
        q = q.where(InventoryItem.id.in_(params["ids"]))
    if params.get("game") or params.get("set_code") or params.get("rarity") or params.get("q"):
        q = q.join(CatalogCard, InventoryItem.catalog_card_id == CatalogCard.id, isouter=True)
        if params.get("game"):
            q = q.where(CatalogCard.game == params["game"])
        if params.get("set_code"):
            q = q.where(CatalogCard.set_code == params["set_code"])
        if params.get("rarity"):
            q = q.where(CatalogCard.rarity.ilike(params["rarity"]))
        if params.get("q"):
            q = q.where(or_(CatalogCard.name.ilike(f"%{params['q']}%"),
                            InventoryItem.comment.ilike(f"%{params['q']}%")))
    items = db.execute(q.order_by(InventoryItem.id.desc())).scalars().all()

    # Post-filters that need computed values
    if params.get("listing_status") and params.get("marketplace"):
        mk, st = params["marketplace"], params["listing_status"]
        items = [i for i in items
                 if any(l.marketplace == mk and l.status == st for l in i.listings)
                 or (st == "unlisted" and not any(l.marketplace == mk for l in i.listings))]
    if params.get("price_min") is not None:
        items = [i for i in items if (i.price_override or i.current_price or 0) >= params["price_min"]]
    if params.get("price_max") is not None:
        items = [i for i in items if (i.price_override or i.current_price or 0) <= params["price_max"]]
    if params.get("age_min_days") is not None:
        items = [i for i in items
                 if (inv_svc.inventory_age_days(db, i) or -1) >= params["age_min_days"]]
    return items


@router.post("/search")
def search(params: dict = Body(default={}), db: Session = Depends(get_db)):
    items = filter_items(db, params)
    limit = params.get("limit", 500)
    offset = params.get("offset", 0)
    page = items[offset:offset + limit]
    with_age = params.get("with_age", False)
    return {
        "total": len(items),
        "items": [inventory_dict(
            i,
            age_days=inv_svc.inventory_age_days(db, i) if with_age else None,
            fifo_cost=inv_svc.fifo_unit_cost(db, i) if with_age else None,
        ) for i in page],
    }


@router.get("/bins")
def bins(db: Session = Depends(get_db)):
    return report_svc.location_summary(db)


@router.get("/{item_id}")
def detail(item_id: int, db: Session = Depends(get_db)):
    from ..models import AcquisitionLog
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(404)
    history = db.execute(select(InventoryLog).where(
        InventoryLog.inventory_id == item_id
    ).order_by(InventoryLog.id.desc())).scalars().all()
    d = inventory_dict(item, age_days=inv_svc.inventory_age_days(db, item),
                       fifo_cost=inv_svc.fifo_unit_cost(db, item))
    # FIFO acquisition lots for this identity (card/sku + condition + printing),
    # each with its own acquisition date and cost — oldest first (consumed first).
    lots = db.execute(select(AcquisitionLog).where(
        AcquisitionLog.catalog_card_id == item.catalog_card_id,
        AcquisitionLog.custom_sku_id == item.custom_sku_id,
        AcquisitionLog.condition == item.condition,
        AcquisitionLog.printing == item.printing,
    ).order_by(AcquisitionLog.acquired_at.asc(), AcquisitionLog.id.asc())).scalars().all()
    d["acquisitions"] = [{
        "id": a.id,
        "acquired_at": a.acquired_at.isoformat() if a.acquired_at else None,
        "quantity": a.quantity, "quantity_remaining": a.quantity_remaining,
        "unit_cost": a.unit_cost,
    } for a in lots]
    d["history"] = [{
        "id": h.id, "type": h.type, "quantity_delta": h.quantity_delta,
        "price_at": h.price_at, "cost_at": h.cost_at,
        "bin_before": h.bin_before, "bin_after": h.bin_after,
        "comment": h.comment, "cause": h.cause, "source": h.source,
        "created_at": h.created_at.isoformat() if h.created_at else None,
    } for h in history]
    return d


@router.patch("/{item_id}")
def update(item_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(404)
    if "bin" in payload and payload["bin"] != item.bin:
        inv_svc.transfer_bin(db, item, payload["bin"], comment=payload.get("comment", ""))
    old_condition, old_printing = item.condition, item.printing
    for f in ("condition", "printing", "language", "comment",
              "price_override", "price_floor", "current_price"):
        if f in payload:
            setattr(item, f, payload[f])
    # Condition/printing are part of the FIFO pool key, so a change must carry
    # the acquisition batches with it (else cost/age/lots orphan). See
    # inv_svc.rekey_cost_basis.
    if item.condition != old_condition or item.printing != old_printing:
        inv_svc.rekey_cost_basis(db, item, old_condition=old_condition,
                                 old_printing=old_printing)
    for l in payload.get("listings", []):
        listing = inv_svc.get_or_create_listing(db, item, l["marketplace"])
        for f in ("listing_cap", "reserve_quantity"):
            if f in l:
                setattr(listing, f, l[f])
        listing.dirty = True
    item.updated_at = utcnow()
    db.commit()
    return inventory_dict(item)


@router.post("/adjust")
def adjust(payload: dict = Body(...), db: Session = Depends(get_db)):
    """Manual stock adjustment: set exact quantity or adjust by amount.
    Deductions clamp at 0. Supports multi-item bulk adjustment."""
    results = []
    for adj in payload.get("adjustments", []):
        item = db.get(InventoryItem, adj["inventory_id"])
        if not item:
            continue
        comment = adj.get("comment", "")
        if adj.get("damaged"):
            comment = f"[DAMAGED] {comment}".strip()
        if adj.get("set_quantity") is not None:
            delta = int(adj["set_quantity"]) - item.quantity
        else:
            delta = int(adj.get("delta", 0))
        applied = inv_svc.apply_delta(db, item, delta, type="adjustment",
                                      cause=adj.get("cause", "manual"),
                                      comment=comment)
        if applied > 0:
            inv_svc.record_acquisition(db, item, applied, adj.get("unit_cost"))
        results.append({"inventory_id": item.id, "applied": applied,
                        "quantity": item.quantity})
    db.commit()
    return {"results": results}


@router.post("/bulk-edit")
def bulk_edit(payload: dict = Body(...), db: Session = Depends(get_db)):
    """Bulk-set fields across filtered/selected records (Section 3 Bulk Edit).
    preview=true returns planned changes without applying."""
    items = filter_items(db, payload.get("filter", {}))
    changes = payload.get("set", {})
    preview = payload.get("preview", False)
    plan = []
    for item in items:
        entry = {"inventory_id": item.id, "changes": {}}
        if "price" in changes:
            entry["changes"]["current_price"] = changes["price"]
        if "price_override" in changes:
            entry["changes"]["price_override"] = changes["price_override"]
        if "clear_price_override" in changes:
            entry["changes"]["price_override"] = None
        if "price_floor" in changes:
            entry["changes"]["price_floor"] = changes["price_floor"]
        if "clear_price_floor" in changes:
            entry["changes"]["price_floor"] = None
        if "comment" in changes:
            entry["changes"]["comment"] = changes["comment"]
        if "bin" in changes and changes["bin"] != item.bin:
            entry["changes"]["bin"] = changes["bin"]
        if "condition" in changes:
            entry["changes"]["condition"] = changes["condition"]
        if "printing" in changes:
            entry["changes"]["printing"] = changes["printing"]
        if "language" in changes:
            entry["changes"]["language"] = changes["language"]
        if "cost" in changes:
            cost = changes["cost"]
            if isinstance(cost, dict):  # {"pct_of_price": 60} or {"flat": 0.1}
                base = item.price_override or item.current_price
                v = (base * cost["pct_of_price"] / 100.0) if cost.get("pct_of_price") and base \
                    else cost.get("flat")
            else:
                v = cost
            existing = inv_svc.fifo_unit_cost(db, item)
            if v is not None and (changes.get("cost_overwrite") or existing is None):
                entry["changes"]["_backfill_cost"] = round(v, 4)
        if "quantity" in changes:
            qc = changes["quantity"]  # {"set": n} | {"add": n} | {"subtract": n}
            if qc.get("set") is not None:
                entry["changes"]["_qty_delta"] = qc["set"] - item.quantity
            elif qc.get("add"):
                entry["changes"]["_qty_delta"] = qc["add"]
            elif qc.get("subtract"):
                entry["changes"]["_qty_delta"] = -qc["subtract"]
        for mk in ("ebay", "tcgplayer"):
            cap_key = f"{mk}_listing_cap"
            if cap_key in changes:
                entry["changes"][cap_key] = changes[cap_key]
        if entry["changes"]:
            plan.append(entry)
    if preview:
        return {"preview": True, "affected": len(plan), "plan": plan[:200]}

    for entry in plan:
        item = db.get(InventoryItem, entry["inventory_id"])
        ch = entry["changes"]
        if "bin" in ch:
            inv_svc.transfer_bin(db, item, ch.pop("bin"), cause="bulk_update")
        qty_delta = ch.pop("_qty_delta", None)
        backfill = ch.pop("_backfill_cost", None)
        for mk in ("ebay", "tcgplayer"):
            cap = ch.pop(f"{mk}_listing_cap", "___missing")
            if cap != "___missing":
                listing = inv_svc.get_or_create_listing(db, item, mk)
                listing.listing_cap = cap
                listing.dirty = True
        old_condition, old_printing = item.condition, item.printing
        for f, v in ch.items():
            setattr(item, f, v)
        # A condition/printing change must carry the FIFO cost basis (pool key
        # includes both) before any quantity delta adds new-key batches.
        if item.condition != old_condition or item.printing != old_printing:
            inv_svc.rekey_cost_basis(db, item, old_condition=old_condition,
                                     old_printing=old_printing)
        if qty_delta:
            applied = inv_svc.apply_delta(db, item, qty_delta, type="adjustment",
                                          cause="bulk_update")
            if applied > 0:
                inv_svc.record_acquisition(db, item, applied, backfill)
        elif backfill is not None and item.quantity > 0:
            inv_svc.record_acquisition(db, item, item.quantity, backfill)
        item.updated_at = utcnow()
        for l in item.listings:
            l.dirty = True
    db.commit()
    return {"preview": False, "affected": len(plan)}


@router.post("/transfer")
def bulk_transfer(payload: dict = Body(...), db: Session = Depends(get_db)):
    ids = payload.get("ids", [])
    new_bin = payload.get("bin", "")
    n = 0
    for item_id in ids:
        item = db.get(InventoryItem, item_id)
        if item:
            inv_svc.transfer_bin(db, item, new_bin, comment=payload.get("comment", ""))
            n += 1
    db.commit()
    return {"transferred": n}


@router.post("/{item_id}/supplier-refund")
def supplier_refund(item_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    """Refund from a supplier on a purchase (you are the buyer). mode=full returns
    `quantity` units and removes them + their cost; mode=partial keeps the goods
    and lowers cost basis by `amount`."""
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(404)
    mode = payload.get("mode", "partial")
    if mode == "full":
        qty = int(payload.get("quantity", 0))
        if qty <= 0 or qty > item.quantity:
            raise HTTPException(400, "invalid return quantity")
        r = inv_svc.return_to_supplier(db, item, qty, comment=payload.get("comment", ""))
    else:
        amount = float(payload.get("amount", 0) or 0)
        if amount <= 0:
            raise HTTPException(400, "refund amount must be positive")
        r = inv_svc.reduce_cost_basis(db, item, amount, comment=payload.get("comment", ""))
    db.commit()
    return {"mode": mode, **r, "quantity": item.quantity}


@router.post("/{item_id}/split")
def split(item_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    """Peel off a quantity into a new record with a different condition,
    printing, or language (at least one must differ)."""
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(404)
    qty = int(payload.get("quantity", 0))
    if qty <= 0 or qty > item.quantity:
        raise HTTPException(400, "invalid split quantity")
    new_attrs = {
        "condition": payload.get("condition", item.condition),
        "printing": payload.get("printing", item.printing),
        "language": payload.get("language", item.language),
    }
    if all(new_attrs[k] == getattr(item, k) for k in new_attrs):
        raise HTTPException(400, "split blocked: at least one attribute must differ")
    target = inv_svc.find_or_create_item(
        db, catalog_card_id=item.catalog_card_id, custom_sku_id=item.custom_sku_id,
        bin=item.bin, **new_attrs)
    # Move the units AND their FIFO cost basis (oldest first, preserving cost +
    # acquisition date) so a split never duplicates or resets cost/age.
    moved_cost = inv_svc.split_cost_basis(db, item, target, qty)
    inv_svc.apply_delta(db, item, -qty, type="adjustment", cause="manual",
                        comment=f"split out {qty} -> {new_attrs}")
    inv_svc.apply_delta(db, target, qty, type="addition", cause="manual",
                        comment=f"split from inventory #{item.id} (cost ${moved_cost} moved)")
    db.commit()
    return {"new_inventory_id": target.id, "cost_moved": moved_cost}


@router.post("/merge-duplicates")
def merge_duplicates(payload: dict = Body(default={}), db: Session = Depends(get_db)):
    """Merge exact-match rows (identity+condition+printing+language+bin+cost
    fields+comment+overrides+listing links). Irreversible."""
    items = filter_items(db, payload.get("filter", {}))
    groups: dict[tuple, list[InventoryItem]] = {}
    for it in items:
        listing_key = tuple(sorted(
            (l.marketplace, l.ebay_listing_id or "", l.tcg_sku_id or "")
            for l in it.listings if l.ebay_listing_id or l.tcg_sku_id))
        key = (it.catalog_card_id, it.custom_sku_id, it.condition, it.printing,
               it.language, it.bin, it.comment, it.price_override,
               it.price_floor, listing_key)
        groups.setdefault(key, []).append(it)
    merged = 0
    for group in groups.values():
        if len(group) < 2:
            continue
        keeper, *rest = sorted(group, key=lambda x: x.id)
        for dup in rest:
            if dup.quantity:
                inv_svc.apply_delta(db, keeper, dup.quantity, type="adjustment",
                                    cause="bulk_update",
                                    comment=f"merged from #{dup.id}")
                inv_svc.apply_delta(db, dup, -dup.quantity, type="adjustment",
                                    cause="bulk_update",
                                    comment=f"merged into #{keeper.id}")
            dup.deleted = True
            merged += 1
    db.commit()
    return {"merged_rows": merged}


@router.post("/{item_id}/delete")
def soft_delete(item_id: int, db: Session = Depends(get_db)):
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(404)
    item.deleted = True
    inv_svc.log_mutation(db, item, "adjustment", 0, cause="manual", comment="soft delete")
    db.commit()
    return {"ok": True}


@router.post("/{item_id}/restore")
def restore(item_id: int, db: Session = Depends(get_db)):
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(404)
    item.deleted = False
    inv_svc.log_mutation(db, item, "adjustment", 0, cause="manual", comment="restored")
    db.commit()
    return {"ok": True}


@router.delete("/{item_id}")
def hard_delete(item_id: int, db: Session = Depends(get_db)):
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(404)
    for l in item.listings:
        db.delete(l)
    db.delete(item)
    db.commit()
    return {"ok": True, "hard_deleted": item_id}


# --- Audit log (global view) -------------------------------------------------

@router.get("/log/global")
def global_log(type: str = "", cause: str = "", q: str = "", limit: int = 500,
               db: Session = Depends(get_db)):
    query = select(InventoryLog).order_by(InventoryLog.id.desc()).limit(limit)
    if type:
        query = query.where(InventoryLog.type == type)
    if cause:
        query = query.where(InventoryLog.cause == cause)
    if q:
        query = query.where(InventoryLog.item_description.ilike(f"%{q}%"))
    return [{
        "id": h.id, "inventory_id": h.inventory_id,
        "item": h.item_description, "type": h.type,
        "quantity_delta": h.quantity_delta, "price_at": h.price_at,
        "cost_at": h.cost_at, "bin_before": h.bin_before,
        "bin_after": h.bin_after, "comment": h.comment, "cause": h.cause,
        "source": h.source,
        "created_at": h.created_at.isoformat() if h.created_at else None,
    } for h in db.execute(query).scalars()]


# --- Cycle counts ------------------------------------------------------------

@router.post("/cycle-counts")
def start_cycle_count(payload: dict = Body(...), db: Session = Depends(get_db)):
    bin_name = payload.get("bin", "")
    count = CycleCount(bin=bin_name)
    db.add(count)
    db.flush()
    items = db.execute(select(InventoryItem).where(
        InventoryItem.bin == bin_name,
        InventoryItem.deleted == False,  # noqa: E712
        InventoryItem.quantity > 0)).scalars().all()
    for it in items:
        db.add(CycleCountLine(count_id=count.id, inventory_id=it.id,
                              expected=it.quantity))
    db.commit()
    return {"count_id": count.id, "lines": len(items)}


@router.get("/cycle-counts/list")
def list_cycle_counts(db: Session = Depends(get_db)):
    counts = db.execute(select(CycleCount).order_by(CycleCount.id.desc())).scalars().all()
    return [{"id": c.id, "bin": c.bin, "status": c.status,
             "created_at": c.created_at.isoformat() if c.created_at else None,
             "lines": len(c.lines),
             "counted": sum(1 for l in c.lines if l.counted is not None)}
            for c in counts]


@router.get("/cycle-counts/{count_id}")
def get_cycle_count(count_id: int, db: Session = Depends(get_db)):
    count = db.get(CycleCount, count_id)
    if not count:
        raise HTTPException(404)
    lines = []
    for l in count.lines:
        status = ("uncounted" if l.counted is None
                  else "match" if l.counted == l.expected else "discrepancy")
        lines.append({
            "id": l.id, "inventory_id": l.inventory_id,
            "name": l.item.card.name if l.item and l.item.card else
            (l.item.custom_sku.product.name if l.item and l.item.custom_sku else "?"),
            "condition": l.item.condition if l.item else "",
            "printing": l.item.printing if l.item else "",
            "expected": l.expected, "counted": l.counted, "status": status,
        })
    return {"id": count.id, "bin": count.bin, "status": count.status, "lines": lines}


@router.patch("/cycle-counts/lines/{line_id}")
def update_count_line(line_id: int, payload: dict = Body(...),
                      db: Session = Depends(get_db)):
    """Progress auto-saves: each tally persists immediately."""
    line = db.get(CycleCountLine, line_id)
    if not line:
        raise HTTPException(404)
    line.counted = payload.get("counted")
    db.commit()
    return {"ok": True}


@router.post("/cycle-counts/{count_id}/approve")
def approve_cycle_count(count_id: int, db: Session = Depends(get_db)):
    """Nothing changes inventory until this explicit approval; deltas are
    committed as logged adjustments and the bin marked verified."""
    count = db.get(CycleCount, count_id)
    if not count:
        raise HTTPException(404)
    adjusted = 0
    for line in count.lines:
        if line.counted is None or line.counted == line.expected:
            continue
        item = db.get(InventoryItem, line.inventory_id)
        if not item:
            continue
        delta = line.counted - item.quantity
        applied = inv_svc.apply_delta(db, item, delta, type="adjustment",
                                      cause="cycle_count",
                                      comment=f"cycle count #{count.id} bin '{count.bin}'")
        if applied > 0:
            inv_svc.record_acquisition(db, item, applied, None)
        adjusted += 1
    count.status = "completed"
    count.completed_at = utcnow()
    db.commit()
    return {"adjusted": adjusted}
