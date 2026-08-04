"""Coverage for the additions: editable sale date, configurable pick-list
ordering, and the Pokémon catalog de-duplication."""
from datetime import timezone

from app.models import (
    AcquisitionLog, CatalogCard, CatalogSet, InventoryItem, Order, OrderItem,
)
from app.services import catalog as cat_svc
from app.services import inventory as inv
from app.services import orders as order_svc
from app.services.settings import set_setting


def stock(db, card, qty=3, cost=5.0, condition="NM", bin="A"):
    item = inv.find_or_create_item(db, catalog_card_id=card.id,
                                   condition=condition, bin=bin)
    inv.add_stock(db, item, qty, cost)
    db.commit()
    return item


# --- editable sale date ----------------------------------------------------

def test_manual_order_backdated(db, card):
    item = stock(db, card)
    o = order_svc.create_manual_order(
        db, platform="t", ordered_at="2026-01-01",
        items=[{"inventory_id": item.id, "quantity": 1, "unit_price": 9.0}])
    assert o.ordered_at.year == 2026 and o.ordered_at.month == 1 and o.ordered_at.day == 1


def test_set_order_costs_updates_sale_date(db, card):
    item = stock(db, card)
    o = order_svc.create_manual_order(
        db, platform="t",
        items=[{"inventory_id": item.id, "quantity": 1, "unit_price": 9.0}])
    order_svc.set_order_costs(db, o, ordered_at="2025-12-31")
    assert o.ordered_at.day == 31 and o.ordered_at.month == 12 and o.ordered_at.year == 2025


def test_parse_sale_date_forms():
    assert order_svc.parse_sale_date("") is None
    d = order_svc.parse_sale_date("2026-07-04")
    assert (d.year, d.month, d.day) == (2026, 7, 4) and d.tzinfo == timezone.utc


# --- configurable pick-list ordering ---------------------------------------

def _pokemon_card(db, name, number, pid, code="BS2", set_name="Base Set 2"):
    c = CatalogCard(game="pokemon", external_id=f"{code}-{number}-{name}",
                    tcgplayer_product_id=pid, set_code=code, set_name=set_name,
                    collector_number=number, name=name, finishes=["normal"],
                    languages=["en"])
    db.add(c)
    db.commit()
    return c


def _order_with(db, lines):
    """lines: list of (inventory_id_or_None, description, condition-ignored)."""
    o = Order(marketplace="manual", external_order_id=f"po-{id(lines)}",
              buyer_name="t", order_total=0.0)
    db.add(o)
    db.flush()
    for inv_id, desc in lines:
        db.add(OrderItem(order_id=o.id, inventory_id=inv_id, description=desc,
                         quantity=1, unit_price=1.0))
    db.commit()
    return o


def test_pick_list_default_condition_then_name(db):
    a = _pokemon_card(db, "Zapdos", "1", 201)
    b = _pokemon_card(db, "Abra", "2", 202)
    # Zapdos is NM, Abra is LP → NM first even though 'Abra' is alphabetically first.
    nm = stock(db, a, condition="NM", bin="B2")
    lp = stock(db, b, condition="LP", bin="B1")
    o = _order_with(db, [(nm.id, "Zapdos"), (lp.id, "Abra")])
    rows = order_svc.build_pick_list(db, [o])
    assert [r["name"] for r in rows] == ["Zapdos", "Abra"]  # NM before LP


def test_pick_list_custom_and_unmatched_sort_last(db):
    a = _pokemon_card(db, "Bulbasaur", "1", 301)
    item = stock(db, a, condition="NM")
    o = _order_with(db, [(None, "Some Sealed Box"), (item.id, "Bulbasaur")])
    rows = order_svc.build_pick_list(db, [o])
    assert rows[0]["name"] == "Bulbasaur"
    assert rows[-1]["name"] == "Some Sealed Box" and rows[-1]["is_other"] is True


def test_pick_list_configurable_by_bin(db):
    a = _pokemon_card(db, "Aaa", "1", 401)
    b = _pokemon_card(db, "Bbb", "2", 402)
    hi = stock(db, a, condition="NM", bin="Z9")
    lo = stock(db, b, condition="NM", bin="A1")
    set_setting(db, "pick_list_sort", ["bin"])
    db.commit()
    o = _order_with(db, [(hi.id, "Aaa"), (lo.id, "Bbb")])
    rows = order_svc.build_pick_list(db, [o])
    assert [r["bin"] for r in rows] == ["A1", "Z9"]


# --- P&L cost spreading across games/sets ----------------------------------

