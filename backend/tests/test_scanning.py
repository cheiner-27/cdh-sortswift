"""Recognition-matching tests, focused on collector-number normalization.

These guard the OCR lookup path against the printed/stored number-format drift
that comes with the TCGcsv Pokémon catalog (fractions with leading zeros like
"029/086") vs. what OCR extracts (the bare numerator "29").
"""
import pytest

from app.models import collector_number_key
from app.services import catalog as cat
from app.services import scanning


@pytest.mark.parametrize("raw, key", [
    ("4/102", "4"),      # classic Pokémon fraction
    ("004/102", "4"),    # zero-padded (TCGcsv form)
    ("029/086", "29"),
    ("4", "4"),          # bare numerator (vintage / promos)
    ("0123", "123"),     # zero-padded MTG
    ("166/165", "166"),  # secret rare numbered above the set total
    ("OP01-004", "OP01-004"),  # non-numeric code passes through, upper-cased
    ("", None),
    (None, None),
])
def test_collector_number_key(raw, key):
    assert collector_number_key(raw) == key


def _seed_pokemon(db, external_id, set_code, number):
    cat._upsert_card(
        db, game="pokemon", external_id=external_id,
        tcgplayer_product_id=int(external_id),
        set_code=set_code, set_name=set_code, collector_number=number,
        name=f"Card {number}", rarity="Rare", finishes=["normal"],
        languages=["en"], image_url="http://img/x.jpg", is_sealed=False,
    )


def test_lookup_by_ocr_matches_numerator_across_number_formats(db):
    # Stored as printed with leading zeros; OCR only ever yields the numerator.
    _seed_pokemon(db, "1", "SVI", "004/198")
    _seed_pokemon(db, "2", "OBF", "004/197")
    db.commit()

    # No set code read -> both #4 printings are candidates (numerator match).
    hits = scanning.lookup_by_ocr(db, "pokemon", None, "4")
    assert {c.external_id for c in hits} == {"1", "2"}

    # Set code read -> narrows to the exact set.
    hits = scanning.lookup_by_ocr(db, "pokemon", "OBF", "4")
    assert [c.external_id for c in hits] == ["2"]


def test_lookup_by_ocr_no_false_positive_on_prefix(db):
    _seed_pokemon(db, "40", "SVI", "040/198")  # numerator 40, not 4
    db.commit()
    assert scanning.lookup_by_ocr(db, "pokemon", None, "4") == []
