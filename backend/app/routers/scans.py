"""Scan pulls, review queue, confirm/reject, session export."""
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import CatalogCard, ProcessedScan, ScanPull, ScanQueueItem
from ..services import exporting, pricing, scanning, staging as staging_svc
from ..services.settings import get_setting
from .serializers import scan_item_dict

router = APIRouter(prefix="/api/scans", tags=["scans"])


def _scan_dict(db: Session, item: ScanQueueItem) -> dict:
    """scan_item_dict enriched with the card's at-a-glance market value."""
    return scan_item_dict(
        item, market_value=pricing.card_market_value(db, item.card, item.printing))


@router.post("/pull")
def pull(payload: dict = Body(...), db: Session = Depends(get_db)):
    folder = payload.get("folder") or get_setting(db, "scan_folder")
    if not folder:
        raise HTTPException(400, "no scan folder configured")
    game = payload.get("game", "mtg")
    try:
        p = scanning.pull_scans(
            db, folder, game,
            use_subfolder_bins=payload.get("use_subfolder_bins", False),
            pair_front_back=payload.get("pair_front_back", False),
            session_defaults=payload.get("session_defaults") or {},
        )
    except FileNotFoundError as e:
        raise HTTPException(400, str(e))
    return {"pull_id": p.id, "image_count": p.image_count,
            "items": len(p.items)}


@router.get("/pulls")
def list_pulls(db: Session = Depends(get_db)):
    pulls = db.execute(select(ScanPull).order_by(ScanPull.id.desc()).limit(100)).scalars().all()
    out = []
    for p in pulls:
        statuses = [i.status for i in p.items]
        out.append({
            "id": p.id, "folder": p.folder,
            "pulled_at": p.pulled_at.isoformat() if p.pulled_at else None,
            "image_count": p.image_count,
            "resolved": sum(1 for s in statuses if s in ("confirmed", "rejected")),
            "pending": sum(1 for s in statuses if s in ("pending", "needs_review")),
        })
    return out


@router.post("/pulls/delete")
def delete_pulls(payload: dict = Body(...), db: Session = Depends(get_db)):
    """Delete whole batches: forgets their processed-scan hashes (so the
    source images are picked up again by the next pull), and removes their
    queue items and pull records. Cards already confirmed to staging are
    unaffected — StagingItem is copied out independently of the scan queue.
    """
    ids = payload.get("pull_ids", [])
    if not ids:
        raise HTTPException(400, "no pull_ids given")
    hashes = db.execute(select(ProcessedScan).where(
        ProcessedScan.pull_id.in_(ids))).scalars().all()
    for h in hashes:
        db.delete(h)
    items = db.execute(select(ScanQueueItem).where(
        ScanQueueItem.pull_id.in_(ids))).scalars().all()
    for it in items:
        db.delete(it)
    pulls = db.execute(select(ScanPull).where(ScanPull.id.in_(ids))).scalars().all()
    deleted = len(pulls)
    for p in pulls:
        db.delete(p)
    db.commit()
    return {"deleted_pulls": deleted, "cleared_hashes": len(hashes), "removed_items": len(items)}


@router.get("/queue")
def queue(pull_id: int | None = None, status: str = "",
          flagged_only: bool = False, db: Session = Depends(get_db)):
    q = select(ScanQueueItem)
    if pull_id:
        q = q.where(ScanQueueItem.pull_id == pull_id)
    if status:
        q = q.where(ScanQueueItem.status == status)
    else:
        q = q.where(ScanQueueItem.status.in_(["pending", "needs_review"]))
    items = db.execute(q.order_by(ScanQueueItem.seq)).scalars().all()
    if flagged_only:
        items = [i for i in items if i.status == "needs_review" or i.low_resolution]
    return [_scan_dict(db, i) for i in items]


@router.get("/image")
def scan_image(path: str, db: Session = Depends(get_db)):
    """Serve a scan image. Restricted to the configured scan folder tree."""
    root = get_setting(db, "scan_folder")
    p = Path(path).resolve()
    known = db.execute(select(ProcessedScan).where(
        ProcessedScan.file_path == path)).scalars().first()
    allowed = known is not None or (root and str(p).startswith(str(Path(root).resolve())))
    if not allowed or not p.is_file():
        raise HTTPException(404, "image not found")
    return FileResponse(p)


