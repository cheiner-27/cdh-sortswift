"""Packing-slip order intake: parse -> review -> live orders (Section 8).

TCGplayer has no live order API here (``TcgplayerAdapter.fetch_orders`` returns
nothing) and the deduction CSV import only decrements stock without recording a
sale, so before this path a TCGplayer order never became an ``Order`` at all —
no revenue, no COGS, no fee. Uploading the packing slips is what turns them into
orders you can pull a pick list from.

The flow deliberately mirrors staging: nothing touches live data on upload.
Parsed orders sit in ``SlipOrder`` rows until reviewed, and each order commits
independently — one unrecognizable card blocks its own order and leaves the rest
of the batch free to go through.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain import normalize_condition, normalize_printing
from ..models import (CatalogCard, InventoryItem, Order, OrderItem, SlipBatch,
                      SlipOrder, collector_number_key, name_key)
from . import orders as order_svc
from . import pdf_slips
from .inventory import item_description

log = logging.getLogger(__name__)

# Packing slips print TCGplayer's product-line label; map it to our game codes.
GAME_LABELS = {
    "magic": "mtg", "pokemon": "pokemon", "pokémon": "pokemon",
    "one piece": "onepiece", "one piece card game": "onepiece",
    "yu-gi-oh": "yugioh", "yu-gi-oh!": "yugioh", "yugioh": "yugioh",
}

MATCHED = "matched"
UNMATCHED = "unmatched"
AMBIGUOUS = "ambiguous"


def _game_code(label: str | None) -> str | None:
    return GAME_LABELS.get((label or "").strip().lower())


def _candidate(item: InventoryItem) -> dict:
    return {"inventory_id": item.id, "catalog_card_id": item.catalog_card_id,
            "label": item_description(item), "bin": item.bin,
            "quantity": item.quantity}


def _find_card(db: Session, line: dict) -> tuple[CatalogCard | None, list[CatalogCard]]:
    """Resolve a slip line to a catalog card.

    The slip prints set *name*, collector number, and card name — no set code and
    no product id — so matching keys on (game, set name, collector number), which
    is unique in practice, and falls back to (game, name) when a set name has
    drifted between TCGplayer and our catalog source.
    """
    game = _game_code(line.get("game_label"))
    number = collector_number_key(line.get("collector_number"))
    set_name = (line.get("set_name") or "").strip()

    q = select(CatalogCard)
    if game:
        q = q.where(CatalogCard.game == game)
    if number:
        by_number = db.execute(
            q.where(CatalogCard.collector_number_norm == number,
                    func.lower(CatalogCard.set_name) == set_name.lower())
        ).scalars().all()
        if len(by_number) == 1:
            return by_number[0], []
        if by_number:
            return None, by_number

    name = name_key(line.get("card_name"))
    if name:
        by_name = db.execute(q.where(CatalogCard.name_norm == name)).scalars().all()
        if number:
            narrowed = [c for c in by_name
                        if collector_number_key(c.collector_number) == number]
            if len(narrowed) == 1:
                return narrowed[0], []
            if narrowed:
                return None, narrowed
        if len(by_name) == 1:
            return by_name[0], []
        return None, by_name[:20]
    return None, []


def match_line(db: Session, line: dict) -> dict:
    """Attach catalog/inventory identity and a match status to one slip line.

    A line matches only when exactly one in-stock inventory record fits the
    card + condition + printing the buyer actually bought. Because an inventory
    record's identity includes its bin, the same card can legitimately sit in
    several rows; picking one arbitrarily would send you to the wrong shelf, so
    multiple in-stock rows are reported as candidates instead.
    """
    line = dict(line)
    line["catalog_card_id"] = None
    line["inventory_id"] = None
    line["candidates"] = []
    line["match_note"] = ""

    if not line.get("parse_ok"):
        line["match_status"] = UNMATCHED
        line["match_note"] = "could not read this line's description"
        return line

    game = _game_code(line.get("game_label"))
    condition = normalize_condition(line.get("condition_label") or "")
    printing = normalize_printing(line.get("printing") or "normal", game)
    line["condition"] = condition
    line["printing_canonical"] = printing

    card, candidates = _find_card(db, line)
    if card is None:
        line["match_status"] = AMBIGUOUS if candidates else UNMATCHED
        line["candidates"] = [{"catalog_card_id": c.id,
                               "label": f"{c.name} — {c.set_name or c.set_code} "
                                        f"#{c.collector_number}"}
                              for c in candidates]
        line["match_note"] = ("several catalog cards fit this line"
                              if candidates else "no catalog card found")
        return line

    line["catalog_card_id"] = card.id
    rows = db.execute(select(InventoryItem).where(
        InventoryItem.catalog_card_id == card.id,
        InventoryItem.condition == condition,
        InventoryItem.printing == printing,
        InventoryItem.deleted == False,  # noqa: E712
    )).scalars().all()
    in_stock = [r for r in rows if r.quantity > 0]
    if len(in_stock) == 1:
        line["inventory_id"] = in_stock[0].id
        line["match_status"] = MATCHED
        if in_stock[0].quantity < (line.get("quantity") or 1):
            line["match_note"] = (f"only {in_stock[0].quantity} in stock, "
                                  f"{line.get('quantity')} sold")
        return line
    if in_stock:
        line["match_status"] = AMBIGUOUS
        line["candidates"] = [_candidate(r) for r in in_stock]
        line["match_note"] = "in stock in more than one bin — pick which"
        return line
    line["match_status"] = UNMATCHED
    line["candidates"] = [_candidate(r) for r in rows]
    line["match_note"] = (
        f"no {condition}/{printing} stock on hand"
        if rows else f"card found but no {condition}/{printing} inventory record")
    return line


def _parse_date(value: str | None) -> datetime | None:
    """Slips print MM/DD/YYYY; keep it at midnight UTC like other sale dates."""
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%m/%d/%Y").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None


def refresh(db: Session, slip: SlipOrder) -> SlipOrder:
    """Recompute a slip order's fee and ready/blocked status from its lines."""
    fee = order_svc.estimate_marketplace_fee(
        db, subtotal=slip.item_total, shipping_charged=slip.shipping_charged,
        tax=slip.tax, state=slip.ship_state)
    slip.estimated_fee = fee["fee"]
    slip.fee_detail = fee
    if slip.status not in ("committed", "duplicate"):
        unresolved = [l for l in (slip.lines or [])
                      if l.get("match_status") != MATCHED]
        if unresolved or not slip.lines:
            slip.status = "blocked"
            slip.error = (f"{len(unresolved)} line(s) need attention"
                          if unresolved else "no line items parsed")
        elif not slip.reconciled:
            slip.status = "blocked"
            slip.error = ("parsed lines don't match the printed total — "
                          "check the slip")
        else:
            slip.status = "ready"
            slip.error = None
    return slip


