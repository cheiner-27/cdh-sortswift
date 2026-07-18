"""Marketplace adapter interface + listing-rule matching (Section 6).

Adapters are stateless wrappers over each marketplace API. All local state
(listing IDs, errors, dirty flags) lives on MarketplaceListing rows.
"""
from abc import ABC, abstractmethod

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import InventoryItem, ListingRule, MarketplaceListing


class ListingError(Exception):
    """Marketplace push failure with a specific reason code (Section 6.4)."""
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class MarketplaceAdapter(ABC):
    marketplace: str

    @abstractmethod
    def create_listing(self, db: Session, item: InventoryItem,
                       listing: MarketplaceListing, rule: ListingRule,
                       price: float, quantity: int) -> None:
        """Create a new listing; persist external IDs onto `listing`."""

    @abstractmethod
    def update_listing(self, db: Session, item: InventoryItem,
                       listing: MarketplaceListing,
                       price: float, quantity: int) -> None:
        """Push price/quantity to an existing listing."""

    @abstractmethod
    def end_listing(self, db: Session, listing: MarketplaceListing) -> None:
        """End/delist (quantity hit zero or rebuild)."""

    @abstractmethod
    def fetch_orders(self, db: Session) -> list[dict]:
        """Pull open orders. Returns normalized dicts:
        {external_order_id, buyer_name, ship_to, total, fees, is_direct,
         ordered_at, items: [{sku, marketplace_product_id, description,
                              quantity, unit_price}]}"""

    @abstractmethod
    def mark_shipped(self, db: Session, external_order_id: str,
                     tracking_number: str, carrier: str) -> None:
        """Mark an order shipped with tracking."""


def rules_for(db: Session, marketplace: str) -> list[ListingRule]:
    return db.execute(
        select(ListingRule)
        .where(ListingRule.marketplace == marketplace, ListingRule.active == True)  # noqa: E712
        .order_by(ListingRule.priority.asc(), ListingRule.id.asc())
    ).scalars().all()


def item_product_type(item: InventoryItem) -> str:
    if item.custom_sku and item.custom_sku.product:
        return item.custom_sku.product.item_type  # graded_card | sealed | accessory | other
    return "single"


def rule_matches(item: InventoryItem, rule: ListingRule, price: float | None) -> bool:
    f = rule.filters or {}
    ptype = item_product_type(item)
    if rule.block_sealed and ptype == "sealed":
        return False
    if rule.block_singles and ptype == "single":
        return False
    if rule.condition_allowlist and item.condition not in rule.condition_allowlist:
        return False
    game = item.card.game if item.card else None
    if f.get("games") and game not in f["games"]:
        return False
    if f.get("sets"):
        if not item.card or item.card.set_code not in f["sets"]:
            return False
    if f.get("conditions") and item.condition not in f["conditions"]:
        return False
    if f.get("product_types") and ptype not in f["product_types"]:
        return False
    if f.get("price_min") is not None and (price is None or price < f["price_min"]):
        return False
    if f.get("price_max") is not None and (price is None or price > f["price_max"]):
        return False
    return True


def find_rule(db: Session, marketplace: str, item: InventoryItem,
              price: float | None) -> ListingRule | None:
    """First-match-wins by priority. None = listing error, not silent skip."""
    for rule in rules_for(db, marketplace):
        if rule_matches(item, rule, price):
            return rule
    return None
