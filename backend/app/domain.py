"""Canonical enums and normalization maps."""

GAMES = ["mtg", "pokemon", "onepiece", "yugioh"]

CONDITIONS = ["NM", "LP", "MP", "HP", "DMG"]

CONDITION_LABELS = {
    "NM": "Near Mint", "LP": "Lightly Played", "MP": "Moderately Played",
    "HP": "Heavily Played", "DMG": "Damaged",
}

# TCGplayer-standard condition names <-> internal codes
TCGPLAYER_CONDITIONS = {
    "Near Mint": "NM", "Lightly Played": "LP", "Moderately Played": "MP",
    "Heavily Played": "HP", "Damaged": "DMG",
}

# Canonical internal printing types.
#
# Printing exists ONLY to disambiguate two otherwise-identical cards (same name,
# set and collector number) that differ solely in how they were printed. Things
# like full-art, alternate-art, textured or serialized treatments are separate
# catalog entries with their own collector numbers, so they are NOT printings.
# Language is tracked on its own field, not here.
CANONICAL_PRINTINGS = [
    "normal", "holo", "foil", "first_edition", "reverse_holo",
]

PRINTING_SYNONYMS = {
    "": "normal",
    "non-foil": "normal", "nonfoil": "normal", "regular": "normal", "base": "normal",
    "unlimited": "normal",  # unlimited is the plain (non-1st-edition) print
    "foil": "foil", "holofoil": "foil", "etched": "foil", "etched foil": "foil",
    "holo": "holo",  # per-game remap handled below
    "reverse": "reverse_holo", "reverse holo": "reverse_holo", "reverse-holo": "reverse_holo",
    "reverse holofoil": "reverse_holo",
    "1st edition": "first_edition", "1st ed": "first_edition", "first edition": "first_edition",
}

# Game-specific override: "holo" means foil for MTG, holo for Pokémon.
GAME_PRINTING_REMAP = {
    "mtg": {"holo": "foil"},
    "pokemon": {"foil": "holo"},
}

LANGUAGES = ["en", "ja", "de", "fr", "it", "es", "ko", "pt", "ru", "zhs", "zht"]

MARKETPLACES = ["ebay", "tcgplayer"]

# eBay hard listing floor (marketplace-imposed minimum, Section 5 guard layer)
MARKETPLACE_MIN_PRICE = {"ebay": 0.99, "tcgplayer": 0.01}

RARITY_TIERS = [
    "common", "uncommon", "rare", "mythic", "holo_rare", "ultra_rare",
    "secret_rare", "special", "promo",
]

# Custom / non-catalog product categories. Free text was confusing next to the
# physical "Type" field, so Category is now a fixed list that mirrors
# TCGplayer's "Product Line" (plus Supplies / Other for accessories & sealed).
CUSTOM_CATEGORIES = [
    "Magic", "Pokémon", "One Piece", "Yu-Gi-Oh", "Supplies", "Other",
]

# --- Bulk valuation ---------------------------------------------------------
#
# A bulk pile is opaque by design: nobody inventories 12,000 commons card by
# card, so there is no catalog link and therefore no TCGplayer market price —
# which is why piles used to value at $0. Instead a pile is valued the way the
# trade actually prices bulk: a going rate per card for each broad grade, times
# roughly how much of the pile is that grade.
#
# The grades are per game because the breakouts genuinely differ (MTG sells
# basic land as its own category; Pokémon sells basic energy). Rates live in
# Settings (see services/settings.DEFAULTS["bulk_rates"]) because they move with
# the market; the grade list itself is fixed here so the settings screen and the
# per-pile mix always agree on what the columns are.
#
# (key, label, default $/card)
BULK_GRADES = {
    "mtg": [
        ("rare", "Rare / Mythic", 0.030),
        ("common_uncommon", "Common / Uncommon", 0.005),
        ("land", "Basic Land", 0.002),
    ],
    "pokemon": [
        ("ultra_rare", "Ultra Rare (ex / V / GX / Full Art)", 0.250),
        ("holo", "Holo / Reverse Holo", 0.020),
        ("common_uncommon", "Common / Uncommon", 0.005),
        ("energy", "Basic Energy", 0.001),
    ],
    "yugioh": [
        ("secret_ultra", "Secret / Ultra / Super Rare", 0.040),
        ("rare", "Rare", 0.008),
        ("common_uncommon", "Common", 0.003),
    ],
    "onepiece": [
        ("leader_sec", "Leader / SEC / SR", 0.100),
        ("rare", "Rare", 0.015),
        ("common_uncommon", "Common / Uncommon", 0.005),
    ],
}

# Bulk piles record their game in CustomProduct.category, which uses the
# display names in CUSTOM_CATEGORIES; BULK_GRADES is keyed by the internal game
# code. Supplies/Other have no bulk grades — they aren't card piles.
CATEGORY_TO_GAME = {
    "Magic": "mtg", "Pokémon": "pokemon",
    "One Piece": "onepiece", "Yu-Gi-Oh": "yugioh",
}


def bulk_grades_for(category_or_game: str | None) -> list[tuple[str, str, float]]:
    """Grade list for a pile, accepting either a display category or game code."""
    key = CATEGORY_TO_GAME.get(category_or_game or "", category_or_game or "")
    return BULK_GRADES.get(key, [])


def default_bulk_rates() -> dict[str, dict[str, float]]:
    """The seed value for the ``bulk_rates`` setting."""
    return {game: {key: rate for key, _label, rate in grades}
            for game, grades in BULK_GRADES.items()}