def _look_alike(db: Session, marketplace: str, slip: SlipOrder) -> str | None:
    """Warn about an order that looks like this sale under a different id.

    Duplicate detection keys on the order number, which only catches sales this
    path recorded. Sales that arrived another way carry a foreign id — the
    Airtable migration used ``airtable-SALE-*`` — so the same physical sale could
    be recorded twice, double-counting its revenue with nothing to notice it.
    Same marketplace, same day, same total is a strong enough signal to raise,
    and far too weak to enforce, so this only ever warns.
    """
    if slip.ordered_at is None or not slip.item_total:
        return None
    day = slip.ordered_at.replace(hour=0, minute=0, second=0, microsecond=0)
    twin = db.execute(select(Order).where(
        Order.marketplace == marketplace,
        Order.ordered_at >= day,
        Order.ordered_at < day + timedelta(days=1),
        Order.order_total == round(slip.item_total, 2),
        Order.external_order_id != slip.order_number,
    )).scalars().first()
    if twin is None:
        return None
    return (f"a {marketplace} order for ${slip.item_total:.2f} on the same day is "
            f"already recorded as {twin.external_order_id} — check this isn't the "
            f"same sale before committing")


def build_batch(db: Session, *, filename: str, content: bytes) -> SlipBatch:
    """Parse an uploaded packing-slip PDF into a review batch.

    Nothing live is written: no orders, no inventory movement. Orders already
    recorded (a re-uploaded PDF, or a batch committed earlier) come back marked
    ``duplicate`` rather than being silently re-created — the slip carries
    TCGplayer's order number, so re-uploading the same file is safe.
    """
    parsed = pdf_slips.parse_packing_slips(content)
    batch = SlipBatch(filename=filename or "packing-slips.pdf")
    db.add(batch)
    db.flush()
    for order in parsed:
        slip = SlipOrder(
            batch_id=batch.id,
            order_number=order["order_number"],
            buyer_name=order.get("buyer_name") or "",
            ordered_at=_parse_date(order.get("order_date")),
            ship_city=order.get("ship_city") or "",
            ship_state=order.get("ship_state") or "",
            ship_postal_code=order.get("ship_postal_code") or "",
            item_total=order.get("item_total") or 0.0,
            quantity_total=order.get("quantity_total") or 0,
            reconciled=pdf_slips.reconciles(order),
            page_count=order.get("page_count") or 1,
            lines=[match_line(db, l) for l in order.get("lines") or []],
        )
        existing = db.execute(select(Order).where(
            Order.marketplace == batch.marketplace,
            Order.external_order_id == slip.order_number)).scalars().first()
        if existing is not None:
            slip.status = "duplicate"
            slip.order_id = existing.id
            slip.error = "already recorded — skipped"
        else:
            slip.warning = _look_alike(db, batch.marketplace, slip)
        db.add(slip)
        db.flush()
        refresh(db, slip)
    db.commit()
    return batch


