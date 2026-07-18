"""TCGplayer adapter (Section 6).

API access is pending — the adapter interface is complete so live sync can be
enabled by filling in credentials, but every network call raises a clean
"api_unavailable" ListingError until then. The CSV fallback (export price+qty
CSV for manual upload, deduction CSV for cross-channel sales) is the working
path and remains useful even after API access exists.
"""
import csv
import io

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...domain import CONDITION_LABELS
from ...models import InventoryItem, ListingRule, MarketplaceAccount, MarketplaceListing
from .base import ListingError, MarketplaceAdapter


class TcgplayerAdapter(MarketplaceAdapter):
    marketplace = "tcgplayer"

    def _account(self, db: Session) -> MarketplaceAccount | None:
        return db.execute(select(MarketplaceAccount).where(
            MarketplaceAccount.marketplace == "tcgplayer")).scalars().first()

    def _api_enabled(self, db: Session) -> bool:
        acct = self._account(db)
        return bool(acct and acct.status == "connected"
                    and acct.credentials.get("api_enabled"))

    def _require_api(self, db: Session):
        if not self._api_enabled(db):
            raise ListingError(
                "api_unavailable",
                "TCGplayer API access not enabled — use the CSV fallback "
                "(Marketplaces > TCGplayer > Export CSV)")

    def create_listing(self, db: Session, item: InventoryItem,
                       listing: MarketplaceListing, rule: ListingRule,
                       price: float, quantity: int) -> None:
        self._require_api(db)
        if self._account(db).credentials.get("dry_run"):
            listing.tcg_sku_id = f"dry-tcg-{item.id}"
            return
        raise ListingError("api_unavailable",
                           "TCGplayer live API integration pending key issuance")

    def update_listing(self, db: Session, item: InventoryItem,
                       listing: MarketplaceListing,
                       price: float, quantity: int) -> None:
        self._require_api(db)
        if self._account(db).credentials.get("dry_run"):
            return
        raise ListingError("api_unavailable",
                           "TCGplayer live API integration pending key issuance")

    def end_listing(self, db: Session, listing: MarketplaceListing) -> None:
        if self._account(db) and self._account(db).credentials.get("dry_run"):
            listing.tcg_sku_id = None
            return
        self._require_api(db)

    def fetch_orders(self, db: Session) -> list[dict]:
        # No live API: orders arrive via the Deduction CSV import (Section 3).
        return []

    def mark_shipped(self, db: Session, external_order_id: str,
                     tracking_number: str, carrier: str) -> None:
        self._require_api(db)


# --- CSV fallback ----------------------------------------------------------

def export_listing_csv(db: Session, items: list[InventoryItem]) -> bytes:
    """Price + quantity in TCGplayer's accepted staged-inventory CSV layout."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["TCGplayer Id", "Product Line", "Set Name", "Product Name",
                "Number", "Condition", "Printing", "Language",
                "Total Quantity", "TCG Marketplace Price"])
    for it in items:
        if not it.card or not it.card.tcgplayer_product_id:
            continue
        listing = next((l for l in it.listings if l.marketplace == "tcgplayer"), None)
        price = (listing.listed_price if listing and listing.listed_price
                 else it.price_override or it.current_price)
        from ..inventory import effective_quantity
        qty = effective_quantity(it, "tcgplayer")
        w.writerow([
            it.card.tcgplayer_product_id, it.card.game, it.card.set_name,
            it.card.name, it.card.collector_number,
            CONDITION_LABELS.get(it.condition, it.condition),
            "Foil" if it.printing != "normal" else "Normal",
            it.language.upper(), qty, f"{price:.2f}" if price else "",
        ])
    return buf.getvalue().encode("utf-8-sig")


def export_deduction_csv(db: Session, rows: list[dict]) -> bytes:
    """Deduction CSV for cross-channel sales (cards sold on eBay) to reflect
    on TCGplayer manually. Never include TCGplayer-originated sales — those
    already decremented on TCGplayer's side."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["TCGplayer Id", "Product Name", "Set Name", "Condition",
                "Quantity To Deduct"])
    for r in rows:
        w.writerow([r.get("tcgplayer_product_id", ""), r.get("name", ""),
                    r.get("set_name", ""), r.get("condition", ""),
                    r.get("quantity", 0)])
    return buf.getvalue().encode("utf-8-sig")
