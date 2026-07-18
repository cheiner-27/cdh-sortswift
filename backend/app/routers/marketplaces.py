"""Marketplace accounts, listing rules, sync operations, order polling."""
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..domain import MARKETPLACES
from ..models import InventoryItem, ListingRule, MarketplaceAccount, MarketplaceListing
from ..services.marketplaces import sync as sync_svc
from ..services.marketplaces import tcgplayer as tcg_csv
from .inventory import filter_items

router = APIRouter(prefix="/api/marketplaces", tags=["marketplaces"])


@router.get("/accounts")
def accounts(db: Session = Depends(get_db)):
    out = []
    for mk in MARKETPLACES:
        acct = sync_svc.account(db, mk)
        out.append({
            "marketplace": mk,
            "status": acct.status if acct else "disconnected",
            "poll_interval_minutes": acct.poll_interval_minutes if acct else 10,
            "auto_push_on_add": acct.auto_push_on_add if acct else False,
            "has_credentials": bool(acct and acct.credentials),
            "dry_run": bool(acct and acct.credentials.get("dry_run")),
            "last_order_poll_at": (acct.last_order_poll_at.isoformat()
                                   if acct and acct.last_order_poll_at else None),
        })
    return out


@router.put("/accounts/{marketplace}")
def update_account(marketplace: str, payload: dict = Body(...),
                   db: Session = Depends(get_db)):
    if marketplace not in MARKETPLACES:
        raise HTTPException(404)
    acct = sync_svc.account(db, marketplace)
    if acct is None:
        acct = MarketplaceAccount(marketplace=marketplace)
        db.add(acct)
    if "credentials" in payload:
        acct.credentials = payload["credentials"]
    if "status" in payload:  # connected | paused | disconnected
        if payload["status"] not in ("connected", "paused", "disconnected"):
            raise HTTPException(400, "bad status")
        acct.status = payload["status"]
        if payload["status"] == "disconnected":
            acct.credentials = {}  # Disconnect revokes credentials
    if "poll_interval_minutes" in payload:
        acct.poll_interval_minutes = int(payload["poll_interval_minutes"])
    if "auto_push_on_add" in payload:
        acct.auto_push_on_add = bool(payload["auto_push_on_add"])
    db.commit()
    return {"ok": True}


# --- Listing rules ------------------------------------------------------------

@router.get("/rules")
def rules(marketplace: str = "", db: Session = Depends(get_db)):
    q = select(ListingRule).order_by(ListingRule.priority)
    if marketplace:
        q = q.where(ListingRule.marketplace == marketplace)
    return [{
        "id": r.id, "marketplace": r.marketplace, "name": r.name,
        "priority": r.priority, "active": r.active, "filters": r.filters,
        "condition_allowlist": r.condition_allowlist,
        "block_sealed": r.block_sealed, "block_singles": r.block_singles,
        "ebay_fulfillment_policy_id": r.ebay_fulfillment_policy_id,
        "ebay_payment_policy_id": r.ebay_payment_policy_id,
        "ebay_return_policy_id": r.ebay_return_policy_id,
        "ebay_category_id": r.ebay_category_id, "best_offer": r.best_offer,
    } for r in db.execute(q).scalars()]


@router.post("/rules")
def create_rule(payload: dict = Body(...), db: Session = Depends(get_db)):
    rule = ListingRule(
        marketplace=payload["marketplace"], name=payload.get("name", "rule"),
        priority=payload.get("priority", 0), active=payload.get("active", True),
        filters=payload.get("filters", {}),
        condition_allowlist=payload.get("condition_allowlist", []),
        block_sealed=payload.get("block_sealed", False),
        block_singles=payload.get("block_singles", False),
        ebay_fulfillment_policy_id=payload.get("ebay_fulfillment_policy_id"),
        ebay_payment_policy_id=payload.get("ebay_payment_policy_id"),
        ebay_return_policy_id=payload.get("ebay_return_policy_id"),
        ebay_category_id=payload.get("ebay_category_id"),
        best_offer=payload.get("best_offer", {}))
    db.add(rule)
    db.commit()
    return {"id": rule.id}


