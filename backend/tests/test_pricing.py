"""Pricing engine: ordered sources, tiers (by current price), multiplicative
modifiers, per-platform offsets, guards, and rounding."""
import copy

from app.services import inventory as inv
from app.services import pricing

BASE_CONFIG = {
    "sources": ["tcg_market"],
    "tiers": [{
        "name": "all", "min": 0, "max": None,
        "modifiers": {
            "condition": {"NM": 100, "LP": 85, "MP": 70, "HP": 50, "DMG": 30},
            "printing": {}, "language": {}, "age_decay": {"days": 0, "pct": 0},
        },
        "offsets": {"ebay": {"pct": 0, "flat": 0}, "tcgplayer": {"pct": 0, "flat": 0}},
        "guards": {"max_move_pct": None, "tier_lock": {"up": False, "down": False},
                   "rarity_floors": {}, "cost_floor": True},
        "rounding": "0.01",
    }],
    "set_overrides": {}, "card_overrides": {},
}


def cfg():
    return copy.deepcopy(BASE_CONFIG)


def make_item(db, card, condition="NM", printing="normal", cost=None, qty=1):
    item = inv.find_or_create_item(db, catalog_card_id=card.id,
                                   condition=condition, printing=printing, bin="A")
    inv.add_stock(db, item, qty, cost)
    db.commit()
    return item


def test_condition_modifier(db, card):
    item = make_item(db, card, condition="LP")
    r = pricing.price_item(db, item, "ebay", cfg())
    assert r["price"] == 8.50  # $10 market x 85%


def test_modifiers_stack_multiplicatively(db, card):
    c = cfg()
    c["tiers"][0]["modifiers"]["language"] = {"ja": 50}
    item = make_item(db, card, condition="LP")
    item.language = "ja"
    r = pricing.price_item(db, item, "ebay", c)
    assert r["price"] == round(10 * 0.85 * 0.50, 2)  # LP x JP = 4.25


def test_foil_uses_foil_price_row(db, card):
    item = make_item(db, card, printing="foil")
    r = pricing.price_item(db, item, "ebay", cfg())
    assert r["base"] == 25.0


def test_source_fallback_skips_missing(db, card):
    # Foil row has no direct_low -> fall through to market (25.0)
    c = cfg()
    c["sources"] = ["tcg_direct_low", "tcg_market"]
    item = make_item(db, card, printing="foil")
    r = pricing.price_item(db, item, "ebay", c)
    assert r["base"] == 25.0


def test_cost_floor_guard(db, card):
    item = make_item(db, card, condition="DMG", cost=6.00)
    r = pricing.price_item(db, item, "ebay", cfg())  # 30% of $10 = $3, floored to cost $6
    assert r["price"] == 6.00


def test_marketplace_floor_raises_listed_price_only(db, card):
    c = cfg()
    c["tiers"][0]["modifiers"]["condition"]["DMG"] = 3  # forces sub-$0.99
    item = make_item(db, card, condition="DMG")
    r = pricing.price_item(db, item, "ebay", c)
    assert r["price"] < 0.99
    assert r["marketplace_price"] == 0.99


# --- at-a-glance market value (scan/staging sifting) -----------------------

def test_card_market_value_printing_aware(db, card):
    # Normal market 10.0, Foil market 25.0 (from the `card` fixture price rows).
    assert pricing.card_market_value(db, card, "normal") == 10.0
    assert pricing.card_market_value(db, card, "foil") == 25.0


def test_card_market_value_none_without_price_data(db):
    from app.models import CatalogCard
    c = CatalogCard(game="mtg", external_id="no-price", tcgplayer_product_id=999,
                    set_code="X", collector_number="1", name="Priceless",
                    finishes=["normal"], languages=["en"])
    db.add(c)
    db.commit()
    assert pricing.card_market_value(db, c, "normal") is None  # no PriceData rows
    assert pricing.card_market_value(db, None, "normal") is None


def test_market_values_for_items_matches_single_lookup(db, card):
    from app.models import CatalogCard, CustomProduct, CustomSku
    normal = make_item(db, card, printing="normal")
    foil = make_item(db, card, printing="foil")
    unpriced_card = CatalogCard(game="mtg", external_id="batch-no-price",
                                tcgplayer_product_id=888, set_code="X",
                                collector_number="2", name="Unpriced",
                                finishes=["normal"], languages=["en"])
    db.add(unpriced_card)
    db.commit()
    unpriced = make_item(db, unpriced_card)
    product = CustomProduct(category="sealed", name="Sealed box", item_type="sealed")
    db.add(product)
    db.flush()
    sku = CustomSku(product_id=product.id)
    db.add(sku)
    db.flush()
    noncatalog = inv.find_or_create_item(db, custom_sku_id=sku.id, bin="A")
    db.commit()

    values = pricing.market_values_for_items(db, [normal, foil, unpriced, noncatalog])
    assert values[normal.id] == pricing.card_market_value(db, card, "normal") == 10.0
    assert values[foil.id] == pricing.card_market_value(db, card, "foil") == 25.0
    assert values[unpriced.id] is None   # catalog card with no price rows
    assert values[noncatalog.id] is None  # custom item, no catalog card