def resolve_line(db: Session, slip: SlipOrder, index: int, *,
                 inventory_id: int | None = None,
                 catalog_card_id: int | None = None) -> SlipOrder:
    """Point one line at a specific inventory record (or catalog card).

    Picking an inventory record marks the line matched — that's the reviewer
    overriding the automatic match, which is the whole point of the review step.
    Picking only a catalog card re-runs matching against it.
    """
    lines = list(slip.lines or [])
    if not 0 <= index < len(lines):
        raise ValueError(f"line {index} not on this order")
    line = dict(lines[index])
    if inventory_id is not None:
        item = db.get(InventoryItem, inventory_id)
        if item is None or item.deleted:
            raise ValueError("inventory record not found")
        line["inventory_id"] = item.id
        line["catalog_card_id"] = item.catalog_card_id
        line["match_status"] = MATCHED
        line["candidates"] = []
        line["match_note"] = ("resolved by hand"
                              if item.quantity >= (line.get("quantity") or 1)
                              else f"resolved by hand, only {item.quantity} in stock")
    elif catalog_card_id is not None:
        line["catalog_card_id"] = catalog_card_id
        card = db.get(CatalogCard, catalog_card_id)
        if card is None:
            raise ValueError("catalog card not found")
        # Re-run inventory matching now that the card is pinned.
        line["set_name"] = card.set_name or line.get("set_name")
        line["collector_number"] = card.collector_number
        line["card_name"] = card.name
        line = match_line(db, line)
    lines[index] = line
    slip.lines = lines  # reassign so SQLAlchemy sees the JSON change
    refresh(db, slip)
    db.commit()
    return slip


def skip_line(db: Session, slip: SlipOrder, index: int) -> SlipOrder:
    """Drop a line from the order so the rest can commit.

    The line's revenue stays in the order total (that's what the buyer paid and
    what the fee is charged on), it just won't consume inventory or carry COGS.
    Used for cards sold outside tracked stock — a promo tossed in, or a record
    that was never entered.
    """
    lines = list(slip.lines or [])
    if not 0 <= index < len(lines):
        raise ValueError(f"line {index} not on this order")
    line = dict(lines[index])
    line["match_status"] = MATCHED
    line["inventory_id"] = None
    line["candidates"] = []
    line["match_note"] = "skipped — no inventory link, no COGS"
    line["skipped"] = True
    lines[index] = line
    slip.lines = lines
    refresh(db, slip)
    db.commit()
    return slip


def _line_description(line: dict) -> str:
    if line.get("parse_ok"):
        bits = [line.get("card_name") or "", line.get("set_name") or ""]
        number = line.get("collector_number")
        tail = f"#{number}" if number else ""
        printing = line.get("printing_canonical") or line.get("printing")
        extra = "" if printing in (None, "normal") else f" {printing}"
        return " — ".join(b for b in bits if b) + \
            (f" {tail}" if tail else "") + \
            f" [{line.get('condition') or ''}{extra}]"
    return line.get("raw") or line.get("description") or ""


def commit_order(db: Session, slip: SlipOrder) -> Order:
    """Create the live ``Order`` for one reviewed slip.

    The order lands as ``open`` and inventory is *not* deducted — that happens at
    mark-shipped, which is also where the marketplace gets told and other
    channels get de-listed. Creating it open is what lets it flow into the
    existing pick-list workflow, and means a pre-ship cancellation doesn't need
    a stock reversal.
    """
    if slip.status == "committed":
        raise ValueError("already committed")
    if slip.status == "duplicate":
        raise ValueError("this order is already recorded")
    if slip.status != "ready":
        raise ValueError(slip.error or "order is not ready to commit")
    order = Order(
        marketplace=slip.batch.marketplace,
        external_order_id=slip.order_number,
        buyer_name=slip.buyer_name,
        ship_to={"city": slip.ship_city, "state": slip.ship_state,
                 "zip": slip.ship_postal_code, "name": slip.buyer_name},
        status="open",
        order_total=round(slip.item_total, 2),
        shipping_charged=round(slip.shipping_charged, 2),
        marketplace_fees=slip.estimated_fee,
        ordered_at=slip.ordered_at or datetime.now(timezone.utc),
    )
    db.add(order)
    db.flush()
    for line in slip.lines or []:
        db.add(OrderItem(
            order_id=order.id,
            inventory_id=line.get("inventory_id"),
            catalog_card_id=line.get("catalog_card_id"),
            description=_line_description(line),
            quantity=line.get("quantity") or 1,
            unit_price=line.get("unit_price") or 0.0,
        ))
    slip.status = "committed"
    slip.order_id = order.id
    slip.error = None
    db.commit()
    return order


def commit_batch(db: Session, batch: SlipBatch) -> dict:
    """Commit every ready order in a batch; leave blocked ones for review."""
    created, skipped = [], []
    for slip in batch.orders:
        if slip.status != "ready":
            if slip.status not in ("committed",):
                skipped.append({"order_number": slip.order_number,
                                "status": slip.status, "reason": slip.error})
            continue
        try:
            order = commit_order(db, slip)
            created.append({"order_number": slip.order_number,
                            "order_id": order.id})
        except ValueError as e:  # one bad order must not stop the batch
            db.rollback()
            slip.status = "blocked"
            slip.error = str(e)
            db.commit()
            skipped.append({"order_number": slip.order_number,
                            "status": "blocked", "reason": str(e)})
    remaining = [s for s in batch.orders if s.status not in ("committed", "duplicate")]
    batch.status = "partially_committed" if remaining else "committed"
    db.commit()
    return {"committed": created, "skipped": skipped, "batch_status": batch.status}
