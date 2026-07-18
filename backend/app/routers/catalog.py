"""Catalog: search, sets, sync triggers, price feed."""
from fastapi import APIRouter, Body, Depends, HTTPException, UploadFile
from sqlalchemy import Integer, case, cast, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import CatalogCard, CatalogSet, PriceData, collector_number_key
from ..services import catalog as cat_svc
from ..services import scanning
from .serializers import card_dict

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


@router.get("/search")
def search_cards(q: str = "", game: str = "", set_code: str = "",
                 collector_number: str = "", limit: int = 50, offset: int = 0,
                 db: Session = Depends(get_db)):
    """Search / browse the catalog. Any combination of name (`q`), `set_code`,
    and `collector_number` narrows results; with no `q` (e.g. just a set) it
    lists that set in collector-number order — so you can pull up a whole set.
    Name matches are ranked exact -> starts-with -> contains so the card you
    typed isn't buried under longer names."""
    query = select(CatalogCard).where(CatalogCard.is_sealed == False)  # noqa: E712
    if game:
        query = query.where(CatalogCard.game == game)
    if set_code:
        query = query.where(CatalogCard.set_code.ilike(f"%{set_code}%"))
    if collector_number:
        key = collector_number_key(collector_number)
        query = query.where(or_(CatalogCard.collector_number == collector_number,
                                CatalogCard.collector_number_norm == key))
    if q:
        query = query.where(or_(CatalogCard.name.ilike(f"%{q}%"),
                                CatalogCard.collector_number == q,
                                CatalogCard.collector_number_norm == collector_number_key(q)))
        rank = case((CatalogCard.name.ilike(q), 0),          # exact (no wildcards)
                    (CatalogCard.name.ilike(f"{q}%"), 1),     # starts-with
                    else_=2)                                  # contains
        query = query.order_by(rank, CatalogCard.name, CatalogCard.set_name,
                               cast(CatalogCard.collector_number_norm, Integer))
    else:  # browse mode (e.g. a whole set) — collector-number order
        query = query.order_by(CatalogCard.set_name,
                               cast(CatalogCard.collector_number_norm, Integer),
                               CatalogCard.collector_number)
    cards = db.execute(query.offset(offset).limit(limit)).scalars().all()
    return [card_dict(c) for c in cards]


@router.get("/card/{card_id}")
def get_card(card_id: int, db: Session = Depends(get_db)):
    """Full detail for one catalog card, with any known TCGplayer prices."""
    c = db.get(CatalogCard, card_id)
    if c is None:
        raise HTTPException(404, "card not found")
    prices = []
    if c.tcgplayer_product_id:
        rows = db.execute(
            select(PriceData).where(
                PriceData.tcgplayer_product_id == c.tcgplayer_product_id)
        ).scalars().all()
        prices = [{
            "sub_type": r.sub_type, "market": r.market, "mid": r.mid,
            "low": r.low, "direct_low": r.direct_low,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        } for r in rows]
    return {"card": card_dict(c), "prices": prices}


@router.get("/sets")
def list_sets(game: str = "", db: Session = Depends(get_db)):
    q = select(CatalogSet)
    if game:
        q = q.where(CatalogSet.game == game)
    return [{"game": s.game, "code": s.code, "name": s.name,
             "release_date": s.release_date}
            for s in db.execute(q.order_by(CatalogSet.game, CatalogSet.name)).scalars()]


@router.get("/stats")
def catalog_stats(db: Session = Depends(get_db)):
    out = {}
    for game in ("mtg", "pokemon", "onepiece", "yugioh"):
        out[game] = {
            "cards": db.query(CatalogCard).filter(CatalogCard.game == game).count(),
            "sets": db.query(CatalogSet).filter(CatalogSet.game == game).count(),
            "phashes": db.query(CatalogCard).filter(
                CatalogCard.game == game, CatalogCard.phash.isnot(None)).count(),
        }
    out["price_rows"] = db.query(PriceData).count()
    return out


