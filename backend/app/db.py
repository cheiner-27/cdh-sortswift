"""Database engine and session management."""
import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATA_DIR = Path(os.environ.get("SORTSWIFT_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "sortswift.db"

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_schema() -> None:
    """Lightweight additive migration for the on-disk DB.

    We have no Alembic and ``create_all`` only creates *missing tables* — it
    never adds a column to a table that already exists. So when the model
    gains a column, an existing ``sortswift.db`` (which also holds inventory,
    orders, etc. and must not be wiped) needs the column added by hand. This
    is idempotent and safe to run on every startup.
    """
    from sqlalchemy import inspect, text
    from .models import PricingConfig, collector_number_key

    insp = inspect(engine)
    tables = set(insp.get_table_names())
    if not tables:
        return  # fresh DB: create_all already built the current schema

    # 1) catalog_cards.collector_number_norm (numerator matching key)
    if "catalog_cards" in tables:
        cols = {c["name"] for c in insp.get_columns("catalog_cards")}
        if "collector_number_norm" not in cols:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE catalog_cards ADD COLUMN collector_number_norm VARCHAR"))
                rows = conn.execute(
                    text("SELECT id, collector_number FROM catalog_cards")).fetchall()
                for rid, cn in rows:
                    conn.execute(
                        text("UPDATE catalog_cards SET collector_number_norm = :k "
                             "WHERE id = :i"),
                        {"k": collector_number_key(cn), "i": rid})
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_cards_game_numnorm "
                    "ON catalog_cards (game, collector_number_norm)"))

    # 2) pricing_configs re-keyed marketplace -> game (Section 5 redesign).
    # The old per-marketplace configs use a different, incompatible schema and
    # a different scope, so the safest migration is to drop the old table and
    # let the new game-scoped one be recreated. Only pricing rules are lost;
    # inventory/orders/etc. are untouched.
    if "pricing_configs" in tables:
        pcols = {c["name"] for c in insp.get_columns("pricing_configs")}
        if "game" not in pcols:
            with engine.begin() as conn:
                conn.execute(text("DROP TABLE pricing_configs"))
            PricingConfig.__table__.create(engine)

    # 3) orders: refund/return accounting + shipping-charged columns (additive).
    if "orders" in tables:
        ocols = {c["name"] for c in insp.get_columns("orders")}
        with engine.begin() as conn:
            if "amount_refunded" not in ocols:
                conn.execute(text(
                    "ALTER TABLE orders ADD COLUMN amount_refunded FLOAT DEFAULT 0.0"))
            if "return_shipping_cost" not in ocols:
                conn.execute(text(
                    "ALTER TABLE orders ADD COLUMN return_shipping_cost FLOAT DEFAULT 0.0"))
            if "shipping_charged" not in ocols:
                conn.execute(text(
                    "ALTER TABLE orders ADD COLUMN shipping_charged FLOAT DEFAULT 0.0"))

    # 4) staging: preserve original acquisition date through the review buffer.
    if "staging" in tables:
        scols = {c["name"] for c in insp.get_columns("staging")}
        if "acquired_at" not in scols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE staging ADD COLUMN acquired_at DATETIME"))

    # 5) order_items: catalog card link for sold lines with no live inventory
    #    record (migrated/historical sales) — enables by-game/set P&L.
    if "order_items" in tables:
        oicols = {c["name"] for c in insp.get_columns("order_items")}
        if "catalog_card_id" not in oicols:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE order_items ADD COLUMN catalog_card_id INTEGER "
                    "REFERENCES catalog_cards(id)"))

    # 6) expenses.expense_class: capex vs opex classification (additive).
    #    Existing rows default to opex; backfill durable-asset categories
    #    (Equipment) to capex so the printer/scanner land in capital spend.
    if "expenses" in tables:
        ecols = {c["name"] for c in insp.get_columns("expenses")}
        if "expense_class" not in ecols:
            from .domain import CAPEX_CATEGORIES
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE expenses ADD COLUMN expense_class VARCHAR DEFAULT 'opex'"))
                for cat in CAPEX_CATEGORIES:
                    conn.execute(
                        text("UPDATE expenses SET expense_class = 'capex' "
                             "WHERE category = :c"), {"c": cat})
