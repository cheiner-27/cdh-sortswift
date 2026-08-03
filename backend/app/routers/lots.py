"""Bulk lot builder: templates, generation, dissolve, sell."""
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Lot, LotTemplate
from ..services import lots as lot_svc
from ..validate import money, whole
from .serializers import lot_dict

router = APIRouter(prefix="/api/lots", tags=["lots"])


def _clean(field: str, value):
    """Validate one lot-template field by name; pass free text through."""
    if field == "lot_size":
        return whole(value, field, default=100, min_value=1)
    if field == "max_duplicates":
        return whole(value, field, default=4, min_value=1)
    if field == "margin_pct":
        return money(value, field, default=80.0)
    if field == "fixed_price":
        return money(value, field, default=None)
    return value


@router.get("/templates")
def templates(db: Session = Depends(get_db)):
    return [{
        "id": t.id, "name": t.name, "description": t.description,
        "filters": t.filters, "lot_size": t.lot_size,
        "pricing_method": t.pricing_method, "margin_pct": t.margin_pct,
        "fixed_price": t.fixed_price, "max_duplicates": t.max_duplicates,
    } for t in db.execute(select(LotTemplate)).scalars()]


@router.post("/templates")
def create_template(payload: dict = Body(...), db: Session = Depends(get_db)):
    t = LotTemplate(
        name=payload.get("name", "lot"), description=payload.get("description", ""),
        filters=payload.get("filters", {}),
        lot_size=_clean("lot_size", payload.get("lot_size")),
        pricing_method=payload.get("pricing_method", "value_margin"),
        margin_pct=_clean("margin_pct", payload.get("margin_pct")),
        fixed_price=_clean("fixed_price", payload.get("fixed_price")),
        max_duplicates=_clean("max_duplicates", payload.get("max_duplicates")))
    db.add(t)
    db.commit()
    return {"id": t.id}


@router.put("/templates/{template_id}")
def update_template(template_id: int, payload: dict = Body(...),
                    db: Session = Depends(get_db)):
    t = db.get(LotTemplate, template_id)
    if not t:
        raise HTTPException(404)
    for f in ("name", "description", "filters", "lot_size", "pricing_method",
              "margin_pct", "fixed_price", "max_duplicates"):
        if f in payload:
            setattr(t, f, _clean(f, payload[f]))
    db.commit()
    return {"ok": True}


@router.delete("/templates/{template_id}")
def delete_template(template_id: int, db: Session = Depends(get_db)):
    t = db.get(LotTemplate, template_id)
    if not t:
        raise HTTPException(404)
    db.delete(t)
    db.commit()
    return {"ok": True}


@router.get("")
def lots(db: Session = Depends(get_db)):
    return [lot_dict(l) for l in db.execute(
        select(Lot).order_by(Lot.id.desc())).scalars()]


@router.post("/generate/{template_id}")
def generate(template_id: int, payload: dict = Body(default={}),
             db: Session = Depends(get_db)):
    t = db.get(LotTemplate, template_id)
    if not t:
        raise HTTPException(404)
    lot = lot_svc.generate_lot(db, t, payload.get("name"))
    return lot_dict(lot)


@router.post("/{lot_id}/dissolve")
def dissolve(lot_id: int, db: Session = Depends(get_db)):
    lot = db.get(Lot, lot_id)
    if not lot:
        raise HTTPException(404)
    if lot.status == "sold":
        raise HTTPException(400, "lot already sold")
    lot_svc.dissolve_lot(db, lot)
    return {"ok": True}


@router.post("/{lot_id}/sell")
def sell(lot_id: int, db: Session = Depends(get_db)):
    lot = db.get(Lot, lot_id)
    if not lot:
        raise HTTPException(404)
    if lot.status == "sold":
        raise HTTPException(400, "lot already sold")
    cogs = lot_svc.sell_lot(db, lot)
    return {"ok": True, "cogs": round(cogs, 2)}


@router.post("/{lot_id}/mark-listed")
def mark_listed(lot_id: int, payload: dict = Body(default={}),
                db: Session = Depends(get_db)):
    lot = db.get(Lot, lot_id)
    if not lot:
        raise HTTPException(404)
    lot.status = "listed"
    lot.marketplace = payload.get("marketplace", "ebay")
    lot.external_listing_id = payload.get("external_listing_id")
    db.commit()
    return {"ok": True}
