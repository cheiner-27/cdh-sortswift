"""Orders & fulfillment: pick lists, packing slips, labels, post-ship."""
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Order
from ..services import orders as order_svc
from ..validate import money, whole
from .serializers import order_dict

router = APIRouter(prefix="/api/orders", tags=["orders"])


def _clean_items(items: list) -> list:
    """Validate the money/count fields on manual-sale line items.

    These feed FIFO consumption and the P&L directly, so a string price here
    would either 500 at commit or book a bad COGS number.
    """
    out = []
    for i in items:
        out.append({**i,
                    "quantity": whole(i.get("quantity"), "quantity", default=1, min_value=1),
                    "unit_price": money(i.get("unit_price"), "unit_price", default=0.0)})
    return out


@router.get("")
def list_orders(status: str = "", marketplace: str = "",
                db: Session = Depends(get_db)):
    q = select(Order).order_by(Order.ordered_at.desc())
    if status:
        q = q.where(Order.status == status)
    if marketplace:
        q = q.where(Order.marketplace == marketplace)
    return [order_dict(o) for o in db.execute(q.limit(500)).scalars()]


@router.get("/platforms")
def manual_platforms(db: Session = Depends(get_db)):
    """Platform options for the manual-sale form (defaults + previously used)."""
    return {"platforms": order_svc.manual_sale_platforms(db)}


@router.get("/{order_id}")
def detail(order_id: int, db: Session = Depends(get_db)):
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404)
    return order_dict(o)


@router.post("/pick-list")
def pick_list(payload: dict = Body(...), db: Session = Depends(get_db)):
    ids = payload.get("order_ids", [])
    orders = db.execute(select(Order).where(Order.id.in_(ids))).scalars().all()
    if not orders:
        raise HTTPException(400, "no orders selected")
    return {"rows": order_svc.build_pick_list(db, orders),
            "orders": [f"{o.marketplace}:{o.external_order_id}" for o in orders]}


@router.get("/{order_id}/packing-slip")
def packing_slip(order_id: int, db: Session = Depends(get_db)):
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404)
    return order_svc.build_packing_slip(db, o)


@router.post("/{order_id}/buy-label")
def buy_label(order_id: int, payload: dict = Body(default={}),
              db: Session = Depends(get_db)):
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404)
    try:
        return order_svc.buy_shippo_label(db, o, payload.get("parcel"),
                                          payload.get("address_from"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # Shippo HTTP errors
        raise HTTPException(502, f"Shippo error: {e}")


@router.post("/{order_id}/mark-shipped")
def mark_shipped(order_id: int, payload: dict = Body(default={}),
                 db: Session = Depends(get_db)):
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404)
    return order_svc.mark_shipped(db, o, payload.get("tracking_number"),
                                  payload.get("carrier"))


@router.post("/{order_id}/refund")
def refund(order_id: int, payload: dict = Body(default={}),
           db: Session = Depends(get_db)):
    """Refund a customer. Body: {mode: full|partial, amount, returned,
    return_shipping, fees_refunded}. Defaults to a full refund with the item
    returned (restock) and the marketplace fees credited back in full."""
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404)
    try:
        return order_svc.refund_sale(
            db, o, mode=payload.get("mode", "full"),
            amount=money(payload.get("amount"), "amount", default=None),
            returned=payload.get("returned", True),
            return_shipping=money(payload.get("return_shipping"),
                                  "return_shipping", default=0.0),
            fees_refunded=money(payload.get("fees_refunded"),
                                "fees_refunded", default=None))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.patch("/{order_id}/costs")
def edit_costs(order_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    """Adjust an order's shipping / marketplace-fee costs after the fact."""
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404)
    order_svc.set_order_costs(
        db, o,
        shipping_cost=money(payload.get("shipping_cost"), "shipping_cost", default=None),
        marketplace_fees=money(payload.get("marketplace_fees"), "marketplace_fees", default=None),
        shipping_charged=money(payload.get("shipping_charged"), "shipping_charged", default=None),
        ordered_at=payload.get("ordered_at"))
    return order_dict(o)


@router.post("/{order_id}/cancel")
def cancel(order_id: int, db: Session = Depends(get_db)):
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404)
    if o.status == "shipped":
        raise HTTPException(400, "order already shipped — use refund")
    order_svc.cancel_order(db, o)
    return {"ok": True}


@router.delete("/{order_id}")
def delete_order(order_id: int, db: Session = Depends(get_db)):
    """Remove an order record. Inventory is NOT changed (no restock, no COGS
    reversal) — use cancel/refund if you want the stock back."""
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404)
    order_svc.delete_order(db, o)
    return {"ok": True, "deleted": order_id}


@router.post("/manual")
def manual_order(payload: dict = Body(...), db: Session = Depends(get_db)):
    order = order_svc.create_manual_order(
        db, platform=payload.get("platform") or payload.get("buyer_name") or "manual",
        items=_clean_items(payload.get("items", [])),
        total=money(payload.get("total"), "total", default=None),
        shipping_cost=money(payload.get("shipping_cost"), "shipping_cost", default=0.0),
        marketplace_fees=money(payload.get("marketplace_fees"), "marketplace_fees", default=0.0),
        shipping_charged=money(payload.get("shipping_charged"), "shipping_charged", default=0.0),
        ordered_at=payload.get("ordered_at"))
    return order_dict(order)
