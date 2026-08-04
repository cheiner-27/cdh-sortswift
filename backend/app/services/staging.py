"""Staging — the pre-commit review layer between intake and live inventory."""
from sqlalchemy.orm import Session

from ..models import InventoryItem, ScanQueueItem, StagingItem
from . import inventory as inv_svc


def resolve_bulk_pile(db: Session, source_bulk_id: int | None) -> InventoryItem | None:
    """The bulk pile a row is pulled from — None means fresh stock.

    ``source_bulk_id`` is an **inventory** id (the pile's InventoryItem), not the
    pile's product id. Those two id spaces overlap, so passing the wrong one used
    to resolve to whatever unrelated card happened to hold that inventory id and
    pull from *that* — moving 0 units when the row was empty, which read as the
    approve doing nothing at all. Anything that isn't a live, stocked bulk pile
    is refused here instead of being pulled from silently.
    """
    if not source_bulk_id:
        return None
    item = db.get(InventoryItem, source_bulk_id)
    if item is None:
        raise ValueError(f"source bulk pile #{source_bulk_id} does not exist")
    product = item.custom_sku.product if item.custom_sku else None
    if product is None or product.item_type != "bulk":
        raise ValueError(
            f"inventory #{source_bulk_id} is not a bulk pile — it is "
            f"{inv_svc.item_description(item)}")
    if item.deleted:
        raise ValueError(f"bulk pile '{product.name}' has been deleted")
    if item.quantity <= 0:
        raise ValueError(f"bulk pile '{product.name}' has no cards on hand")
    return item


def stage_scan_item(db: Session, scan: ScanQueueItem) -> StagingItem:
    """Confirm a scan-queue item into staging."""
    row = StagingItem(
        source="scan",
        catalog_card_id=scan.card_id,
        condition=scan.condition,
        printing=scan.printing,
        language=scan.language,
        bin=scan.bin,
        quantity=scan.quantity,
        cost=scan.cost,
        scan_image_path=scan.image_path,
        back_image_path=scan.back_image_path,
        source_bulk_id=scan.source_bulk_id,
    )
    db.add(row)
    scan.status = "confirmed"
    db.flush()
    return row


def approve_staging_rows(db: Session, rows: list[StagingItem],
                         cause: str = "scan_intake") -> dict:
    """Push staged rows to live inventory (partial approval supported).

    A row whose bulk pile can't be pulled from is **left in staging** and
    reported in ``skipped`` rather than deleted — approving used to consume the
    row either way, so an unusable pile discarded the card and still reported
    success. ``partial`` reports rows the pile could only cover part of.
    """
    approved, skipped, partial = 0, [], []
    for row in rows:
        # Resolve the pile first: a row we can't approve must not leave a
        # quantity-0 inventory record behind as a side effect.
        try:
            bulk = resolve_bulk_pile(db, row.source_bulk_id)
        except ValueError as e:
            skipped.append({"id": row.id, "reason": str(e)})
            continue
        item = inv_svc.find_or_create_item(
            db,
            catalog_card_id=row.catalog_card_id,
            custom_sku_id=row.custom_sku_id,
            condition=row.condition,
            printing=row.printing,
            language=row.language,
            bin=row.bin,
        )
        if row.scan_image_path and not item.scan_image_path:
            item.scan_image_path = row.scan_image_path
            item.back_image_path = row.back_image_path
        if row.comment:
            item.comment = (item.comment + "\n" + row.comment).strip() if item.comment else row.comment
        if row.price is not None:
            item.current_price = row.price
        if bulk is not None:
            # Card came out of a bulk pile: decrement the pile and carry its
            # FIFO cost + age across, instead of recording a fresh purchase.
            moved = inv_svc.pull_from_bulk(
                db, item, bulk, row.quantity,
                comment="pulled from bulk (staging approve)")
            if moved < row.quantity:
                partial.append({"id": row.id, "wanted": row.quantity,
                                "moved": moved})
        else:
            inv_svc.add_stock(db, item, row.quantity, row.cost,
                              cause=cause, comment=f"staging approve (source={row.source})",
                              acquired_at=row.acquired_at)
        db.delete(row)
        approved += 1
    db.commit()
    return {"approved": approved, "skipped": skipped, "partial": partial}


def add_direct(db: Session, *, catalog_card_id=None, custom_sku_id=None,
               condition="NM", printing="normal", language="en", bin="",
               quantity=1, cost=None, price=None, comment="",
               acquired_at=None, cause="manual", source_bulk_id=None) -> int:
    """Skip-staging direct-to-live path for trusted intake.

    Raises ValueError when a source bulk pile is named but unusable, so the
    caller reports it instead of quietly booking a fresh purchase.
    """
    bulk = resolve_bulk_pile(db, source_bulk_id)
    item = inv_svc.find_or_create_item(
        db, catalog_card_id=catalog_card_id, custom_sku_id=custom_sku_id,
        condition=condition, printing=printing, language=language, bin=bin)
    if comment:
        item.comment = (item.comment + "\n" + comment).strip() if item.comment else comment
    if price is not None:
        item.current_price = price
    if bulk is not None:
        inv_svc.pull_from_bulk(db, item, bulk, quantity,
                               comment=comment or "pulled from bulk (direct add)")
    else:
        inv_svc.add_stock(db, item, quantity, cost, cause=cause, comment="direct add",
                          acquired_at=acquired_at)
    db.commit()
    return item.id