@router.put("/rules/{rule_id}")
def update_rule(rule_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    rule = db.get(ListingRule, rule_id)
    if not rule:
        raise HTTPException(404)
    for f in ("name", "priority", "active", "filters", "condition_allowlist",
              "block_sealed", "block_singles", "ebay_fulfillment_policy_id",
              "ebay_payment_policy_id", "ebay_return_policy_id",
              "ebay_category_id", "best_offer"):
        if f in payload:
            setattr(rule, f, payload[f])
    db.commit()
    return {"ok": True}


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = db.get(ListingRule, rule_id)
    if not rule:
        raise HTTPException(404)
    db.delete(rule)
    db.commit()
    return {"ok": True}


# --- Sync operations (Section 6.3) ---------------------------------------------

def _items_for(db: Session, payload: dict) -> list[InventoryItem]:
    return filter_items(db, {**payload.get("filter", {}), "in_stock_only": False})


@router.post("/{marketplace}/resync")
def resync(marketplace: str, payload: dict = Body(default={}),
           db: Session = Depends(get_db)):
    return sync_svc.resync(db, marketplace, _items_for(db, payload),
                           changed_only=payload.get("changed_only", True))


@router.post("/{marketplace}/push-remaining")
def push_remaining(marketplace: str, payload: dict = Body(default={}),
                   db: Session = Depends(get_db)):
    return sync_svc.push_remaining(db, marketplace, _items_for(db, payload))


@router.post("/{marketplace}/clear-ids")
def clear_ids(marketplace: str, payload: dict = Body(default={}),
              db: Session = Depends(get_db)):
    return {"cleared": sync_svc.clear_listing_ids(db, marketplace,
                                                  _items_for(db, payload))}


@router.post("/{marketplace}/rebuild")
def rebuild(marketplace: str, payload: dict = Body(default={}),
            db: Session = Depends(get_db)):
    return sync_svc.rebuild(db, marketplace, _items_for(db, payload))


@router.post("/{marketplace}/sync-item/{item_id}")
def sync_item(marketplace: str, item_id: int, db: Session = Depends(get_db)):
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(404)
    return sync_svc.sync_single(db, marketplace, item)


@router.post("/{marketplace}/poll-orders")
def poll_orders(marketplace: str, db: Session = Depends(get_db)):
    return sync_svc.poll_orders(db, marketplace)


@router.get("/errors")
def listing_errors(db: Session = Depends(get_db)):
    """Per-item listing errors, surfaced in the UI (Section 6.4)."""
    rows = db.execute(select(MarketplaceListing).where(
        MarketplaceListing.error_code.isnot(None))).scalars().all()
    from .serializers import inventory_dict
    return [{
        "listing_id": l.id, "marketplace": l.marketplace,
        "error_code": l.error_code, "error_message": l.error_message,
        "item": inventory_dict(l.item) if l.item else None,
    } for l in rows]


# --- TCGplayer CSV fallback -----------------------------------------------------

@router.post("/tcgplayer/export-listing-csv")
def tcg_listing_csv(payload: dict = Body(default={}), db: Session = Depends(get_db)):
    items = filter_items(db, {**payload.get("filter", {}), "in_stock_only": True})
    data = tcg_csv.export_listing_csv(db, items)
    return Response(data, media_type="text/csv", headers={
        "Content-Disposition": 'attachment; filename="tcgplayer-listings.csv"'})


@router.post("/tcgplayer/export-deduction-csv")
def tcg_deduction_csv(payload: dict = Body(default={}), db: Session = Depends(get_db)):
    """Cross-channel deduction CSV: eBay-sold items to reflect on TCGplayer.
    TCGplayer-originated sales are excluded (never double-deduct)."""
    from ..models import Order
    since = payload.get("since")  # ISO date optional
    orders = db.execute(select(Order).where(
        Order.marketplace == "ebay",
        Order.deduction_applied == True)).scalars().all()  # noqa: E712
    if since:
        orders = [o for o in orders
                  if o.ordered_at and o.ordered_at.isoformat() >= since]
    rows = []
    for o in orders:
        for li in o.items:
            if not li.inventory_id:
                continue
            item = db.get(InventoryItem, li.inventory_id)
            if not item or not item.card or not item.card.tcgplayer_product_id:
                continue
            rows.append({
                "tcgplayer_product_id": item.card.tcgplayer_product_id,
                "name": item.card.name, "set_name": item.card.set_name,
                "condition": item.condition, "quantity": li.quantity,
            })
    data = tcg_csv.export_deduction_csv(db, rows)
    return Response(data, media_type="text/csv", headers={
        "Content-Disposition": 'attachment; filename="tcgplayer-deductions.csv"'})