@router.patch("/queue/{item_id}")
def update_item(item_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    item = db.get(ScanQueueItem, item_id)
    if not item:
        raise HTTPException(404)
    for field in ("card_id", "condition", "printing", "language", "bin",
                  "quantity", "cost", "status"):
        if field in payload:
            setattr(item, field, payload[field])
    if "card_id" in payload and payload["card_id"]:
        card = db.get(CatalogCard, payload["card_id"])
        if card is None:
            raise HTTPException(400, "unknown card")
        item.method = item.method or "manual"
        item.confidence = 1.0
        if item.status == "needs_review":
            item.status = "pending"
    db.commit()
    return _scan_dict(db, item)


@router.post("/queue/bulk")
def bulk_update(payload: dict = Body(...), db: Session = Depends(get_db)):
    """Bulk-set condition/printing/language/bin; bulk approve/reject/clear."""
    ids = payload.get("ids", [])
    action = payload.get("action", "set")
    items = db.execute(select(ScanQueueItem).where(
        ScanQueueItem.id.in_(ids))).scalars().all() if ids else []
    if action == "clear_all" and payload.get("pull_id"):
        items = db.execute(select(ScanQueueItem).where(
            ScanQueueItem.pull_id == payload["pull_id"],
            ScanQueueItem.status.in_(["pending", "needs_review"]))).scalars().all()
        for it in items:
            it.status = "rejected"
        db.commit()
        return {"rejected": len(items)}
    count = 0
    for it in items:
        if action == "set":
            for f in ("condition", "printing", "language", "bin", "quantity", "cost"):
                if f in payload.get("values", {}):
                    setattr(it, f, payload["values"][f])
            count += 1
        elif action == "approve":
            if it.card_id is None:
                continue
            staging_svc.stage_scan_item(db, it)
            count += 1
        elif action == "reject":
            # source image never deleted; can be re-pulled by clearing its hash
            it.status = "rejected"
            count += 1
    db.commit()
    return {"affected": count}


@router.post("/queue/{item_id}/confirm")
def confirm(item_id: int, db: Session = Depends(get_db)):
    item = db.get(ScanQueueItem, item_id)
    if not item:
        raise HTTPException(404)
    if item.card_id is None:
        raise HTTPException(400, "no card selected")
    row = staging_svc.stage_scan_item(db, item)
    db.commit()
    return {"staging_id": row.id}


@router.post("/queue/{item_id}/reject")
def reject(item_id: int, db: Session = Depends(get_db)):
    item = db.get(ScanQueueItem, item_id)
    if not item:
        raise HTTPException(404)
    item.status = "rejected"
    db.commit()
    return {"ok": True}


@router.post("/rehash")
def clear_hash(payload: dict = Body(...), db: Session = Depends(get_db)):
    """Clear a processed-scan hash so the image is picked up by the next pull."""
    path = payload.get("path")
    rows = db.execute(select(ProcessedScan).where(
        ProcessedScan.file_path == path)).scalars().all()
    for r in rows:
        db.delete(r)
    db.commit()
    return {"cleared": len(rows)}


@router.get("/export")
def export_session(pull_id: int, fmt: str = "csv", db: Session = Depends(get_db)):
    """Export a pull's recognized cards (rejected/zero-qty excluded)."""
    items = db.execute(select(ScanQueueItem).where(
        ScanQueueItem.pull_id == pull_id,
        ScanQueueItem.status != "rejected",
        ScanQueueItem.quantity > 0)).scalars().all()
    headers = ["Name", "Set", "Collector #", "Condition", "Printing",
               "Language", "Bin", "Quantity", "Cost", "Confidence", "File"]
    rows = [[i.card.name if i.card else "?",
             i.card.set_code if i.card else "",
             i.card.collector_number if i.card else "",
             i.condition, i.printing, i.language, i.bin, i.quantity,
             i.cost or "", round(i.confidence, 2), i.file_name] for i in items]
    if fmt == "xlsx":
        data = exporting.to_xlsx_bytes(headers, rows)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        name = f"scan-pull-{pull_id}.xlsx"
    else:
        data = exporting.to_csv_bytes(headers, rows)
        media, name = "text/csv", f"scan-pull-{pull_id}.csv"
    return Response(data, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="{name}"'})
