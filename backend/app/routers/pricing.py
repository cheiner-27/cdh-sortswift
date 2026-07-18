"""Pricing configs, simulation, manual reprice trigger."""
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..domain import GAMES
from ..models import PricingConfig
from ..services import pricing as pricing_svc
from ..services.settings import get_setting
from .inventory import filter_items

router = APIRouter(prefix="/api/pricing", tags=["pricing"])


@router.get("/config/{game}")
def get_config(game: str, db: Session = Depends(get_db)):
    if game not in GAMES:
        raise HTTPException(404, "unknown game")
    return pricing_svc.get_config(db, game)


@router.put("/config/{game}")
def put_config(game: str, config: dict = Body(...), db: Session = Depends(get_db)):
    if game not in GAMES:
        raise HTTPException(404, "unknown game")
    # tiers must be non-overlapping bands; only the last may be open-ended
    tiers = sorted(config.get("tiers", []), key=lambda t: t.get("min") or 0)
    for a, b in zip(tiers, tiers[1:]):
        if a.get("max") is None:
            raise HTTPException(400, "only the last tier may be open-ended (max=null)")
        if (b.get("min") or 0) < a["max"] - 1e-9:
            raise HTTPException(400, f"tiers overlap: {a.get('name')} / {b.get('name')}")
    row = db.execute(select(PricingConfig).where(
        PricingConfig.game == game)).scalars().first()
    if row is None:
        row = PricingConfig(game=game, config=config)
        db.add(row)
    else:
        row.config = config
    db.commit()
    return {"ok": True}


@router.post("/simulate/{marketplace}")
def simulate(marketplace: str, payload: dict = Body(default={}),
             db: Session = Depends(get_db)):
    items = filter_items(db, {**payload.get("filter", {}), "in_stock_only": True})
    results = pricing_svc.simulate(
        db, marketplace, items,
        large_move_pct=float(get_setting(db, "large_move_pct")))
    return {"count": len(results), "results": results}


@router.post("/apply/{marketplace}")
def apply(marketplace: str, payload: dict = Body(default={}),
          db: Session = Depends(get_db)):
    """Manual reprice trigger (no scheduler by design)."""
    items = filter_items(db, {**payload.get("filter", {}), "in_stock_only": True})
    return pricing_svc.apply_reprice(db, marketplace, items)


@router.post("/preview-item/{marketplace}/{item_id}")
def preview_item(marketplace: str, item_id: int, db: Session = Depends(get_db)):
    from ..models import InventoryItem
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(404)
    return pricing_svc.price_item(db, item, marketplace)