def test_pnl_by_game_splits_order_costs_pro_rata(db, card):
    """A mixed-game order splits its fees pro-rata by item subtotal, while each
    line's COGS/units land in its own game (regression: order-level costs used
    to be dumped entirely on whichever group sorted first)."""
    from app.services import reports as report_svc

    mtg = stock(db, card, qty=2, cost=10.0)              # mtg (fixture card)
    pkmn_card = _pokemon_card(db, "Pikachu", "58", 500)
    pkmn = stock(db, pkmn_card, qty=2, cost=2.0)

    o = order_svc.create_manual_order(
        db, platform="t", marketplace_fees=4.0,
        items=[{"inventory_id": mtg.id, "quantity": 1, "unit_price": 30.0},
               {"inventory_id": pkmn.id, "quantity": 1, "unit_price": 10.0}])
    order_svc.mark_shipped(db, o)

    rows = {r["group"]: r for r in report_svc.realized_pnl(db, group_by="game")}
    assert set(rows) == {"mtg", "pokemon"}
    # subtotal 40 → mtg 75% / pokemon 25%, so the $4 fee splits 3.0 / 1.0
    assert rows["mtg"]["fees"] == 3.0 and rows["pokemon"]["fees"] == 1.0
    assert rows["mtg"]["cogs"] == 10.0 and rows["pokemon"]["cogs"] == 2.0
    assert rows["mtg"]["revenue"] == 30.0 and rows["pokemon"]["revenue"] == 10.0
    assert rows["mtg"]["profit"] == 17.0 and rows["pokemon"]["profit"] == 7.0
    assert rows["mtg"]["units"] == 1 and rows["pokemon"]["units"] == 1


# --- Pokémon dedupe --------------------------------------------------------

def test_dedupe_remaps_and_removes_legacy(db):
    db.add(CatalogSet(game="pokemon", code="BS2", name="Base Set 2"))
    db.add(CatalogSet(game="pokemon", code="base4", name="Base Set 2"))
    canon = _pokemon_card(db, "Chansey", "4/130", 42471, code="BS2")
    legacy = CatalogCard(game="pokemon", external_id="base4-4",
                         tcgplayer_product_id=None, set_code="base4",
                         set_name="Base Set 2", collector_number="4",
                         name="Chansey", finishes=["normal"], languages=["en"])
    db.add(legacy)
    db.commit()
    item = inv.find_or_create_item(db, catalog_card_id=legacy.id, condition="NM", bin="A")
    inv.add_stock(db, item, 2, 3.0)  # also records a FIFO acquisition batch
    db.commit()
    legacy_id = legacy.id

    res = cat_svc.deduplicate_pokemon_catalog(db)
    assert res["remapped_cards"] == 1
    db.refresh(item)
    assert item.catalog_card_id == canon.id                     # inventory repointed
    assert db.get(CatalogCard, legacy_id) is None               # legacy card gone
    assert db.query(CatalogSet).filter_by(code="base4").first() is None  # legacy set gone
    aqs = db.query(AcquisitionLog).all()
    assert aqs and all(aq.catalog_card_id == canon.id for aq in aqs)  # FIFO batches carried over
    # idempotent
    assert cat_svc.deduplicate_pokemon_catalog(db)["remapped_cards"] == 0


# --- manual-sale platform ----------------------------------------------------

def test_manual_sale_records_the_platform_as_its_marketplace(db, card):
    """A hand-entered TCGplayer sale should count as TCGplayer revenue, not sit
    in a separate 'manual' channel."""
    item = inv.find_or_create_item(db, catalog_card_id=card.id, condition="NM")
    inv.add_stock(db, item, 2, 1.0)
    db.commit()
    o = order_svc.create_manual_order(
        db, platform="TCGplayer",
        items=[{"inventory_id": item.id, "quantity": 1, "unit_price": 9.0}])
    assert o.marketplace == "tcgplayer"
    assert o.buyer_name == ""                       # names aren't retained
    assert order_svc.is_manual_entry(o)             # still known as hand-entered


def test_platform_key_canonicalises_names():
    assert order_svc.platform_key("eBay") == "ebay"
    assert order_svc.platform_key("TCGplayer") == "tcgplayer"
    assert order_svc.platform_key("Card Kingdom") == "cardkingdom"
    assert order_svc.platform_key("") == "manual"


def test_manual_entry_is_told_apart_by_its_id_prefix(db):
    """`marketplace` now holds the platform, so it can't distinguish a typed-in
    sale from a synced one — the `manual-` id prefix is what does."""
    typed = Order(marketplace="tcgplayer", external_order_id="manual-20260101",
                  order_total=5.0)
    synced = Order(marketplace="tcgplayer", external_order_id="5BABB616-AA-01",
                   order_total=5.0)
    assert order_svc.is_manual_entry(typed)
    assert not order_svc.is_manual_entry(synced)


