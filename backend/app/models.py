"""SQLAlchemy ORM models for cdh-sortswift.

Design constraints honored here (see REQUIREMENTS.md):
- FIFO costing from day one: AcquisitionLog is the authoritative cost/age source.
- Every mutation logged: InventoryLog records all inventory deltas.
- Master quantity authoritative: MarketplaceListing quantities are derived.
- Staging as a soft landing zone: StagingItem is distinct from InventoryItem.
- No file mutation: ProcessedScan tracks SHA-256 hashes; files never move.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def collector_number_key(value: str | None) -> str | None:
    """Canonical matching key for a collector number.

    Recognition compares an OCR-read number against the catalog, but the
    printed/stored forms vary by era and source: Pokémon prints a fraction
    ("4/102", zero-padded "029/086"), MTG prints a bare number sometimes
    zero-padded ("0123"), and TCGcsv keeps the leading zeros while our OCR
    strips them. Reduce everything to the leading numerator with leading
    zeros removed so those forms all collapse to the same key ("4/102",
    "004/102" and "4" all -> "4"). Non-numeric codes (Yu-Gi-Oh! print codes,
    One Piece "OP01-004") are upper-cased and passed through unchanged.
    """
    if not value:
        return None
    head = value.split("/")[0].strip()
    return str(int(head)) if head.isdigit() else head.upper()


# ---------------------------------------------------------------------------
# Settings (key/value)
# ---------------------------------------------------------------------------

class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)


# ---------------------------------------------------------------------------
# Catalog (per-game reference data, fetched from APIs, stored locally)
# ---------------------------------------------------------------------------

class CatalogSet(Base):
    __tablename__ = "catalog_sets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game: Mapped[str] = mapped_column(String, index=True)  # mtg | pokemon | onepiece | yugioh
    code: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String)
    release_date: Mapped[str | None] = mapped_column(String, nullable=True)
    __table_args__ = (UniqueConstraint("game", "code"),)


class CatalogCard(Base):
    __tablename__ = "catalog_cards"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game: Mapped[str] = mapped_column(String, index=True)
    external_id: Mapped[str] = mapped_column(String, index=True)  # scryfall id / pokemontcg id / etc.
    tcgplayer_product_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    set_code: Mapped[str] = mapped_column(String, index=True)
    set_name: Mapped[str | None] = mapped_column(String, nullable=True)
    collector_number: Mapped[str] = mapped_column(String, index=True)  # as printed, e.g. "029/086"
    # numerator-only matching key (see collector_number_key); powers OCR lookup
    collector_number_norm: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    name: Mapped[str] = mapped_column(String, index=True)
    rarity: Mapped[str | None] = mapped_column(String, nullable=True)
    finishes: Mapped[list] = mapped_column(JSON, default=list)  # canonical printing types available
    languages: Mapped[list] = mapped_column(JSON, default=list)
    image_url: Mapped[str | None] = mapped_column(String, nullable=True)
    back_image_url: Mapped[str | None] = mapped_column(String, nullable=True)  # double-faced cards
    is_double_faced: Mapped[bool] = mapped_column(Boolean, default=False)
    is_sealed: Mapped[bool] = mapped_column(Boolean, default=False)  # excluded from recognition
    phash: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    release_date: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (
        UniqueConstraint("game", "external_id"),
        Index("ix_cards_set_num", "game", "set_code", "collector_number"),
        Index("ix_cards_game_numnorm", "game", "collector_number_norm"),
    )


class PriceData(Base):
    """Market prices pulled from TCGcsv (TCGplayer price feed)."""
    __tablename__ = "price_data"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tcgplayer_product_id: Mapped[int] = mapped_column(Integer, index=True)
    sub_type: Mapped[str] = mapped_column(String, default="Normal")  # Normal | Foil | ...
    market: Mapped[float | None] = mapped_column(Float, nullable=True)
    mid: Mapped[float | None] = mapped_column(Float, nullable=True)
    low: Mapped[float | None] = mapped_column(Float, nullable=True)
    direct_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("tcgplayer_product_id", "sub_type"),)


# ---------------------------------------------------------------------------
# Custom / non-catalog items (graded, sealed, accessories) — Section 4
# ---------------------------------------------------------------------------

class CustomProduct(Base):
    __tablename__ = "custom_products"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[str] = mapped_column(String, index=True)
    group: Mapped[str] = mapped_column(String, default="")
    name: Mapped[str] = mapped_column(String, index=True)
    item_type: Mapped[str] = mapped_column(String)  # graded_card | sealed | accessory | other
    description: Mapped[str] = mapped_column(Text, default="")
    images: Mapped[list] = mapped_column(JSON, default=list)  # first = primary
    upc: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    # sealed breakdown: [{"name": "...", "count": 36, "component_product_id": null}]
    breakdown_components: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    skus: Mapped[list["CustomSku"]] = relationship(back_populates="product", cascade="all, delete-orphan")


class CustomSku(Base):
    __tablename__ = "custom_skus"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("custom_products.id"), index=True)
    condition: Mapped[str | None] = mapped_column(String, nullable=True)
    language: Mapped[str | None] = mapped_column(String, nullable=True)
    printing: Mapped[str | None] = mapped_column(String, nullable=True)
    grading_company: Mapped[str | None] = mapped_column(String, nullable=True)  # PSA/BGS/CGC/SGC/Raw
    grade_value: Mapped[str | None] = mapped_column(String, nullable=True)
    cert_number: Mapped[str | None] = mapped_column(String, nullable=True)
    product: Mapped[CustomProduct] = relationship(back_populates="skus")


# ---------------------------------------------------------------------------
# Scanning — Section 1
# ---------------------------------------------------------------------------

class ScanPull(Base):
    __tablename__ = "scan_pulls"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    folder: Mapped[str] = mapped_column(String)
    pulled_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    image_count: Mapped[int] = mapped_column(Integer, default=0)
    use_subfolder_bins: Mapped[bool] = mapped_column(Boolean, default=False)
    pair_front_back: Mapped[bool] = mapped_column(Boolean, default=False)
    items: Mapped[list["ScanQueueItem"]] = relationship(back_populates="pull")


class ProcessedScan(Base):
    """SHA-256 dedup registry. Files are never moved or deleted."""
    __tablename__ = "processed_scans"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sha256: Mapped[str] = mapped_column(String, unique=True, index=True)
    file_path: Mapped[str] = mapped_column(String)
    pull_id: Mapped[int | None] = mapped_column(ForeignKey("scan_pulls.id"), nullable=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ScanQueueItem(Base):
    """Recognized-but-unconfirmed scan results — persisted, not UI-only."""
    __tablename__ = "scan_queue"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pull_id: Mapped[int] = mapped_column(ForeignKey("scan_pulls.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)  # scan order within pull
    image_path: Mapped[str] = mapped_column(String)
    back_image_path: Mapped[str | None] = mapped_column(String, nullable=True)
    file_name: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    # pending | needs_review | confirmed | rejected
    low_resolution: Mapped[bool] = mapped_column(Boolean, default=False)
    method: Mapped[str | None] = mapped_column(String, nullable=True)  # ocr | phash | manual
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    candidates: Mapped[list] = mapped_column(JSON, default=list)  # top-N: [{card_id, score, ...}]
    card_id: Mapped[int | None] = mapped_column(ForeignKey("catalog_cards.id"), nullable=True)
    condition: Mapped[str] = mapped_column(String, default="NM")
    printing: Mapped[str] = mapped_column(String, default="normal")
    language: Mapped[str] = mapped_column(String, default="en")
    bin: Mapped[str] = mapped_column(String, default="")
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    pull: Mapped[ScanPull] = relationship(back_populates="items")
    card: Mapped[CatalogCard | None] = relationship()


# ---------------------------------------------------------------------------
# Staging — Section 2
# ---------------------------------------------------------------------------

class StagingItem(Base):
    __tablename__ = "staging"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String, default="scan")  # scan | csv | manual
    catalog_card_id: Mapped[int | None] = mapped_column(ForeignKey("catalog_cards.id"), nullable=True)
    custom_sku_id: Mapped[int | None] = mapped_column(ForeignKey("custom_skus.id"), nullable=True)
    condition: Mapped[str] = mapped_column(String, default="NM")
    printing: Mapped[str] = mapped_column(String, default="normal")
    language: Mapped[str] = mapped_column(String, default="en")
    bin: Mapped[str] = mapped_column(String, default="")
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Original acquisition date, preserved through staging so FIFO age carries
    # over on migration/import (defaults to now when approved if unset).
    acquired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    scan_image_path: Mapped[str | None] = mapped_column(String, nullable=True)
    back_image_path: Mapped[str | None] = mapped_column(String, nullable=True)
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    card: Mapped[CatalogCard | None] = relationship()
    custom_sku: Mapped[CustomSku | None] = relationship()


# ---------------------------------------------------------------------------
# Inventory — Section 3
# ---------------------------------------------------------------------------

class InventoryItem(Base):
    """One record per (card identity + condition + printing + language + bin)."""
    __tablename__ = "inventory"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    catalog_card_id: Mapped[int | None] = mapped_column(ForeignKey("catalog_cards.id"), index=True, nullable=True)
    custom_sku_id: Mapped[int | None] = mapped_column(ForeignKey("custom_skus.id"), index=True, nullable=True)
    condition: Mapped[str] = mapped_column(String, default="NM", index=True)
    printing: Mapped[str] = mapped_column(String, default="normal", index=True)
    language: Mapped[str] = mapped_column(String, default="en")
    bin: Mapped[str] = mapped_column(String, default="", index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    comment: Mapped[str] = mapped_column(Text, default="")
    price_override: Mapped[float | None] = mapped_column(Float, nullable=True)  # bypass autopricing
    price_floor: Mapped[float | None] = mapped_column(Float, nullable=True)     # per-item floor
    current_price: Mapped[float | None] = mapped_column(Float, nullable=True)   # last computed/applied
    scan_image_path: Mapped[str | None] = mapped_column(String, nullable=True)
    back_image_path: Mapped[str | None] = mapped_column(String, nullable=True)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    card: Mapped[CatalogCard | None] = relationship()
    custom_sku: Mapped[CustomSku | None] = relationship()
    listings: Mapped[list["MarketplaceListing"]] = relationship(
        back_populates="item", cascade="all, delete-orphan")


class MarketplaceListing(Base):
    """Per-marketplace listing state for an inventory record.

    Quantities here are DERIVED from InventoryItem.quantity minus
    reserves/caps — the master quantity is authoritative.
    """
    __tablename__ = "marketplace_listings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inventory_id: Mapped[int] = mapped_column(ForeignKey("inventory.id"), index=True)
    marketplace: Mapped[str] = mapped_column(String, index=True)  # ebay | tcgplayer
    status: Mapped[str] = mapped_column(String, default="unlisted", index=True)
    # unlisted | listed | sold | error
    listed_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    listed_quantity: Mapped[int] = mapped_column(Integer, default=0)
    listing_cap: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0 = excluded
    reserve_quantity: Mapped[int] = mapped_column(Integer, default=0)  # held back FOR this marketplace
    # External IDs (persisted fields, not just a status enum):
    ebay_sku: Mapped[str | None] = mapped_column(String, nullable=True)
    ebay_offer_id: Mapped[str | None] = mapped_column(String, nullable=True)
    ebay_listing_id: Mapped[str | None] = mapped_column(String, nullable=True)
    tcg_sku_id: Mapped[str | None] = mapped_column(String, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    dirty: Mapped[bool] = mapped_column(Boolean, default=True)  # changed since last sync
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    item: Mapped[InventoryItem] = relationship(back_populates="listings")
    __table_args__ = (UniqueConstraint("inventory_id", "marketplace"),)


class AcquisitionLog(Base):
    """FIFO cost batches — authoritative source for cost basis and age."""
    __tablename__ = "acquisition_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    catalog_card_id: Mapped[int | None] = mapped_column(ForeignKey("catalog_cards.id"), index=True, nullable=True)
    custom_sku_id: Mapped[int | None] = mapped_column(ForeignKey("custom_skus.id"), index=True, nullable=True)
    condition: Mapped[str] = mapped_column(String)
    printing: Mapped[str] = mapped_column(String, default="normal")
    language: Mapped[str] = mapped_column(String, default="en")
    quantity: Mapped[int] = mapped_column(Integer)            # acquired in this batch
    quantity_remaining: Mapped[int] = mapped_column(Integer)  # unexhausted units
    unit_cost: Mapped[float] = mapped_column(Float, default=0.0)
    acquired_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class FifoConsumption(Base):
    """Links a sale deduction to the acquisition batches it consumed (enables reversal)."""
    __tablename__ = "fifo_consumption"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    acquisition_id: Mapped[int] = mapped_column(ForeignKey("acquisition_log.id"), index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), index=True, nullable=True)
    quantity: Mapped[int] = mapped_column(Integer)
    unit_cost: Mapped[float] = mapped_column(Float)
    consumed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class InventoryLog(Base):
    """Audit / Adjustment Log — every inventory mutation."""
    __tablename__ = "inventory_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inventory_id: Mapped[int | None] = mapped_column(ForeignKey("inventory.id"), index=True, nullable=True)
    item_description: Mapped[str] = mapped_column(String, default="")
    type: Mapped[str] = mapped_column(String, index=True)  # addition | deduction | adjustment | transfer
    quantity_delta: Mapped[int] = mapped_column(Integer, default=0)
    price_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    bin_before: Mapped[str | None] = mapped_column(String, nullable=True)
    bin_after: Mapped[str | None] = mapped_column(String, nullable=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    cause: Mapped[str] = mapped_column(String, default="manual", index=True)
    # manual | bulk_update | undo | sale | csv_import | transfer | scan_intake | breakdown | cycle_count | refund | cancellation
    source: Mapped[str] = mapped_column(String, default="staff")  # staff | automated | platform
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class ImportBatch(Base):
    __tablename__ = "import_batches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String, default="")
    mode: Mapped[str] = mapped_column(String, default="add")  # add | overwrite | deduction
    status: Mapped[str] = mapped_column(String, default="in_progress")
    # in_progress | completed | error | partially_complete | undone
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    quantity_total: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    rows: Mapped[list["ImportRow"]] = relationship(back_populates="batch", cascade="all, delete-orphan")


class ImportRow(Base):
    __tablename__ = "import_rows"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), index=True)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    mapped: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String, default="pending")
    # pending | imported | staged | error | ambiguous | undone | skipped
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    candidates: Mapped[list] = mapped_column(JSON, default=list)  # for ambiguous rows
    inventory_id: Mapped[int | None] = mapped_column(ForeignKey("inventory.id"), nullable=True)
    quantity_applied: Mapped[int] = mapped_column(Integer, default=0)
    batch: Mapped[ImportBatch] = relationship(back_populates="rows")


class ExportTemplate(Base):
    __tablename__ = "export_templates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    columns: Mapped[list] = mapped_column(JSON, default=list)  # ordered column keys
    layout: Mapped[str] = mapped_column(String, default="native")  # native | tcgplayer | ebay
    options: Mapped[dict] = mapped_column(JSON, default=dict)


class Expense(Base):
    """General business expense (supplies, postage, software, equipment) — a
    ledger modeled on the Card Tracker Airtable 'Expenses' table. Not tied to a
    specific card purchase/sale; feeds net-profit reporting. Tax defaults to a
    configurable rate of the subtotal unless a tax_override is given."""
    __tablename__ = "expenses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[str | None] = mapped_column(String, index=True, nullable=True)  # ISO YYYY-MM-DD
    name: Mapped[str] = mapped_column(String, default="")
    category: Mapped[str] = mapped_column(String, default="", index=True)
    # opex (operating overhead, expensed now) | capex (durable asset). Both hit
    # net profit in-period (de minimis safe harbor) but are reported separately.
    expense_class: Mapped[str] = mapped_column(String, default="opex", index=True)
    retailer: Mapped[str] = mapped_column(String, default="")
    payment_method: Mapped[str] = mapped_column(String, default="")
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    subtotal: Mapped[float] = mapped_column(Float, default=0.0)     # pre-tax total paid
    tax_override: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CycleCount(Base):
    __tablename__ = "cycle_counts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bin: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, default="in_progress")  # in_progress | completed | abandoned
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lines: Mapped[list["CycleCountLine"]] = relationship(back_populates="count", cascade="all, delete-orphan")


class CycleCountLine(Base):
    __tablename__ = "cycle_count_lines"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    count_id: Mapped[int] = mapped_column(ForeignKey("cycle_counts.id"), index=True)
    inventory_id: Mapped[int] = mapped_column(ForeignKey("inventory.id"))
    expected: Mapped[int] = mapped_column(Integer, default=0)
    counted: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = uncounted (red)
    count: Mapped[CycleCount] = relationship(back_populates="lines")
    item: Mapped[InventoryItem] = relationship()


# ---------------------------------------------------------------------------
# Pricing — Section 5
# ---------------------------------------------------------------------------

class PricingConfig(Base):
    """One row per game; rules stored as a structured JSON document.

    Pricing is scoped by game (not marketplace) because the games differ too
    much to share rules; per-platform differences are expressed as offsets
    inside each tier (see services/pricing.py).
    """
    __tablename__ = "pricing_configs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game: Mapped[str] = mapped_column(String, unique=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


# ---------------------------------------------------------------------------
# Marketplace accounts & listing rules — Section 6
# ---------------------------------------------------------------------------

class MarketplaceAccount(Base):
    __tablename__ = "marketplace_accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    marketplace: Mapped[str] = mapped_column(String, unique=True)
    status: Mapped[str] = mapped_column(String, default="disconnected")
    # connected | paused | disconnected
    credentials: Mapped[dict] = mapped_column(JSON, default=dict)
    poll_interval_minutes: Mapped[int] = mapped_column(Integer, default=10)
    auto_push_on_add: Mapped[bool] = mapped_column(Boolean, default=False)
    last_order_poll_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ListingRule(Base):
    __tablename__ = "listing_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    marketplace: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String)
    priority: Mapped[int] = mapped_column(Integer, default=0)  # lower = higher priority
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # filters: {games: [], sets: [], conditions: [], product_types: [], price_min, price_max}
    filters: Mapped[dict] = mapped_column(JSON, default=dict)
    condition_allowlist: Mapped[list] = mapped_column(JSON, default=list)  # empty = all
    block_sealed: Mapped[bool] = mapped_column(Boolean, default=False)
    block_singles: Mapped[bool] = mapped_column(Boolean, default=False)
    # eBay-specific: business policy IDs, category, best offer
    ebay_fulfillment_policy_id: Mapped[str | None] = mapped_column(String, nullable=True)
    ebay_payment_policy_id: Mapped[str | None] = mapped_column(String, nullable=True)
    ebay_return_policy_id: Mapped[str | None] = mapped_column(String, nullable=True)
    ebay_category_id: Mapped[str | None] = mapped_column(String, nullable=True)
    best_offer: Mapped[dict] = mapped_column(JSON, default=dict)
    # {enabled: bool, auto_accept_pct: 90, auto_decline_pct: 60}  (floored at COGS)


# ---------------------------------------------------------------------------
# Lots — Section 7
# ---------------------------------------------------------------------------

class LotTemplate(Base):
    __tablename__ = "lot_templates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text, default="")
    filters: Mapped[dict] = mapped_column(JSON, default=dict)
    # {games: [], sets: [], rarities: [], conditions: [], price_min, price_max}
    lot_size: Mapped[int] = mapped_column(Integer, default=100)
    pricing_method: Mapped[str] = mapped_column(String, default="value_margin")  # value_margin | fixed
    margin_pct: Mapped[float] = mapped_column(Float, default=80.0)  # % of total value
    fixed_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_duplicates: Mapped[int] = mapped_column(Integer, default=4)


class Lot(Base):
    __tablename__ = "lots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("lot_templates.id"), nullable=True)
    name: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="open")  # open | listed | sold | dissolved
    price: Mapped[float] = mapped_column(Float, default=0.0)
    total_value: Mapped[float] = mapped_column(Float, default=0.0)
    marketplace: Mapped[str | None] = mapped_column(String, nullable=True)
    external_listing_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    items: Mapped[list["LotItem"]] = relationship(back_populates="lot", cascade="all, delete-orphan")


class LotItem(Base):
    __tablename__ = "lot_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("lots.id"), index=True)
    inventory_id: Mapped[int] = mapped_column(ForeignKey("inventory.id"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)  # reserved from inventory
    unit_value: Mapped[float] = mapped_column(Float, default=0.0)
    lot: Mapped[Lot] = relationship(back_populates="items")
    item: Mapped[InventoryItem] = relationship()


# ---------------------------------------------------------------------------
# Orders & fulfillment — Section 8
# ---------------------------------------------------------------------------

class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    marketplace: Mapped[str] = mapped_column(String, index=True)  # ebay | tcgplayer | manual
    external_order_id: Mapped[str] = mapped_column(String, index=True)
    buyer_name: Mapped[str] = mapped_column(String, default="")
    ship_to: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String, default="open", index=True)
    # open | shipped | cancelled | refunded | partially_refunded
    is_direct: Mapped[bool] = mapped_column(Boolean, default=False)  # TCGplayer Direct
    order_total: Mapped[float] = mapped_column(Float, default=0.0)  # item subtotal (revenue)
    marketplace_fees: Mapped[float] = mapped_column(Float, default=0.0)
    shipping_cost: Mapped[float] = mapped_column(Float, default=0.0)  # what we paid (label)
    shipping_charged: Mapped[float] = mapped_column(Float, default=0.0)  # what the buyer paid us for shipping (revenue)
    amount_refunded: Mapped[float] = mapped_column(Float, default=0.0)  # refunded to buyer (partial or full)
    return_shipping_cost: Mapped[float] = mapped_column(Float, default=0.0)  # what we paid to get it back
    tracking_number: Mapped[str | None] = mapped_column(String, nullable=True)
    carrier: Mapped[str | None] = mapped_column(String, nullable=True)
    label_url: Mapped[str | None] = mapped_column(String, nullable=True)
    deduction_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    ordered_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    __table_args__ = (UniqueConstraint("marketplace", "external_order_id"),)


class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    inventory_id: Mapped[int | None] = mapped_column(ForeignKey("inventory.id"), nullable=True)
    # Catalog card the sold unit was, for lines with no live inventory record
    # (e.g. migrated/historical sales) — lets Reports attribute them by game/set.
    catalog_card_id: Mapped[int | None] = mapped_column(ForeignKey("catalog_cards.id"), nullable=True)
    lot_id: Mapped[int | None] = mapped_column(ForeignKey("lots.id"), nullable=True)
    description: Mapped[str] = mapped_column(String, default="")
    marketplace_product_id: Mapped[str | None] = mapped_column(String, nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[float] = mapped_column(Float, default=0.0)
    cogs: Mapped[float] = mapped_column(Float, default=0.0)  # filled by FIFO at deduction time
    order: Mapped[Order] = relationship(back_populates="items")
    item: Mapped[InventoryItem | None] = relationship()
