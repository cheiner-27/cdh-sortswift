"""Automated pricing engine (Section 5).

Rules are configured **per game** (things differ too much between games to share
one set of rules) and produce a **per-platform** price. One config is a JSON
document per game (PricingConfig.config):

{
  "sources": ["tcg_market", "tcg_mid", "tcg_low", "tcg_direct_low"],
      # ordered fallback: the first source that has a value is used

  "tiers": [                       # bands on the card's CURRENT price
    {
      "name": "bulk", "min": 0, "max": 1,

      "modifiers": {               # stack MULTIPLICATIVELY (e.g. LP .85 x JP .5)
         "condition": {"NM": 100, "LP": 85, "MP": 70, "HP": 50, "DMG": 30},
         "printing":  {"normal": 100, "foil": 110},
         "language":  {"en": 100, "ja": 50},
         "age_decay": {"days": 30, "pct": 5}},   # >= days in stock -> x(100-pct)%

      "offsets": {                 # applied AFTER modifiers, one per platform
         "ebay":      {"pct": 0, "flat": 0},
         "tcgplayer": {"pct": 0, "flat": 0}},

      "guards": {
         "max_move_pct": null,                    # don't move > x% from current
         "tier_lock": {"up": false, "down": false},  # keep price inside the band
         "rarity_floors": {"common": 0.10},       # min price per rarity
         "cost_floor": true},                     # never below FIFO cost

      "rounding": "0.99"           # nearest 0.01 / 0.05 / 0.10 / 0.49 / .95 / .99 / 1
    }
  ],

  "set_overrides":  {"MH3": {"suppress": true}},
  "card_overrides": {"123": {"fixed_price": 5.0}}
}

Computation order for one item on one platform:
  source (fallback) -> tier (by current price) -> multiplicative modifiers ->
  platform offset -> guards (max-move, tier-lock, floors) -> rounding ->
  marketplace-imposed minimum.

Per-item overrides on the InventoryItem (price_override / price_floor) still win
and can be set from the Inventory page.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import MARKETPLACES, MARKETPLACE_MIN_PRICE
from ..models import InventoryItem, PriceData, PricingConfig
from . import inventory as inv_svc

SOURCE_FIELDS = {
    "tcg_market": "market",
    "tcg_mid": "mid",
    "tcg_low": "low",
    "tcg_direct_low": "direct_low",
}


def _default_offsets() -> dict:
    return {mk: {"pct": 0.0, "flat": 0.0} for mk in MARKETPLACES}


DEFAULT_CONFIG = {
    "sources": ["tcg_market", "tcg_mid", "tcg_low", "tcg_direct_low"],
    "tiers": [
        {
            "name": "default", "min": 0, "max": None,
            "modifiers": {
                "condition": {"NM": 100, "LP": 85, "MP": 70, "HP": 50, "DMG": 30},
                "printing": {}, "language": {},
                "age_decay": {"days": 0, "pct": 0},
            },
            "offsets": _default_offsets(),
            "guards": {
                "max_move_pct": None,
                "tier_lock": {"up": False, "down": False},
                "rarity_floors": {}, "cost_floor": True,
            },
            "rounding": "0.01",
        }
    ],
    "set_overrides": {},
    "card_overrides": {},
}


def default_config() -> dict:
    import copy
    return copy.deepcopy(DEFAULT_CONFIG)


def get_config(db: Session, game: str) -> dict:
    """Return the pricing config for a game (games are the config scope now)."""
    row = db.execute(
        select(PricingConfig).where(PricingConfig.game == game)
    ).scalars().first()
    if row and row.config:
        return _upgrade_config(row.config)
    return default_config()


def _upgrade_config(config: dict) -> dict:
    """Fill in any keys a hand-saved / older config might be missing so the
    engine never KeyErrors on a partial document."""
    config.setdefault("sources", list(DEFAULT_CONFIG["sources"]))
    # A pre-redesign config stored sources as {"enabled": [...]} — flatten it.
    if isinstance(config["sources"], dict):
        config["sources"] = config["sources"].get("enabled") or list(DEFAULT_CONFIG["sources"])
    for tier in config.get("tiers", []):
        tier.setdefault("modifiers", {})
        tier["modifiers"].setdefault("condition", {})
        tier["modifiers"].setdefault("printing", {})
        tier["modifiers"].setdefault("language", {})
        tier["modifiers"].setdefault("age_decay", {"days": 0, "pct": 0})
        tier.setdefault("offsets", _default_offsets())
        for mk in MARKETPLACES:
            tier["offsets"].setdefault(mk, {"pct": 0.0, "flat": 0.0})
        g = tier.setdefault("guards", {})
        g.setdefault("max_move_pct", None)
        g.setdefault("tier_lock", {"up": False, "down": False})
        g.setdefault("rarity_floors", {})
        g.setdefault("cost_floor", True)
        tier.setdefault("rounding", "0.01")
    config.setdefault("set_overrides", {})
    config.setdefault("card_overrides", {})
    return config


def _price_rows(db: Session, product_id: int) -> list[PriceData]:
    return db.execute(
        select(PriceData).where(PriceData.tcgplayer_product_id == product_id)
    ).scalars().all()


def _pick_price_row(rows: list[PriceData], printing: str) -> PriceData:
    """Choose the Normal vs Foil price row for a printing (same coarse rule as
    ``base_price``: holo/foil/reverse read the non-Normal row when present)."""
    want_foil = printing not in ("normal", "first_edition")
    matched = [r for r in rows if (r.sub_type.lower() != "normal") == want_foil]
    return matched[0] if matched else rows[0]


def _market_from_rows(rows: list[PriceData], printing: str) -> float | None:
    """Ordered-source fallback over already-loaded price rows for one product."""
    if not rows:
        return None
    row = _pick_price_row(rows, printing)
    for src in DEFAULT_CONFIG["sources"]:
        field = SOURCE_FIELDS.get(src)
        v = getattr(row, field, None) if field else None
        if v is not None and v > 0:
            return round(float(v), 2)
    return None


def card_market_value(db: Session, card, printing: str = "normal") -> float | None:
    """Headline market value for a catalog card, for at-a-glance triage.

    Uses the same ordered source fallback as pricing (market → mid → low →
    direct_low) and is printing-aware, but deliberately ignores condition, age
    and the pricing tiers/guards — it's the raw sticker market number a seller
    scans a scanned batch against, not the computed sell price. Returns None
    when the card has no TCGplayer id or no price data.
    """
    if card is None or not getattr(card, "tcgplayer_product_id", None):
        return None
    return _market_from_rows(_price_rows(db, card.tcgplayer_product_id), printing)


def market_values_for_items(db: Session, items) -> dict[int, float | None]:
    """card_market_value() for a whole result set, keyed by inventory id.

    Batches the price lookup (one query per 500 products instead of one per
    item) so a valuation over every filtered row stays cheap — price_data is
    the biggest table in the DB. Items without a catalog card or price row map
    to None, same as the single-item call.
    """
    product_ids = sorted({
        it.card.tcgplayer_product_id for it in items
        if it.card and it.card.tcgplayer_product_id
    })
    rows_by_product: dict[int, list[PriceData]] = {}
    for i in range(0, len(product_ids), 500):  # chunked: SQLite caps bind params
        chunk = product_ids[i:i + 500]
        for row in db.execute(select(PriceData).where(
                PriceData.tcgplayer_product_id.in_(chunk))).scalars():
            rows_by_product.setdefault(row.tcgplayer_product_id, []).append(row)
    values: dict[int, float | None] = {}
    for it in items:
        product_id = it.card.tcgplayer_product_id if it.card else None
        values[it.id] = (_market_from_rows(rows_by_product.get(product_id, []), it.printing)
                         if product_id else None)
    return values


def base_price(db: Session, item: InventoryItem, config: dict, trace: list) -> float | None:
    """Resolve the baseline market price via the ordered source fallback."""
    if not item.card or not item.card.tcgplayer_product_id:
        return None
    rows = _price_rows(db, item.card.tcgplayer_product_id)
    if not rows:
        return None
    # Coarse foil/normal price-row selection: "normal" and "first_edition" read
    # the Normal row, holo/foil/reverse read the non-Normal (foil) row if present.
    want_foil = item.printing not in ("normal", "first_edition")
    matched = [r for r in rows if (r.sub_type.lower() != "normal") == want_foil]
    row = matched[0] if matched else rows[0]

    for src in config.get("sources", []):
        field = SOURCE_FIELDS.get(src)
        if not field:
            continue
        v = getattr(row, field, None)
        if v is not None and v > 0:
            trace.append(f"base {v:.2f} from {src}")
            return v
    return None


def tiering_price(db: Session, item: InventoryItem, base: float) -> float:
    """The 'current price' a tier band is matched against — the item's current
    price if it has one, otherwise the freshly computed base."""
    if item.price_override is not None:
        return item.price_override
    if item.current_price is not None:
        return item.current_price
    return base


def find_tier(config: dict, price: float) -> dict:
    for tier in config.get("tiers", []):
        lo = tier.get("min") or 0
        hi = tier.get("max")
        if price >= lo and (hi is None or price < hi):
            return tier
    tiers = config.get("tiers", [])
    return tiers[-1] if tiers else DEFAULT_CONFIG["tiers"][0]


def apply_rounding(price: float, rule: str) -> float:
    """Round to the nearest value of the requested shape."""
    if price <= 0:
        return 0.0
    # normalize legacy spellings to the canonical option strings
    rule = {"exact": "0.01", "": "0.01", ".99": "0.99", ".49": "0.49",
            ".25": "0.25", ".10": "0.10", ".05": "0.05"}.get(rule, rule)
    if rule == "0.01":
        return round(price, 2)
    if rule == "1":
        return float(round(price))
    step_rules = {"0.05": 0.05, "0.10": 0.10, "0.25": 0.25}
    if rule in step_rules:
        step = step_rules[rule]
        return round(round(price / step) * step, 2)
    # charm endings: nearest number whose cents match the target (.49/.95/.99)
    charm = {"0.49": 0.49, "0.95": 0.95, "0.99": 0.99}
    if rule in charm:
        cents = charm[rule]
        val = round(price - cents) + cents
        return round(val if val > 0 else cents, 2)
    return round(price, 2)


def _age_factor(db: Session, item: InventoryItem, decay: dict, trace: list) -> float:
    days = decay.get("days")
    pct = decay.get("pct")
    if not days or not pct:
        return 1.0
    age = inv_svc.inventory_age_days(db, item)
    if age is not None and age >= days:
        trace.append(f"age {age}d >= {days}d: x{100 - pct}%")
        return (100 - pct) / 100.0
    return 1.0


def _current_platform_price(item: InventoryItem, marketplace: str) -> float | None:
    for listing in item.listings:
        if listing.marketplace == marketplace and listing.listed_price is not None:
            return listing.listed_price
    return item.current_price


def price_item(db: Session, item: InventoryItem, marketplace: str,
               config: dict | None = None) -> dict:
    """Compute the price for one inventory item on one platform.

    Returns {price, marketplace_price, base, trace, status} where status is
    ok | no_source | suppressed | override. ``price`` is the true computed price;
    ``marketplace_price`` is that raised to the platform's hard minimum.
    """
    game = item.card.game if item.card else None
    if config is None:
        config = get_config(db, game) if game else default_config()
    else:
        config = _upgrade_config(config)
    mp_min = MARKETPLACE_MIN_PRICE.get(marketplace, 0)
    trace: list[str] = []

    # Per-item manual override (fixed price / do-not-reprice) — set from Inventory.
    if item.price_override is not None:
        return {"price": item.price_override,
                "marketplace_price": max(item.price_override, mp_min),
                "base": item.price_override,
                "trace": ["per-item price override"], "status": "override"}

    # Per-card fixed price override
    card_ov = config.get("card_overrides", {}).get(
        str(item.catalog_card_id)) if item.catalog_card_id else None
    if card_ov and card_ov.get("fixed_price") is not None:
        p = float(card_ov["fixed_price"])
        return {"price": p, "marketplace_price": max(p, mp_min), "base": p,
                "trace": ["per-card fixed price"], "status": "override"}

    # Per-set suppression (leave price untouched)
    set_ov = config.get("set_overrides", {}).get(item.card.set_code) if item.card else None
    if set_ov and set_ov.get("suppress"):
        return {"price": item.current_price, "marketplace_price": item.current_price,
                "base": None, "trace": [f"set {item.card.set_code} suppressed"],
                "status": "suppressed"}

    base = base_price(db, item, config, trace)
    if base is None:
        return {"price": None, "marketplace_price": None, "base": None,
                "trace": ["no price source data"], "status": "no_source"}

    current = tiering_price(db, item, base)
    tier = find_tier(config, current)
    trace.append(f"tier '{tier.get('name', '?')}' (current {current:.2f})")
    price = base

    # --- Multiplicative modifier layer -------------------------------------
    mods = tier.get("modifiers", {})
    for layer, key in (("condition", item.condition),
                       ("printing", item.printing),
                       ("language", item.language)):
        pct = mods.get(layer, {}).get(key)
        if pct is not None and pct != 100:
            price *= pct / 100.0
            trace.append(f"{layer} {key}: x{pct}%")
    price *= _age_factor(db, item, mods.get("age_decay", {}), trace)

    if card_ov and card_ov.get("modifier_pct"):
        price *= card_ov["modifier_pct"] / 100.0
        trace.append(f"per-card modifier x{card_ov['modifier_pct']}%")

    # --- Per-platform offset -----------------------------------------------
    off = tier.get("offsets", {}).get(marketplace, {})
    if off.get("pct"):
        price *= (100 + off["pct"]) / 100.0
        trace.append(f"{marketplace} offset {off['pct']:+g}%")
    if off.get("flat"):
        price += off["flat"]
        trace.append(f"{marketplace} offset {off['flat']:+.2f}$")

    # --- Guard layer -------------------------------------------------------
    guards = tier.get("guards", {})

    # Max move vs the platform's current listed price
    cap = guards.get("max_move_pct")
    prev = _current_platform_price(item, marketplace)
    if cap and prev:
        lo, hi = prev * (100 - cap) / 100.0, prev * (100 + cap) / 100.0
        clamped = min(max(price, lo), hi)
        if abs(clamped - price) > 1e-9:
            trace.append(f"max move {cap}%: {price:.2f} -> {clamped:.2f}")
            price = clamped

    # Tier-movement lock: keep price inside the current band on a locked side
    lock = guards.get("tier_lock", {})
    lo_band, hi_band = tier.get("min") or 0, tier.get("max")
    if lock.get("down") and price < lo_band:
        trace.append(f"tier-lock (down): floored to band min {lo_band:.2f}")
        price = lo_band
    if lock.get("up") and hi_band is not None and price > hi_band:
        trace.append(f"tier-lock (up): capped to band max {hi_band:.2f}")
        price = hi_band

    # Floors (authoritative — applied last, before rounding)
    rarity = (item.card.rarity or "").lower() if item.card else ""
    rfloor = guards.get("rarity_floors", {}).get(rarity)
    if rfloor is not None and price < rfloor:
        price = rfloor
        trace.append(f"rarity floor {rfloor:.2f}")
    if guards.get("cost_floor"):
        cost = inv_svc.fifo_unit_cost(db, item)
        if cost is not None and price < cost:
            price = cost
            trace.append(f"cost floor {cost:.2f}")
    if item.price_floor is not None and price < item.price_floor:
        price = item.price_floor
        trace.append(f"per-item floor {item.price_floor:.2f}")

    price = apply_rounding(price, tier.get("rounding", "0.01"))
    trace.append(f"rounded ({tier.get('rounding', '0.01')}) = {price:.2f}")

    marketplace_price = max(price, mp_min)
    if abs(marketplace_price - price) > 1e-9:
        trace.append(f"raised to {marketplace} floor {mp_min:.2f} (listed price only)")

    return {"price": round(price, 2), "marketplace_price": round(marketplace_price, 2),
            "base": base, "trace": trace, "status": "ok"}


def simulate(db: Session, marketplace: str, items: list[InventoryItem],
             large_move_pct: float = 25.0) -> list[dict]:
    """Preview repricing without committing (Section 5 simulation mode)."""
    out = []
    for item in items:
        result = price_item(db, item, marketplace)
        old = _current_platform_price(item, marketplace)
        new = result["marketplace_price"]
        move_pct = round((new - old) / old * 100, 1) if old and new else None
        out.append({
            "inventory_id": item.id,
            "description": inv_svc.item_description(item),
            "old_price": old, "new_price": new,
            "internal_price": result["price"],
            "move_pct": move_pct,
            "large_move": move_pct is not None and abs(move_pct) >= large_move_pct,
            "status": result["status"], "trace": result["trace"],
        })
    return out


def apply_reprice(db: Session, marketplace: str, items: list[InventoryItem]) -> dict:
    """Commit new prices: updates listing target prices and marks them dirty."""
    updated = skipped = 0
    for item in items:
        result = price_item(db, item, marketplace)
        if result["status"] in ("no_source", "suppressed") or result["marketplace_price"] is None:
            skipped += 1
            continue
        item.current_price = result["price"]
        listing = inv_svc.get_or_create_listing(db, item, marketplace)
        if listing.listed_price != result["marketplace_price"]:
            listing.listed_price = result["marketplace_price"]
            listing.dirty = True
        updated += 1
    db.commit()
    return {"updated": updated, "skipped": skipped}