def test_marking_a_typed_sale_shipped_skips_the_marketplace_call(db, card):
    """The marketplace never heard of a hand-entered order id, so telling it to
    mark shipped would only produce a failure warning."""
    item = inv.find_or_create_item(db, catalog_card_id=card.id, condition="NM")
    inv.add_stock(db, item, 1, 1.0)
    db.commit()
    o = order_svc.create_manual_order(
        db, platform="TCGplayer",
        items=[{"inventory_id": item.id, "quantity": 1, "unit_price": 9.0}])
    result = order_svc.mark_shipped(db, o, tracking_number="1Z999", carrier="USPS")
    assert result["status"] == "shipped"
    assert result["warnings"] == []


def test_platform_list_includes_previously_used_ones(db, card):
    item = inv.find_or_create_item(db, catalog_card_id=card.id, condition="NM")
    inv.add_stock(db, item, 1, 1.0)
    db.commit()
    order_svc.create_manual_order(
        db, platform="Card Kingdom",
        items=[{"inventory_id": item.id, "quantity": 1, "unit_price": 9.0}])
    platforms = order_svc.manual_sale_platforms(db)
    assert "TCGplayer" in platforms and "eBay" in platforms   # defaults
    assert "cardkingdom" in platforms                         # previously used


def test_legacy_manual_orders_migrate_platform_and_drop_names():
    """ensure_schema moves the platform out of buyer_name into marketplace and
    clears retained buyer names, including the copy inside ship_to."""
    import json as _json

    from sqlalchemy import text

    from app.db import Base, engine, ensure_schema

    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO orders (marketplace, external_order_id, buyer_name, "
            "ship_to, status, order_total, marketplace_fees, shipping_cost, "
            "shipping_charged, amount_refunded, fees_refunded, "
            "return_shipping_cost, deduction_applied, is_direct, ordered_at) VALUES "
            "('manual', 'manual-mig-1', 'TCGplayer', '{}', 'shipped', 5.0, "
            "0, 0, 0, 0, 0, 0, 0, 0, '2026-01-01 00:00:00')"))
        conn.execute(text(
            "INSERT INTO orders (marketplace, external_order_id, buyer_name, "
            "ship_to, status, order_total, marketplace_fees, shipping_cost, "
            "shipping_charged, amount_refunded, fees_refunded, "
            "return_shipping_cost, deduction_applied, is_direct, ordered_at) VALUES "
            "('tcgplayer', 'mig-named-1', 'Jane Doe', "
            """'{"city": "AUSTIN", "state": "TX", "name": "Jane Doe"}', """
            "'open', 5.0, 0, 0, 0, 0, 0, 0, 0, 0, '2026-01-01 00:00:00')"))

    ensure_schema()

    with engine.begin() as conn:
        mkt, buyer = conn.execute(text(
            "SELECT marketplace, buyer_name FROM orders "
            "WHERE external_order_id = 'manual-mig-1'")).one()
        assert mkt == "tcgplayer"          # platform moved out of buyer_name
        assert buyer == ""

        buyer, ship = conn.execute(text(
            "SELECT buyer_name, ship_to FROM orders "
            "WHERE external_order_id = 'mig-named-1'")).one()
        assert buyer == ""
        data = _json.loads(ship) if isinstance(ship, str) else ship
        assert "name" not in data
        assert data["city"] == "AUSTIN" and data["state"] == "TX"

        ensure_schema()                    # idempotent
        conn.execute(text("DELETE FROM orders WHERE external_order_id "
                          "IN ('manual-mig-1', 'mig-named-1')"))


def test_ensure_schema_adds_columns_to_a_table_that_predates_them():
    """create_all only ever creates whole tables — it will not add a column to a
    table that already exists. Any column added to a shipped model therefore
    needs a migration here, or the live DB 500s on the first query that selects
    it. Regression: slip_orders.fee_overridden shipped without one.
    """
    from sqlalchemy import inspect, text

    from app.db import Base, engine, ensure_schema

    Base.metadata.create_all(engine)
    with engine.begin() as conn:  # pretend this DB was built before the column
        conn.execute(text("ALTER TABLE slip_orders DROP COLUMN fee_overridden"))
    assert "fee_overridden" not in {
        c["name"] for c in inspect(engine).get_columns("slip_orders")}

    ensure_schema()

    # Every column the models declare must now exist on disk, for every table.
    insp = inspect(engine)
    on_disk = set(insp.get_table_names())
    for name, table in Base.metadata.tables.items():
        if name not in on_disk:
            continue
        have = {c["name"] for c in insp.get_columns(name)}
        missing = {c.name for c in table.columns} - have
        assert not missing, f"{name} is missing {sorted(missing)}"

    ensure_schema()   # idempotent
