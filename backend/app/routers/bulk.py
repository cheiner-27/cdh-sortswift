"""Bulk piles — opaque card lots bought and sold by count, never inventoried
per-card (e.g. "MTG Bulk Commons": buy 5,000 cheap, sell packs of 100).

A pile is modeled on the existing custom-item machinery so it inherits all
accounting for free:

    CustomProduct(item_type="bulk") -> one CustomSku -> its InventoryItem
    (quantity = card count)

- **Buying** records a FIFO acquisition batch per purchase (500 @ $0.05, then
  1,000 @ $0.06 → those exact costs are consumed oldest-first on sale).
- **Selling** goes through the normal manual-order path so COGS/P&L are booked
  by FIFO like any other sale.
- **Pulling** a good card OUT of a pile into tracked inventory happens in the
  scan/staging workflow via ``source_bulk_id`` (see services/staging.py), not
  here.
"""
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AcquisitionLog, CustomProduct, CustomSku, InventoryItem
from ..services import inventory as inv_svc
from ..services import orders as order_svc

router = APIRouter(prefix="/api/bulk", tags=["bulk"])

# A pile's FIFO pool key is (custom_sku_id, condition, printing); pin condition
# and printing so every buy into one pile shares a single oldest-first pool.
BULK_CONDITION = "NM"
BULK_PRINTING = "normal"


def _pile_sku(product: CustomProduct) -> CustomSku | None:
    return product.skus[0] if product.skus else None


def _pile_item(db: Session, sku: CustomSku, *,
               include_deleted: bool = False) -> InventoryItem | None:
    q = select(InventoryItem).where(InventoryItem.custom_sku_id == sku.id)
    if not include_deleted:
        q = q.where(InventoryItem.deleted == False)  # noqa: E712
    return db.execute(q).scalars().first()


def pile_dict(db: Session, product: CustomProduct) -> dict:
    sku = _pile_sku(product)
    item = _pile_item(db, sku) if sku else None
    cost_basis = 0.0
    next_unit_cost = None
    if sku is not None:
        batches = db.execute(select(AcquisitionLog).where(
            AcquisitionLog.custom_sku_id == sku.id,
            AcquisitionLog.quantity_remaining > 0,
        )).scalars().all()
        cost_basis = sum(b.quantity_remaining * b.unit_cost for b in batches)
        if item is not None:
            next_unit_cost = inv_svc.fifo_unit_cost(db, item)
    return {
        "id": product.id,
        "name": product.name,
        "game": product.category,
        "group": product.group,
        "description": product.description,
        "sku_id": sku.id if sku else None,
        "inventory_id": item.id if item else None,
        "on_hand": item.quantity if item else 0,
        "cost_basis": round(cost_basis, 2),
        "avg_unit_cost": (round(cost_basis / item.quantity, 4)
                          if item and item.quantity else None),
        "next_unit_cost": round(next_unit_cost, 4) if next_unit_cost is not None else None,
        "current_price": item.current_price if item else None,
    }


@router.get("/piles")
def list_piles(db: Session = Depends(get_db)):
    products = db.execute(select(CustomProduct).where(
        CustomProduct.item_type == "bulk").order_by(CustomProduct.name)).scalars().all()
    out = []
    for p in products:
        sku = _pile_sku(p)
        item = _pile_item(db, sku, include_deleted=True) if sku else None
        if item is not None and item.deleted:
            continue  # soft-deleted pile — hidden
        out.append(pile_dict(db, p))
    return out


@router.delete("/piles/{product_id}")
def delete_pile(product_id: int, db: Session = Depends(get_db)):
    """Delete a bulk pile. A pile that was never stocked is removed outright;
    one with stock or sales history is soft-deleted — its inventory record is
    flagged deleted (hidden from the pile list and inventory/valuation) while
    FIFO batches and order history stay intact."""
    product = db.get(CustomProduct, product_id)
    if not product or product.item_type != "bulk":
        raise HTTPException(404, "not a bulk pile")
    sku = _pile_sku(product)
    item = _pile_item(db, sku, include_deleted=True) if sku else None
    if item is not None:
        if not item.deleted:
            inv_svc.log_mutation(db, item, "adjustment", 0, cause="manual",
                                 comment=f"bulk pile '{product.name}' deleted (hidden)")
        item.deleted = True
        db.commit()
        return {"ok": True, "soft_deleted": True, "on_hand": item.quantity}
    db.delete(product)  # never stocked → cascade removes the sku
    db.commit()
    return {"ok": True, "deleted": True}


