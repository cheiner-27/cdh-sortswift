"""Staging review: edit, partial approve, reject, manual add paths."""
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..domain import CONDITIONS, CANONICAL_PRINTINGS, LANGUAGES
from ..models import StagingItem
from ..services import pricing, staging as staging_svc
from ..validate import choice, money, whole
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


# Row fields that carry a value type, checked identically on the per-row PATCH,
# bulk edit and both add paths. Everything else on a staged row is free text.
_ENUMS = {"condition": CONDITIONS, "printing": CANONICAL_PRINTINGS,
          "language": LANGUAGES}


def _clean(field: str, value):
    """Validate one staged-row field by name; pass free text through."""
    if field in _ENUMS:
        return choice(value, field, _ENUMS[field])
    if field == "quantity":
        return whole(value, field, min_value=1)
    if field in ("cost", "price"):
        return money(value, field, default=None)
    return value


@router.patch("/{row_id}")
def edit_row(row_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    row = db.get(StagingItem, row_id)
    if not row:
        raise HTTPException(404)
    for f in ("condition", "printing", "language", "bin", "quantity",
              "cost", "price", "comment", "catalog_card_id", "source_bulk_id"):
        if f in payload:
            setattr(row, f, _clean(f, payload[f]))
    if "acquired_at" in payload:
        row.acquired_at = _parse_date(payload["acquired_at"])
    db.commit()
    return _staging_dict(db, row)


@router.post("/bulk-edit")
def bulk_edit(payload: dict = Body(...), db: Session = Depends(get_db)):
    """Set the same field(s) on many staged rows at once.

    The bread-and-butter case is one purchase date + cost for a whole batch that
    came in together. Only keys present in ``set`` are touched, and blank values
    are dropped by the caller, so this never clears a field — per-row editing
    handles exceptions and clears.
    """
    ids = payload.get("ids") or []
    changes = {k: v for k, v in (payload.get("set") or {}).items() if v not in ("", None)}
    if not ids or not changes:
        return {"updated": 0, "rows": []}
    rows = db.execute(select(StagingItem).where(
        StagingItem.id.in_(ids))).scalars().all()
    for row in rows:
        for f in ("condition", "printing", "language", "bin", "comment",
                  "quantity", "price"):
            if f in changes:
                setattr(row, f, _clean(f, changes[f]))
        # Cost AND acquired date on a bulk-pull row are ignored at approve time
        # (both come from the pile it's pulled out of), so don't pretend to set
        # them.
        if "cost" in changes and not row.source_bulk_id:
            row.cost = _clean("cost", changes["cost"])
        if "acquired_at" in changes and not row.source_bulk_id:
            row.acquired_at = _parse_date(changes["acquired_at"])
    db.commit()
    return {"updated": len(rows), "rows": [_staging_dict(db, r) for r in rows]}


@router.post("/approve")
def approve(payload: dict = Body(...), db: Session = Depends(get_db)):
    """Approve some or all staged rows (partial approval supported).

    Rows naming an unusable bulk pile come back under ``skipped`` and stay in
    staging, so a bad source never silently discards the card.
    """
    ids = payload.get("ids")
    if ids:
        rows = db.execute(select(StagingItem).where(
            StagingItem.id.in_(ids))).scalars().all()
    else:
        rows = db.execute(select(StagingItem)).scalars().all()
    return staging_svc.approve_staging_rows(db, rows)


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


def _row_fields(r: dict) -> dict:
    """Validated StagingItem kwargs from one manual-entry row.

    Shared by the single-row and multi-row add paths so both reject the same
    things — a quantity of 0, a negative cost, a condition that isn't a real
    condition — rather than only whichever one was patched last.
    """
    return dict(
        catalog_card_id=r.get("catalog_card_id"),
        custom_sku_id=r.get("custom_sku_id"),
        condition=_clean("condition", r.get("condition") or "NM"),
        printing=_clean("printing", r.get("printing") or "normal"),
        language=_clean("language", r.get("language") or "en"),
        bin=r.get("bin", ""),
        quantity=whole(r.get("quantity"), "quantity", default=1, min_value=1),
        cost=_clean("cost", r.get("cost")),
        price=_clean("price", r.get("price")),
        acquired_at=_parse_date(r.get("acquired_at")),
        comment=r.get("comment", ""),
        source_bulk_id=r.get("source_bulk_id"),
    )


@router.post("/manual-add")
def manual_add(payload: dict = Body(...), db: Session = Depends(get_db)):
    """Full-form manual entry. direct=true skips staging (trusted intake)."""
    fields = _row_fields(payload)
    if not fields["catalog_card_id"] and not fields["custom_sku_id"]:
        raise HTTPException(400, "catalog_card_id or custom_sku_id required")
    if payload.get("direct"):
        try:
            inv_id = staging_svc.add_direct(db, **fields)
        except ValueError as e:                       # unusable source bulk pile
            raise HTTPException(400, str(e))
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
        fields = _row_fields(r)
        if direct:
            try:
                staging_svc.add_direct(db, **fields)
            except ValueError as e:                   # unusable source bulk pile
                raise HTTPException(400, str(e))
        else:
            db.add(StagingItem(source="manual", **fields))
        n += 1
    db.commit()
    return {"staged": 0 if direct else n, "added": n if direct else 0, "direct": direct}
