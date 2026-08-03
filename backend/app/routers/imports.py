"""CSV import: preview, run, disambiguation, undo."""
import base64

from fastapi import APIRouter, Body, Depends, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ImportBatch, ImportRow
from ..services import importing
from ..validate import whole
from .serializers import import_batch_dict

router = APIRouter(prefix="/api/imports", tags=["imports"])


@router.get("/system-fields")
def system_fields():
    return importing.SYSTEM_FIELDS


@router.post("/preview")
async def preview(file: UploadFile, db: Session = Depends(get_db)):
    """Upload a CSV; return headers + sample rows + file token for the run step."""
    content = await file.read()
    headers, rows = importing.parse_csv(content)
    return {
        "headers": headers,
        "sample_rows": rows[:10],
        "row_count": len(rows),
        "file_b64": base64.b64encode(content).decode(),
        "filename": file.filename,
    }


@router.post("/preview-mapped")
def preview_mapped(payload: dict = Body(...), db: Session = Depends(get_db)):
    """Show sample rows with the mapping applied, before committing."""
    content = base64.b64decode(payload["file_b64"])
    _, rows = importing.parse_csv(content)
    mapping = payload.get("mapping", {})
    value_maps = payload.get("value_maps")
    return [importing.apply_mapping(r, mapping, value_maps) for r in rows[:10]]


@router.post("/run")
def run(payload: dict = Body(...), db: Session = Depends(get_db)):
    content = base64.b64decode(payload["file_b64"])
    mode = payload.get("mode", "add")
    if mode not in ("add", "overwrite", "deduction"):
        raise HTTPException(400, "mode must be add | overwrite | deduction")
    batch = importing.run_import(
        db,
        filename=payload.get("filename", "upload.csv"),
        content=content,
        mapping=payload.get("mapping", {}),
        value_maps=payload.get("value_maps"),
        mode=mode,
        to_staging=payload.get("to_staging", True),
    )
    return import_batch_dict(batch, with_rows=True)


@router.get("/batches")
def batches(db: Session = Depends(get_db)):
    rows = db.execute(select(ImportBatch).order_by(ImportBatch.id.desc()).limit(100)).scalars().all()
    return [import_batch_dict(b) for b in rows]


@router.get("/batches/{batch_id}")
def batch_detail(batch_id: int, db: Session = Depends(get_db)):
    b = db.get(ImportBatch, batch_id)
    if not b:
        raise HTTPException(404)
    return import_batch_dict(b, with_rows=True)


@router.post("/rows/{row_id}/resolve")
def resolve_row(row_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    """Manual disambiguation: user picked the correct card for an ambiguous row."""
    row = db.get(ImportRow, row_id)
    if not row:
        raise HTTPException(404)
    if row.status != "ambiguous":
        raise HTTPException(400, "row is not ambiguous")
    try:
        # The row-level import data stays permissive (see services/importing);
        # this is the user clicking a specific candidate, so it's an id or a 400.
        card_id = whole(payload.get("card_id"), "card_id", min_value=1,
                        max_value=None)
        importing.resolve_ambiguous_row(db, row, card_id,
                                        payload.get("to_staging", True))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"status": row.status}


@router.post("/batches/{batch_id}/undo")
def undo(batch_id: int, db: Session = Depends(get_db)):
    b = db.get(ImportBatch, batch_id)
    if not b:
        raise HTTPException(404)
    try:
        return importing.undo_import(db, b)
    except ValueError as e:
        raise HTTPException(400, str(e))