# Baseline price sources available to a pricing rule, in the fallback order the
# user can reorder. Each maps to a column on PriceData (see pricing.SOURCE_FIELDS).
PRICE_SOURCES = ["tcg_market", "tcg_mid", "tcg_low", "tcg_direct_low"]

# Rounding targets a pricing tier can round the final price to (nearest).
ROUNDING_OPTIONS = ["0.01", "0.05", "0.10", "0.49", "0.95", "0.99", "1"]

# Default dropdown seeds for the Expenses ledger. These are merged with the
# distinct values already used (services/expenses.suggestions) so the lists grow
# as new categories/retailers are entered via the "Add new" option.
EXPENSE_CATEGORIES = [
    "Supplies", "Postage", "Software", "Equipment", "Fees", "Other",
]
EXPENSE_RETAILERS = ["Amazon", "Airtable", "eBay", "Lovable", "USPS"]

# Capex vs opex. Opex (consumables, postage, software subscriptions, fees) is
# operating overhead expensed in the period it's incurred. Capex is a durable
# asset with a useful life beyond one year (printer, scanner, cutter, shelving).
# Under the IRS de minimis safe harbor a small business still expenses low-cost
# capex (< $2,500/item) in-period, so net profit treats both the same — but the
# class is tracked so capital spend is visible separately from operating cost.
EXPENSE_CLASSES = ["opex", "capex"]
# Categories whose purchases are durable assets (capex) by default. Everything
# else defaults to opex. This is only the default suggestion — the class is
# stored per expense and can be overridden.
CAPEX_CATEGORIES = {"Equipment"}


def default_expense_class(category: str | None) -> str:
    """Suggested capex/opex class for a category (see CAPEX_CATEGORIES)."""
    return "capex" if (category or "").strip() in CAPEX_CATEGORIES else "opex"

# Default platform seeds for manually-recorded (off-sync) sales. Merged with the
# platforms already used on prior manual orders (see /api/orders/platforms).
SALE_PLATFORMS = ["eBay", "TCGplayer", "Whatnot"]

# --- Destination sales tax --------------------------------------------------
#
# Combined state + average-local sales tax rate per state, used for ONE purpose:
# the payment-processing slice of a marketplace fee is charged on the
# tax-inclusive order total, and packing slips don't print the tax.
#
# These are deliberately state-level averages, not jurisdiction lookups. Tax
# reaches the fee only through that percentage term, so the fee's sensitivity to
# rate error is 0.025 x order value: on a $35 order a full percentage point of
# rate error moves the fee by $0.009, i.e. you'd have to be off by ~1.15 points
# to miss by a single cent. Address-level accuracy (a paid tax API) would buy
# fractions of a cent here, at the cost of a key and a network call in the
# middle of the pick workflow.
#
# Note this is never a liability we owe: on these marketplaces the platform is
# the facilitator and collects/remits the tax itself. Rates drift by a few
# tenths a year; edit them here (or override per order in review) when they do.
STATE_TAX_RATES = {
    "AL": 0.0929, "AK": 0.0182, "AZ": 0.0838, "AR": 0.0945, "CA": 0.0885,
    "CO": 0.0781, "CT": 0.0635, "DC": 0.0600, "DE": 0.0000, "FL": 0.0700,
    "GA": 0.0738, "HI": 0.0450, "IA": 0.0694, "ID": 0.0602, "IL": 0.0886,
    "IN": 0.0700, "KS": 0.0866, "KY": 0.0600, "LA": 0.1011, "MA": 0.0625,
    "MD": 0.0600, "ME": 0.0550, "MI": 0.0600, "MN": 0.0804, "MO": 0.0839,
    "MS": 0.0706, "MT": 0.0000, "NC": 0.0700, "ND": 0.0704, "NE": 0.0697,
    "NH": 0.0000, "NJ": 0.0660, "NM": 0.0762, "NV": 0.0824, "NY": 0.0853,
    "OH": 0.0724, "OK": 0.0899, "OR": 0.0000, "PA": 0.0634, "RI": 0.0700,
    "SC": 0.0750, "SD": 0.0611, "TN": 0.0955, "TX": 0.0820, "UT": 0.0725,
    "VA": 0.0577, "VT": 0.0636, "WA": 0.0938, "WI": 0.0570, "WV": 0.0657,
    "WY": 0.0544,
}

# Rate applied when the destination state is unknown or outside the table (e.g.
# a territory or an international address). The median US combined rate keeps a
# missing state from silently understating the fee.
FALLBACK_TAX_RATE = 0.0700


def state_tax_rate(state: str | None) -> float:
    """Estimated combined sales tax rate for a destination state."""
    if not state:
        return FALLBACK_TAX_RATE
    return STATE_TAX_RATES.get(state.strip().upper(), FALLBACK_TAX_RATE)


def normalize_condition(value: str) -> str:
    v = (value or "").strip()
    if v.upper() in CONDITIONS:
        return v.upper()
    if v in TCGPLAYER_CONDITIONS:
        return TCGPLAYER_CONDITIONS[v]
    # try title-cased long names
    for long, code in TCGPLAYER_CONDITIONS.items():
        if v.lower() == long.lower():
            return code
    if v.lower() in ("damaged", "dmg", "d"):
        return "DMG"
    return "NM"


def normalize_printing(value: str, game: str | None = None) -> str:
    v = (value or "").strip().lower()
    canonical = PRINTING_SYNONYMS.get(v, v if v in CANONICAL_PRINTINGS else "normal")
    if game and game in GAME_PRINTING_REMAP:
        canonical = GAME_PRINTING_REMAP[game].get(canonical, canonical)
    return canonical