def test_platform_offsets_differ(db, card):
    c = cfg()
    c["tiers"][0]["offsets"]["ebay"] = {"pct": 20, "flat": 0}
    c["tiers"][0]["offsets"]["tcgplayer"] = {"pct": 0, "flat": 0}
    item = make_item(db, card)
    assert pricing.price_item(db, item, "ebay", c)["price"] == 12.0     # +20%
    assert pricing.price_item(db, item, "tcgplayer", c)["price"] == 10.0


def test_offset_pct_then_flat(db, card):
    c = cfg()
    c["tiers"][0]["offsets"]["ebay"] = {"pct": 13.25, "flat": 1.00}
    item = make_item(db, card)
    r = pricing.price_item(db, item, "ebay", c)
    assert r["price"] == round(10 * 1.1325 + 1.00, 2)


def test_max_move_cap(db, card):
    c = cfg()
    c["tiers"][0]["guards"]["max_move_pct"] = 10
    item = make_item(db, card)
    item.current_price = 5.00  # market says $10 -> capped at +10%
    r = pricing.price_item(db, item, "ebay", c)
    assert r["price"] == 5.50


def test_tier_lock_down_floors_to_band(db, card):
    c = cfg()
    low = {**cfg()["tiers"][0], "name": "low", "min": 1, "max": 5}
    low["offsets"]["ebay"] = {"pct": -95, "flat": 0}  # 10 -> 0.5, below band
    low["guards"]["tier_lock"] = {"up": False, "down": True}
    low["guards"]["cost_floor"] = False
    c["tiers"] = [low]
    item = make_item(db, card)
    item.current_price = 3.00  # sits in the $1-5 band
    r = pricing.price_item(db, item, "ebay", c)
    assert r["price"] == 1.00  # locked from moving down out of its tier


def test_tier_selected_by_current_price(db, card):
    c = cfg()
    bulk = {**cfg()["tiers"][0], "name": "bulk", "min": 0, "max": 1}
    mid = {**cfg()["tiers"][0], "name": "mid", "min": 1, "max": None}
    c["tiers"] = [bulk, mid]
    item = make_item(db, card)
    item.current_price = 0.50  # bulk tier, even though market base is $10
    r = pricing.price_item(db, item, "ebay", c)
    assert any("bulk" in t for t in r["trace"])


def test_per_item_override_bypasses_rules(db, card):
    item = make_item(db, card)
    item.price_override = 99.99
    r = pricing.price_item(db, item, "ebay", cfg())
    assert r["price"] == 99.99 and r["status"] == "override"


def test_set_suppression(db, card):
    c = cfg()
    c["set_overrides"] = {"MH3": {"suppress": True}}
    item = make_item(db, card)
    r = pricing.price_item(db, item, "ebay", c)
    assert r["status"] == "suppressed"


def test_rounding_rules():
    assert pricing.apply_rounding(10.37, "0.99") == 9.99   # nearest .99
    assert pricing.apply_rounding(10.37, "0.49") == 10.49
    assert pricing.apply_rounding(10.95, "0.95") == 10.95
    assert pricing.apply_rounding(10.32, "0.10") == 10.30
    assert pricing.apply_rounding(10.32, "0.05") == 10.30
    assert pricing.apply_rounding(10.40, "1") == 10.0
    assert pricing.apply_rounding(0.40, "0.99") == 0.99
    assert pricing.apply_rounding(10.373, "0.01") == 10.37


def test_printing_subtypes_are_specific_and_order_independent(db, card):
    from app.models import PriceData
    db.add_all([
        PriceData(tcgplayer_product_id=111, sub_type="Reverse Holofoil", market=7),
        PriceData(tcgplayer_product_id=111, sub_type="Holofoil", market=18),
        PriceData(tcgplayer_product_id=111, sub_type="1st Edition", market=30),
    ])
    db.commit()
    rows = pricing._price_rows(db, 111)
    assert pricing._pick_price_row(list(reversed(rows)), "reverse_holo").market == 7
    assert pricing._pick_price_row(rows, "holo").market == 18
    assert pricing._pick_price_row(rows, "first_edition").market == 30
    item = make_item(db, card, printing="reverse_holo")
    result = pricing.price_item(db, item, "tcgplayer", cfg())
    assert result["base"] == 7 and "[Reverse Holofoil]" in result["trace"][0]


