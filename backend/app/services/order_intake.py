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

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import normalize_condition, normalize_printing
from ..models import (CatalogCard, InventoryItem, Order, OrderItem, SlipBatch,
                      SlipOrder, collector_number_key, name_key)
from . import orders as order_svc
from . import pdf_slips
from .inventory import item_description
from .settings import get_setting

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
OUT_OF_STOCK = "out_of_stock"

# How much of the card name has to agree. TCGplayer appends treatment suffixes
# the catalog doesn't carry ("Cauldron of Essence (Extended Art)" vs "Cauldron of
# Essence"), so the comparison is a shared prefix rather than an equality test.
NAME_PREFIX = 10


def _game_code(label: str | None) -> str | None:
    return GAME_LABELS.get((label or "").strip().lower())


def _candidate(item: InventoryItem) -> dict:
    return {"inventory_id": item.id, "catalog_card_id": item.catalog_card_id,
            "label": item_description(item), "bin": item.bin,
            "quantity": item.quantity}


def _name_agrees(line_key: str, card_key: str | None) -> bool:
    """Do two name keys refer to the same card? Either side may carry extra
    text, so a shared prefix counts on whichever is shorter."""
    card_key = card_key or ""
    if not line_key or not card_key:
        return False
    n = min(len(line_key), len(card_key), NAME_PREFIX)
    return line_key[:n] == card_key[:n]


def find_stock(db: Session, line: dict, *, any_variant: bool = False) -> list[InventoryItem]:
    """Inventory records that could be the card on this slip line.

    Matching runs against *inventory*, not the catalog. That is the point: a
    packing slip is a list of cards you are about to pull off a shelf, so a hit
    against something you don't own tells you nothing, and a miss is exactly the
    signal worth having — it means the stock was never entered, or was entered
    wrong.

    The key is the collector number plus a name prefix. Set name is deliberately
    *not* used: TCGplayer's set naming diverges from the catalog's often enough
    to be useless as a key ("Commander: Innistrad: Crimson Vow" against "Crimson
    Vow Commander", "The List Reprints" against the original set). Scoping to
    inventory is what makes the looser key safe — a collector number collides
    constantly across 139k catalog rows and essentially never inside one
    collection.

    Pre-numbering-era slip lines (Beta, Antiquities, Legends, ...) carry no
    collector number at all, so there's nothing to key on but the name prefix
    itself — still scoped to inventory for the same reason a number is safe
    there.

    ``any_variant`` drops the condition and printing filters, to answer "do I
    have this card at all, just graded differently?" when the exact one is
    missing.
    """
    number = collector_number_key(line.get("collector_number"))
    game = _game_code(line.get("game_label"))
    q = (select(InventoryItem)
         .join(CatalogCard, CatalogCard.id == InventoryItem.catalog_card_id)
         .where(InventoryItem.deleted == False))  # noqa: E712
    if number:
        q = q.where(CatalogCard.collector_number_norm == number)
    else:
        key = name_key(line.get("card_name"))
        if not key:
            return []
        q = q.where(CatalogCard.name_norm.like(f"{key[:NAME_PREFIX]}%"))
    if not any_variant:
        q = q.where(InventoryItem.condition == line["condition"],
                    InventoryItem.printing == line["printing_canonical"])
    if game:
        q = q.where(CatalogCard.game == game)
    rows = db.execute(q).scalars().all()
    if len(rows) <= 1:
        return rows
    key = name_key(line.get("card_name")) or ""
    narrowed = [r for r in rows
                if _name_agrees(key, r.card.name_norm if r.card else None)]
    return narrowed or rows


