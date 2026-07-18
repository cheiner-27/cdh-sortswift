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
        db, buyer_name="t", ordered_at="2026-01-01",
        items=[{"inventory_id": item.id, "quantity": 1, "unit_price": 9.0}])
    assert o.ordered_at.year == 2026 and o.ordered_at.month == 1 and o.ordered_at.day == 1


def test_set_order_costs_updates_sale_date(db, card):
    item = stock(db, card)
    o = order_svc.create_manual_order(
        db, buyer_name="t",
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
        db, buyer_name="t", marketplace_fees=4.0,
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