def test_missing_printing_does_not_use_arbitrary_foil(db, card):
    item = make_item(db, card, printing="reverse_holo")
    assert pricing.price_item(db, item, "tcgplayer", cfg())["status"] == "no_source"


def test_legacy_age_config_upgrades_without_mutation(db, card):
    from datetime import datetime, timedelta, timezone
    item = make_item(db, card)
    inv._fifo_batches(db, item)[0].acquired_at = datetime.now(timezone.utc) - timedelta(days=95)
    config = cfg()
    config["tiers"][0]["modifiers"]["age_decay"] = {"days": 30, "pct": 15}
    assert pricing.price_item(db, item, "tcgplayer", config)["price"] == 8.5
    assert config["tiers"][0]["modifiers"]["age_decay"] == {"days": 30, "pct": 15}


def test_age_ladder_uses_highest_reached_step_and_anchor(db, card):
    from datetime import datetime, timedelta, timezone
    item = make_item(db, card)
    now = datetime.now(timezone.utc)
    inv._fifo_batches(db, item)[0].acquired_at = now - timedelta(days=125)
    config = cfg()
    decay = {"anchor": None, "steps": [
        {"days": 120, "pct": 20}, {"days": 30, "pct": 5}, {"days": 60, "pct": 10}]}
    config["tiers"][0]["modifiers"]["age_decay"] = decay
    assert pricing.price_item(db, item, "tcgplayer", config)["price"] == 8
    decay["anchor"] = now.date().isoformat()
    result = pricing.price_item(db, item, "tcgplayer", config)
    assert result["price"] == 10 and any("anchored 0d" in t for t in result["trace"])
    decay["anchor"] = (now - timedelta(days=65)).date().isoformat()
    assert pricing.price_item(db, item, "tcgplayer", config)["price"] == 9
    assert pricing.effective_age(2, decay) == 2
    assert pricing.effective_age(None, decay) is None


def test_ignored_item_and_card_overrides_preserve_floors_and_suppression(db, card):
    item = make_item(db, card)
    item.price_override = 80
    item.price_floor = 12
    config = cfg()
    config["card_overrides"][str(card.id)] = {"fixed_price": 70}
    assert pricing.price_item(db, item, "tcgplayer", config, ignore_overrides=True)["price"] == 12
    item.price_override = None
    assert pricing.price_item(db, item, "tcgplayer", config)["price"] == 70
    assert pricing.price_item(db, item, "tcgplayer", config, ignore_overrides=True)["price"] == 12
    config["set_overrides"]["MH3"] = {"suppress": True}
    assert pricing.price_item(db, item, "tcgplayer", config, ignore_overrides=True)["status"] == "suppressed"


def test_simulation_reports_true_and_effective_age(db, card):
    from datetime import datetime, timedelta, timezone
    from app.models import PricingConfig
    item = make_item(db, card)
    now = datetime.now(timezone.utc)
    inv._fifo_batches(db, item)[0].acquired_at = now - timedelta(days=95)
    config = cfg()
    config["tiers"][0]["modifiers"]["age_decay"] = {
        "anchor": now.date().isoformat(), "steps": [{"days": 30, "pct": 5}]}
    db.add(PricingConfig(game="mtg", config=config))
    db.commit()
    result = pricing.simulate(db, "tcgplayer", [item])[0]
    assert result["age_days"] == 95 and result["age_days_effective"] == 0


def test_age_config_validation_rejects_invalid_inputs(db):
    import pytest
    from fastapi import HTTPException
    from app.routers.pricing import put_config
    invalid = [
        {"anchor": "not-a-date", "steps": []},
        {"anchor": None, "steps": [{"days": 1.5, "pct": 10}]},
        {"anchor": None, "steps": [{"days": 30, "pct": 101}]},
        {"anchor": None, "steps": [{"days": 30, "pct": 5}, {"days": 30, "pct": 10}]},
        {"anchor": None, "steps": "wrong"},
    ]
    for decay in invalid:
        config = cfg()
        config["tiers"][0]["modifiers"]["age_decay"] = decay
        with pytest.raises(HTTPException):
            put_config("mtg", config, db)