def match_line(db: Session, line: dict) -> dict:
    """Attach inventory identity and a match status to one slip line.

    Four outcomes, each meaning something different to the person picking:

    - ``matched`` — one in-stock record fits; nothing to do.
    - ``ambiguous`` — several in-stock records fit, normally the same card in
      more than one bin. Nothing is guessed, because a wrong guess sends you to
      the wrong shelf mid-pick.
    - ``out_of_stock`` — the right record exists but holds zero. Worth its own
      state: it means you sold something your inventory says you don't have, so
      the record, not the slip, is what needs fixing.
    - ``unmatched`` — nothing in inventory looks like this at all.
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

    rows = find_stock(db, line)
    in_stock = [r for r in rows if r.quantity > 0]
    if len(in_stock) == 1:
        item = in_stock[0]
        line["inventory_id"] = item.id
        line["catalog_card_id"] = item.catalog_card_id
        line["match_status"] = MATCHED
        if item.quantity < (line.get("quantity") or 1):
            line["match_note"] = (f"only {item.quantity} in stock, "
                                  f"{line.get('quantity')} sold")
        return line
    if in_stock:
        line["match_status"] = AMBIGUOUS
        line["candidates"] = [_candidate(r) for r in in_stock]
        line["match_note"] = "in stock in more than one bin — pick which"
        return line
    if rows:
        line["catalog_card_id"] = rows[0].catalog_card_id
        line["match_status"] = OUT_OF_STOCK
        line["candidates"] = [_candidate(r) for r in rows]
        line["match_note"] = (f"found this {condition}/{printing} record but it "
                              f"holds 0 — stock may not have been entered")
        return line

    # Nothing in that exact grade/finish. Having the same card in another one is
    # worth surfacing rather than reporting a flat miss: it usually means the
    # card was graded or flagged wrong on the way in, which is the mistake this
    # screen exists to catch.
    variants = [r for r in find_stock(db, line, any_variant=True) if r.quantity > 0]
    line["match_status"] = UNMATCHED
    if variants:
        line["catalog_card_id"] = variants[0].catalog_card_id
        line["candidates"] = [_candidate(r) for r in variants]
        have = sorted({f"{r.condition}/{r.printing}" for r in variants})
        line["match_note"] = (f"no {condition}/{printing} in stock, but you have "
                              f"this card as {', '.join(have)}")
    elif line.get("collector_number"):
        line["match_note"] = (
            f"nothing in inventory matches #{line.get('collector_number')}")
    else:
        line["match_note"] = (
            f"nothing in inventory matches \"{line.get('card_name')}\" "
            f"({line.get('set_name') or 'unknown set'}) — no collector number "
            f"printed on the slip for this one")
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


def default_shipping_charged(db: Session, subtotal: float) -> float:
    """What the buyer paid for shipping, for a slip that doesn't print it.

    Small orders ship at the flat rate the marketplace charges the buyer; above
    the threshold shipping is free and the buyer paid nothing. It's revenue and
    part of the commission base, so it's pre-filled rather than left at zero —
    and it stays editable, since this is a default, not a lookup.
    """
    if subtotal < float(get_setting(db, "slip_free_shipping_over")):
        return float(get_setting(db, "slip_shipping_charged"))
    return 0.0


def refresh(db: Session, slip: SlipOrder) -> SlipOrder:
    """Recompute a slip order's fee and ready/blocked status from its lines.

    A fee the reviewer typed is left alone — the estimate exists to save typing,
    not to overrule someone reading the actual figure off a payout.
    """
    fee = order_svc.estimate_marketplace_fee(
        db, subtotal=slip.item_total, shipping_charged=slip.shipping_charged,
        tax=slip.tax, state=slip.ship_state)
    slip.fee_detail = fee
    if not slip.fee_overridden:
        slip.estimated_fee = fee["fee"]
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
        subtotal = order.get("item_total") or 0.0
        slip = SlipOrder(
            batch_id=batch.id,
            order_number=order["order_number"],
            # Held for the review screen only, so the name on the physical slip
            # can be eyeballed while picking. It is not carried onto the order.
            buyer_name=order.get("buyer_name") or "",
            ordered_at=_parse_date(order.get("order_date")),
            ship_city=order.get("ship_city") or "",
            ship_state=order.get("ship_state") or "",
            ship_postal_code=order.get("ship_postal_code") or "",
            item_total=subtotal,
            quantity_total=order.get("quantity_total") or 0,
            reconciled=pdf_slips.reconciles(order),
            page_count=order.get("page_count") or 1,
            shipping_charged=default_shipping_charged(db, subtotal),
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
                 inventory_id: int) -> SlipOrder:
    """Point one line at a specific inventory record.

    Only inventory can be picked — a catalog card you don't stock isn't an
    answer to "which of my cards is this", which is the question the review
    screen is asking.
    """
    lines = list(slip.lines or [])
    if not 0 <= index < len(lines):
        raise ValueError(f"line {index} not on this order")
    line = dict(lines[index])
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


def _line_description(db: Session, line: dict) -> str:
    """Label a committed line the same way every other sale path does.

    A matched line describes the *inventory record*, via the shared
    ``item_description``, so an intake sale and a manual sale of the same card
    read identically. It also means the catalog's spelling wins over the slip's
    — TCGplayer drops diacritics ("Anduril" for "Andúril") and appends treatment
    suffixes the catalog doesn't use, and the record is the better authority on
    what the card is called.
    """
    item = (db.get(InventoryItem, line["inventory_id"])
            if line.get("inventory_id") else None)
    if item is not None:
        return item_description(item)
    if line.get("parse_ok"):
        printing = line.get("printing_canonical") or line.get("printing")
        extra = "" if printing in (None, "normal") else f" {printing}"
        number = f"[#{line['collector_number']}] " if line.get("collector_number") else ""
        return (f"{line.get('card_name') or ''} "
                f"{number}"
                f"{line.get('condition') or ''}{extra}").strip()
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
        # The buyer's name is deliberately not carried over. Destination
        # city/state/ZIP are, because the fee estimate and any label need them;
        # who the buyer was isn't ours to keep once the order exists.
        buyer_name="",
        ship_to={"city": slip.ship_city, "state": slip.ship_state,
                 "zip": slip.ship_postal_code},
        status="open",
        order_total=round(slip.item_total, 2),
        shipping_charged=round(slip.shipping_charged, 2),
        shipping_cost=round(slip.shipping_cost, 2),
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
            description=_line_description(db, line),
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
