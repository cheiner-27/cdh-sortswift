"""Settings, reports, inventory labels."""
from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..domain import (
    BULK_GRADES, CANONICAL_PRINTINGS, CONDITIONS, CUSTOM_CATEGORIES, GAMES,
    LANGUAGES, MARKETPLACES, PRICE_SOURCES, RARITY_TIERS, ROUNDING_OPTIONS,
)
from ..services import reports as report_svc
from ..services.settings import all_settings, set_setting
from .inventory import filter_items

router = APIRouter(prefix="/api", tags=["misc"])


@router.get("/meta")
def meta():
    from ..services.orders import PICK_SORT_FIELDS
    return {"games": GAMES, "conditions": CONDITIONS,
            "printings": CANONICAL_PRINTINGS, "languages": LANGUAGES,
            "marketplaces": MARKETPLACES, "rarities": RARITY_TIERS,
            "custom_categories": CUSTOM_CATEGORIES,
            "price_sources": PRICE_SOURCES, "rounding_options": ROUNDING_OPTIONS,
            "pick_sort_fields": list(PICK_SORT_FIELDS),
            # Bulk grade columns per game, so Settings (rates) and the Bulk page
            # (per-pile mix) render the same list without hardcoding either.
            "bulk_grades": {game: [{"key": k, "label": lbl} for k, lbl, _ in grades]
                            for game, grades in BULK_GRADES.items()}}


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    return all_settings(db)


@router.put("/settings")
def put_settings(payload: dict = Body(...), db: Session = Depends(get_db)):
    for k, v in payload.items():
        set_setting(db, k, v)
    db.commit()
    return all_settings(db)


@router.get("/reports/pnl")
def pnl(group_by: str = "month", db: Session = Depends(get_db)):
    return report_svc.realized_pnl(db, group_by=group_by)


@router.get("/reports/aging")
def aging(db: Session = Depends(get_db)):
    return report_svc.aging_report(db)


@router.get("/reports/locations")
def locations(db: Session = Depends(get_db)):
    return report_svc.location_summary(db)


@router.post("/labels/inventory")
def inventory_labels(payload: dict = Body(default={}), db: Session = Depends(get_db)):
    """Data for printable price/SKU/bin labels (rendered client-side, printed
    via the browser/Windows print dialog). Two fixed layouts."""
    from ..services.exporting import internal_sku
    items = filter_items(db, payload.get("filter", {}))
    if payload.get("ids"):
        items = [i for i in items if i.id in payload["ids"]]
    return {"layout": payload.get("layout", "standard"), "labels": [{
        "name": i.card.name if i.card else (
            i.custom_sku.product.name if i.custom_sku else "?"),
        "set_code": i.card.set_code if i.card else "",
        "collector_number": i.card.collector_number if i.card else "",
        "condition": i.condition, "printing": i.printing,
        "price": i.price_override or i.current_price,
        "sku": internal_sku(i), "bin": i.bin,
        "comment": i.comment,
    } for i in items]}