@router.post("/piles")
def create_pile(payload: dict = Body(...), db: Session = Depends(get_db)):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    product = CustomProduct(
        category=payload.get("game", "Other"),
        group=payload.get("group", ""),
        name=name,
        item_type="bulk",
        description=payload.get("description", ""))
    db.add(product)
    db.flush()
    db.add(CustomSku(product_id=product.id, condition=BULK_CONDITION,
                     printing=BULK_PRINTING, language="en"))
    db.commit()
    return pile_dict(db, product)


@router.post("/piles/{product_id}/purchase")
def record_purchase(product_id: int, payload: dict = Body(...),
                    db: Session = Depends(get_db)):
    """Record a bulk buy: card count + what you paid. Accepts either a per-card
    ``unit_cost`` or a ``total_cost`` (divided into a per-card cost). Writes one
    FIFO acquisition batch."""
    product = db.get(CustomProduct, product_id)
    if not product or product.item_type != "bulk":
        raise HTTPException(404, "not a bulk pile")
    sku = _pile_sku(product)
    if sku is None:
        raise HTTPException(400, "pile has no sku")
    qty = int(payload.get("quantity", 0))
    if qty <= 0:
        raise HTTPException(400, "quantity must be > 0")
    unit_cost = payload.get("unit_cost")
    if unit_cost is None and payload.get("total_cost") is not None:
        unit_cost = float(payload["total_cost"]) / qty
    # Keep a pile to a single inventory record: reuse the existing bin so a
    # second purchase never splits the pile across two rows (the bin only
    # matters on the very first buy).
    existing = _pile_item(db, sku)
    bin_val = existing.bin if existing is not None else payload.get("bin", "")
    item = inv_svc.find_or_create_item(
        db, custom_sku_id=sku.id, condition=BULK_CONDITION,
        printing=BULK_PRINTING, bin=bin_val)
    if payload.get("price") is not None:
        item.current_price = payload["price"]
    # Bulk is in-store only: cap it out of every marketplace push (cap 0).
    for mk in ("ebay", "tcgplayer"):
        listing = inv_svc.get_or_create_listing(db, item, mk)
        if listing.listing_cap is None:
            listing.listing_cap = 0
    inv_svc.add_stock(db, item, qty,
                      float(unit_cost) if unit_cost is not None else None,
                      cause="bulk_purchase",
                      comment=f"bulk purchase into {product.name}",
                      acquired_at=order_svc.parse_sale_date(payload.get("acquired_at")))
    db.commit()
    return pile_dict(db, product)


@router.post("/piles/{product_id}/sell")
def sell_bulk(product_id: int, payload: dict = Body(...),
              db: Session = Depends(get_db)):
    """Record a manual/offline bulk sale by card count + sale price. Reuses the
    normal manual-order path so COGS is booked FIFO and it lands in Orders/P&L.
    Accepts ``total_price`` (whole-sale price) or ``unit_price`` (per card)."""
    product = db.get(CustomProduct, product_id)
    if not product or product.item_type != "bulk":
        raise HTTPException(404, "not a bulk pile")
    sku = _pile_sku(product)
    item = _pile_item(db, sku) if sku else None
    if item is None or item.quantity <= 0:
        raise HTTPException(400, "no bulk stock to sell")
    qty = int(payload.get("quantity", 0))
    if qty <= 0:
        raise HTTPException(400, "quantity must be > 0")
    if qty > item.quantity:
        raise HTTPException(400, f"only {item.quantity} cards on hand")
    total = payload.get("total_price")
    unit_price = payload.get("unit_price")
    if unit_price is None:
        if total is None:
            raise HTTPException(400, "total_price or unit_price required")
        unit_price = float(total) / qty
    order = order_svc.create_manual_order(
        db,
        buyer_name=payload.get("buyer_name") or "Bulk sale",
        items=[{"inventory_id": item.id, "quantity": qty,
                "unit_price": float(unit_price),
                "description": f"{product.name} — {qty} cards (bulk)"}],
        total=float(total) if total is not None else None,
        shipping_cost=float(payload.get("shipping_cost", 0) or 0),
        marketplace_fees=float(payload.get("marketplace_fees", 0) or 0),
        shipping_charged=float(payload.get("shipping_charged", 0) or 0),
        ordered_at=payload.get("ordered_at"))
    return {"order_id": order.id, "pile": pile_dict(db, product)}
