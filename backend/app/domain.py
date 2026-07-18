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

# Default platform seeds for manually-recorded (off-sync) sales. Merged with the
# platforms already used on prior manual orders (see /api/orders/platforms).
SALE_PLATFORMS = ["eBay", "TCGplayer", "Whatnot"]


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