@router.post("/sync/sets")
def sync_sets(payload: dict = Body(...), db: Session = Depends(get_db)):
    game = payload.get("game")
    try:
        if game == "mtg":
            return {"synced": cat_svc.sync_mtg_sets(db)}
        if game == "pokemon":
            return {"synced": cat_svc.sync_pokemon_catalog(db), "note": "pokemon syncs sets+cards together"}
        if game == "yugioh":
            return {"synced": cat_svc.sync_yugioh_cards(db), "note": "yugioh syncs sets+cards together"}
        if game == "onepiece":
            return {"synced": cat_svc.sync_onepiece_catalog(db), "note": "onepiece syncs sets+cards together"}
    except Exception as e:  # network errors surfaced to UI
        raise HTTPException(502, f"catalog sync failed: {e}")
    raise HTTPException(400, "unknown game")


@router.post("/sync/all")
def sync_all(payload: dict = Body(...), db: Session = Depends(get_db)):
    """One-shot full catalog sync for a game (sets + every card)."""
    game = payload.get("game")
    try:
        if game == "mtg":
            return {"sets": cat_svc.sync_mtg_sets(db),
                    "cards": cat_svc.sync_mtg_all_cards(db)}
        if game == "pokemon":
            return {"cards": cat_svc.sync_pokemon_catalog(db)}
        if game == "yugioh":
            return {"cards": cat_svc.sync_yugioh_cards(db)}
        if game == "onepiece":
            return {"cards": cat_svc.sync_onepiece_catalog(db)}
    except Exception as e:  # network errors surfaced to UI
        raise HTTPException(502, f"catalog sync failed: {e}")
    raise HTTPException(400, "unknown game")


@router.post("/sync/cards")
def sync_cards(payload: dict = Body(...), db: Session = Depends(get_db)):
    game, set_code = payload.get("game"), payload.get("set_code")
    if not set_code:
        raise HTTPException(400, "set_code required")
    try:
        if game == "mtg":
            return {"synced": cat_svc.sync_mtg_set_cards(db, set_code)}
        if game == "pokemon":
            return {"synced": cat_svc.sync_pokemon_set_cards(db, set_code)}
    except Exception as e:
        raise HTTPException(502, f"card sync failed: {e}")
    raise HTTPException(400, "per-set sync only for mtg/pokemon; yugioh/onepiece sync whole catalog via /sync/sets")


@router.post("/backfill-ids")
def backfill_ids(payload: dict = Body(default={}), db: Session = Depends(get_db)):
    """Backfill missing TCGplayer product ids on MTG cards (tokens/promos that
    Scryfall left without one) from TCGcsv, so they can be priced/exported."""
    game = payload.get("game", "mtg")
    if game != "mtg":
        raise HTTPException(400, "backfill only applies to MTG (other games sync ids from TCGcsv natively)")
    try:
        return cat_svc.backfill_mtg_tcgplayer_ids(db)
    except Exception as e:
        raise HTTPException(502, f"backfill failed: {e}")


@router.post("/dedupe-pokemon")
def dedupe_pokemon(payload: dict = Body(default={}), db: Session = Depends(get_db)):
    """Merge leftover pokemontcg.io Pokémon cards into their TCGcsv twins and
    remove the resulting duplicate sets. Repoints references only (no inventory
    row is deleted); idempotent."""
    try:
        return cat_svc.deduplicate_pokemon_catalog(db)
    except Exception as e:
        raise HTTPException(502, f"dedupe failed: {e}")


@router.post("/sync/prices")
def sync_prices(payload: dict = Body(...), db: Session = Depends(get_db)):
    game = payload.get("game")
    if game not in cat_svc.TCGPLAYER_CATEGORIES:
        raise HTTPException(400, "unknown game")
    try:
        return {"synced": cat_svc.sync_tcgcsv_prices(db, game)}
    except Exception as e:
        raise HTTPException(502, f"price sync failed: {e}")


@router.post("/prices/upload")
async def upload_prices_csv(file: UploadFile, db: Session = Depends(get_db)):
    content = await file.read()
    return {"imported": cat_svc.import_prices_csv(db, content)}


@router.post("/phash/build")
def build_phashes(payload: dict = Body(...), db: Session = Depends(get_db)):
    game = payload.get("game")
    if not game:
        raise HTTPException(400, "game required")
    n = scanning.build_catalog_phashes(
        db, game, payload.get("set_code"), payload.get("limit"))
    return {"built": n}
