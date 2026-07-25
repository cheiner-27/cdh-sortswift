"""Staging — the pre-commit review layer between intake and live inventory."""
from sqlalchemy.orm import Session

from ..models import InventoryItem, ScanQueueItem, StagingItem
from . import inventory as inv_svc


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
                         cause: str = "scan_intake") -> int:
    """Push staged rows to live inventory (partial approval supported)."""
    approved = 0
    for row in rows:
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
        bulk = db.get(InventoryItem, row.source_bulk_id) if row.source_bulk_id else None
        if bulk is not None:
            # Card came out of a bulk pile: decrement the pile and carry its
            # FIFO cost + age across, instead of recording a fresh purchase.
            inv_svc.pull_from_bulk(db, item, bulk, row.quantity,
                                   comment="pulled from bulk (staging approve)")
        else:
            inv_svc.add_stock(db, item, row.quantity, row.cost,
                              cause=cause, comment=f"staging approve (source={row.source})",
                              acquired_at=row.acquired_at)
        db.delete(row)
        approved += 1
    db.commit()
    return approved


def add_direct(db: Session, *, catalog_card_id=None, custom_sku_id=None,
               condition="NM", printing="normal", language="en", bin="",
               quantity=1, cost=None, price=None, comment="",
               acquired_at=None, cause="manual", source_bulk_id=None) -> int:
    """Skip-staging direct-to-live path for trusted intake."""
    item = inv_svc.find_or_create_item(
        db, catalog_card_id=catalog_card_id, custom_sku_id=custom_sku_id,
        condition=condition, printing=printing, language=language, bin=bin)
    if comment:
        item.comment = (item.comment + "\n" + comment).strip() if item.comment else comment
    if price is not None:
        item.current_price = price
    bulk = db.get(InventoryItem, source_bulk_id) if source_bulk_id else None
    if bulk is not None:
        inv_svc.pull_from_bulk(db, item, bulk, quantity,
                               comment=comment or "pulled from bulk (direct add)")
    else:
        inv_svc.add_stock(db, item, quantity, cost, cause=cause, comment="direct add",
                          acquired_at=acquired_at)
    db.commit()
    return item.id
