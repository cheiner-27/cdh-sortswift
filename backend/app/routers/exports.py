"""Inventory export: column picker, layouts, templates, out-of-stock list."""
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ExportTemplate, InventoryItem
from ..services import exporting
from .inventory import filter_items

router = APIRouter(prefix="/api/exports", tags=["exports"])


@router.get("/columns")
def columns():
    return [{"key": k, "header": v[0]} for k, v in exporting.NATIVE_COLUMNS.items()]


def _respond(headers, rows, fmt: str, name: str):
    if fmt == "xlsx":
        data = exporting.to_xlsx_bytes(headers, rows)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        name += ".xlsx"
    else:
        data = exporting.to_csv_bytes(headers, rows)
        media = "text/csv"
        name += ".csv"
    return Response(data, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/inventory")
def export_inventory(payload: dict = Body(default={}), db: Session = Depends(get_db)):
    items = filter_items(db, payload.get("filter", {}))
    headers, rows = exporting.build_export(
        db, items,
        columns=payload.get("columns"),
        layout=payload.get("layout", "native"),
        exclude_zero=payload.get("exclude_zero", True),
        merge_duplicates=payload.get("merge_duplicates", False),
    )
    return _respond(headers, rows, payload.get("format", "csv"), "inventory")


@router.post("/out-of-stock")
def out_of_stock(payload: dict = Body(default={}), db: Session = Depends(get_db)):
    """Previously-stocked, now-zero-quantity items — restock candidates."""
    items = db.execute(select(InventoryItem).where(
        InventoryItem.quantity == 0,
        InventoryItem.deleted == False)).scalars().all()  # noqa: E712
    headers, rows = exporting.build_export(
        db, items, columns=payload.get("columns"), exclude_zero=False)
    return _respond(headers, rows, payload.get("format", "csv"), "out-of-stock")


@router.get("/templates")
def list_templates(db: Session = Depends(get_db)):
    return [{"id": t.id, "name": t.name, "columns": t.columns,
             "layout": t.layout, "options": t.options}
            for t in db.execute(select(ExportTemplate)).scalars()]


@router.post("/templates")
def save_template(payload: dict = Body(...), db: Session = Depends(get_db)):
    existing = db.execute(select(ExportTemplate).where(
        ExportTemplate.name == payload["name"])).scalars().first()
    if existing:
        existing.columns = payload.get("columns", [])
        existing.layout = payload.get("layout", "native")
        existing.options = payload.get("options", {})
    else:
        db.add(ExportTemplate(name=payload["name"],
                              columns=payload.get("columns", []),
                              layout=payload.get("layout", "native"),
                              options=payload.get("options", {})))
    db.commit()
    return {"ok": True}


@router.delete("/templates/{template_id}")
def delete_template(template_id: int, db: Session = Depends(get_db)):
    t = db.get(ExportTemplate, template_id)
    if not t:
        raise HTTPException(404)
    db.delete(t)
    db.commit()
    return {"ok": True}
