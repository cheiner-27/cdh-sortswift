"""Packing-slip order intake: upload, review, commit (Section 8)."""
from datetime import timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import SlipBatch, SlipOrder, utcnow
from ..services import order_intake as intake_svc
from ..services import orders as order_svc
from ..services.pdf_slips import SlipParseError
from ..validate import money, whole
from .serializers import slip_batch_dict, slip_order_dict

router = APIRouter(prefix="/api/order-intake", tags=["order-intake"])


def _slip(db: Session, slip_id: int) -> SlipOrder:
    slip = db.get(SlipOrder, slip_id)
    if not slip:
        raise HTTPException(404)
    return slip


@router.post("/upload")
async def upload(file: UploadFile, db: Session = Depends(get_db)):
    """Upload a packing-slip PDF. Parses and matches only — nothing goes live."""
    content = await file.read()
    try:
        batch = intake_svc.build_batch(db, filename=file.filename or "",
                                       content=content)
    except SlipParseError as e:
        raise HTTPException(400, str(e))
    return slip_batch_dict(batch, with_orders=True)


# The uploads list shows the last two weeks of uploads, but never fewer than
# the newest few, so a quiet fortnight doesn't leave it empty.
RECENT_DAYS = 14
RECENT_MIN = 10


@router.get("/batches")
def batches(page: int = 1, page_size: int = 10, show: str = "recent",
            db: Session = Depends(get_db)):
    """Uploads, newest first, one page at a time.

    ``show=recent`` (the default) covers uploads from the last ``RECENT_DAYS``
    days, topped up to the ``RECENT_MIN`` newest when that window is thin.
    ``show=all`` pages through every upload.
    """
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    total_all = db.scalar(select(func.count(SlipBatch.id))) or 0
    if show == "all":
        total = total_all
    else:
        since = utcnow() - timedelta(days=RECENT_DAYS)
        in_window = db.scalar(select(func.count(SlipBatch.id)).where(
            SlipBatch.created_at >= since)) or 0
        # Newest-first, the window is a prefix of the list, so "the window or
        # the newest RECENT_MIN, whichever is longer" is just a longer prefix.
        total = max(in_window, min(RECENT_MIN, total_all))
    offset = (page - 1) * page_size
    take = max(0, min(page_size, total - offset))
    rows = db.execute(select(SlipBatch).order_by(
        SlipBatch.created_at.desc().nulls_last(), SlipBatch.id.desc())
        .offset(offset).limit(take)).scalars().all() if take else []
    return {"items": [slip_batch_dict(b) for b in rows], "total": total,
            "total_all": total_all, "page": page, "page_size": page_size,
            "show": "all" if show == "all" else "recent",
            "recent_days": RECENT_DAYS}


@router.get("/batches/{batch_id}")
def batch_detail(batch_id: int, db: Session = Depends(get_db)):
    b = db.get(SlipBatch, batch_id)
    if not b:
        raise HTTPException(404)
    return slip_batch_dict(b, with_orders=True)


@router.delete("/batches/{batch_id}")
def delete_batch(batch_id: int, db: Session = Depends(get_db)):
    """Discard a review batch. Orders already committed from it are untouched —
    they're live orders now and have their own lifecycle."""
    b = db.get(SlipBatch, batch_id)
    if not b:
        raise HTTPException(404)
    db.delete(b)
    db.commit()
    return {"ok": True, "deleted": batch_id}


@router.patch("/orders/{slip_id}")
def edit_order(slip_id: int, payload: dict = Body(...),
               db: Session = Depends(get_db)):
    """Correct a parsed order before commit.

    ``tax`` set to a number pins the buyer-paid tax (making the fee exact);
    clearing it back to null returns to the state-based estimate.
    """
    slip = _slip(db, slip_id)
    if slip.status in ("committed", "duplicate"):
        raise HTTPException(400, f"order is {slip.status} — nothing to edit")
    if "shipping_charged" in payload:
        slip.shipping_charged = money(payload["shipping_charged"],
                                      "shipping_charged", default=0.0)
    if "shipping_cost" in payload:
        slip.shipping_cost = money(payload["shipping_cost"],
                                   "shipping_cost", default=0.0)
    if "tax" in payload:
        slip.tax = money(payload["tax"], "tax", default=None)
    if "estimated_fee" in payload:
        # Blank restores the estimate; a number pins what the reviewer typed.
        fee = money(payload["estimated_fee"], "estimated_fee", default=None)
        slip.fee_overridden = fee is not None
        if fee is not None:
            slip.estimated_fee = fee
    if "quantity_total" in payload:
        slip.quantity_total = whole(payload["quantity_total"], "quantity_total",
                                    default=slip.quantity_total)
    if "item_total" in payload:
        slip.item_total = money(payload["item_total"], "item_total",
                                default=slip.item_total)
    for field in ("buyer_name", "ship_city", "ship_state", "ship_postal_code"):
        if field in payload:
            setattr(slip, field, str(payload[field] or "").strip())
    if "ordered_at" in payload:
        slip.ordered_at = order_svc.parse_sale_date(payload["ordered_at"])
    intake_svc.refresh(db, slip)
    db.commit()
    return slip_order_dict(slip)


@router.post("/orders/{slip_id}/lines/{index}/resolve")
def resolve_line(slip_id: int, index: int, payload: dict = Body(default={}),
                 db: Session = Depends(get_db)):
    """Match one line by hand: pick an inventory record, or skip it.

    Body: ``{inventory_id}`` | ``{skip: true}``.
    """
    slip = _slip(db, slip_id)
    if slip.status in ("committed", "duplicate"):
        raise HTTPException(400, f"order is {slip.status} — nothing to edit")
    try:
        if payload.get("skip"):
            intake_svc.skip_line(db, slip, index)
        else:
            if payload.get("inventory_id") is None:
                raise ValueError("inventory_id or skip required")
            intake_svc.resolve_line(
                db, slip, index,
                inventory_id=whole(payload["inventory_id"], "inventory_id",
                                   min_value=1, max_value=None))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return slip_order_dict(slip)


@router.post("/orders/{slip_id}/rematch")
def rematch(slip_id: int, db: Session = Depends(get_db)):
    """Re-run automatic matching — use after adding the missing stock."""
    slip = _slip(db, slip_id)
    if slip.status in ("committed", "duplicate"):
        raise HTTPException(400, f"order is {slip.status} — nothing to rematch")
    slip.lines = [intake_svc.match_line(db, l) for l in (slip.lines or [])]
    intake_svc.refresh(db, slip)
    db.commit()
    return slip_order_dict(slip)


@router.post("/orders/{slip_id}/commit")
def commit_one(slip_id: int, db: Session = Depends(get_db)):
    slip = _slip(db, slip_id)
    try:
        order = intake_svc.commit_order(db, slip)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"order_id": order.id, "slip": slip_order_dict(slip)}


@router.post("/batches/{batch_id}/commit")
def commit_batch(batch_id: int, db: Session = Depends(get_db)):
    """Commit every ready order; blocked ones stay behind for review."""
    b = db.get(SlipBatch, batch_id)
    if not b:
        raise HTTPException(404)
    result = intake_svc.commit_batch(db, b)
    result["batch"] = slip_batch_dict(b, with_orders=True)
    return result
