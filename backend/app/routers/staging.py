"""Staging review: edit, partial approve, reject, manual add paths."""
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import StagingItem
from ..services import pricing, staging as staging_svc
from .serializers import staging_dict

router = APIRouter(prefix="/api/staging", tags=["staging"])


def _staging_dict(db: Session, row: StagingItem) -> dict:
    """staging_dict enriched with the card's at-a-glance market value."""
    return staging_dict(
        row, market_value=pricing.card_market_value(db, row.card, row.printing))


def _parse_date(value):
    """Accept an ISO 'YYYY-MM-DD' (or full ISO) string → tz-aware datetime."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(str(value).strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


@router.get("")
def list_staging(source: str = "", db: Session = Depends(get_db)):
    q = select(StagingItem).order_by(StagingItem.id.desc())
    if source:
        q = q.where(StagingItem.source == source)
    return [_staging_dict(db, s) for s in db.execute(q).scalars()]


@router.patch("/{row_id}")
def edit_row(row_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    row = db.get(StagingItem, row_id)
    if not row:
        raise HTTPException(404)
    for f in ("condition", "printing", "language", "bin", "quantity",
              "cost", "price", "comment", "catalog_card_id", "source_bulk_id"):
        if f in payload:
            setattr(row, f, payload[f])
    if "acquired_at" in payload:
        row.acquired_at = _parse_date(payload["acquired_at"])
    db.commit()
    return _staging_dict(db, row)


@router.post("/approve")
def approve(payload: dict = Body(...), db: Session = Depends(get_db)):
    """Approve some or all staged rows (partial approval supported)."""
    ids = payload.get("ids")
    if ids:
        rows = db.execute(select(StagingItem).where(
            StagingItem.id.in_(ids))).scalars().all()
    else:
        rows = db.execute(select(StagingItem)).scalars().all()
    n = staging_svc.approve_staging_rows(db, rows)
    return {"approved": n}


@router.post("/reject")
def reject(payload: dict = Body(...), db: Session = Depends(get_db)):
    """Rejecting in staging permanently discards the row."""
    ids = payload.get("ids", [])
    rows = db.execute(select(StagingItem).where(
        StagingItem.id.in_(ids))).scalars().all()
    for r in rows:
        db.delete(r)
    db.commit()
    return {"rejected": len(rows)}


@router.post("/manual-add")
def manual_add(payload: dict = Body(...), db: Session = Depends(get_db)):
    """Full-form manual entry. direct=true skips staging (trusted intake)."""
    fields = dict(
        catalog_card_id=payload.get("catalog_card_id"),
        custom_sku_id=payload.get("custom_sku_id"),
        condition=payload.get("condition", "NM"),
        printing=payload.get("printing", "normal"),
        language=payload.get("language", "en"),
        bin=payload.get("bin", ""),
        quantity=int(payload.get("quantity", 1)),
        cost=payload.get("cost"),
        price=payload.get("price"),
        acquired_at=_parse_date(payload.get("acquired_at")),
        comment=payload.get("comment", ""),
        source_bulk_id=payload.get("source_bulk_id"),
    )
    if not fields["catalog_card_id"] and not fields["custom_sku_id"]:
        raise HTTPException(400, "catalog_card_id or custom_sku_id required")
    if payload.get("direct"):
        inv_id = staging_svc.add_direct(db, **fields)
        return {"inventory_id": inv_id, "direct": True}
    row = StagingItem(source="manual", **fields)
    db.add(row)
    db.commit()
    return {"staging_id": row.id, "direct": False}


@router.post("/bulk-add")
def bulk_add(payload: dict = Body(...), db: Session = Depends(get_db)):
    """Add one-to-many catalog/custom products in a single submit.

    This is the single manual-entry path (a one-row add is just this with one
    row). ``direct=true`` skips staging and writes straight to live inventory
    for trusted intake; otherwise rows land in staging for review.
    """
    rows = payload.get("rows", [])
    direct = bool(payload.get("direct"))
    n = 0
    for r in rows:
        if not r.get("catalog_card_id") and not r.get("custom_sku_id"):
            continue
        fields = dict(
            catalog_card_id=r.get("catalog_card_id"),
            custom_sku_id=r.get("custom_sku_id"),
            condition=r.get("condition", "NM"),
            printing=r.get("printing", "normal"),
            language=r.get("language", "en"),
            bin=r.get("bin", ""),
            quantity=int(r.get("quantity", 1)),
            cost=r.get("cost"), price=r.get("price"),
            acquired_at=_parse_date(r.get("acquired_at")),
            comment=r.get("comment", ""),
            source_bulk_id=r.get("source_bulk_id"))
        if direct:
            staging_svc.add_direct(db, **fields)
        else:
            db.add(StagingItem(source="manual", **fields))
        n += 1
    db.commit()
    return {"staged": 0 if direct else n, "added": n if direct else 0, "direct": direct}
