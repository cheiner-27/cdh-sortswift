"""Recognition-matching tests.

Covers collector-number normalization (the printed/stored number-format drift
between the TCGcsv Pokémon catalog's zero-padded fractions and what OCR
extracts) and the name-first recognition tier added alongside it: OCR'ing the
title band, matching it against the catalog tolerant of the trailing junk
that crop picks up (mana-cost symbols etc.), and using that match (or a set
code) to scope the phash fallback instead of sweeping the whole catalog.
"""
import pytest

from app.models import CatalogCard, collector_number_key, name_key
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


@pytest.mark.parametrize("raw, key", [
    ("Riverchurn Monument", "riverchurnmonument"),
    ("{ Riverchurn Monument Dy", "riverchurnmonumentdy"),  # trailing OCR junk
    ("Dandân", "dandan"),        # accent stripped
    ("  Burnt Offering  ", "burntoffering"),
    ("", None),
    (None, None),
])
def test_name_key(raw, key):
    assert name_key(raw) == key


def _seed_pokemon(db, external_id, set_code, number):
    cat._upsert_card(
        db, game="pokemon", external_id=external_id,
        tcgplayer_product_id=int(external_id),
        set_code=set_code, set_name=set_code, collector_number=number,
        name=f"Card {number}", rarity="Rare", finishes=["normal"],
        languages=["en"], image_url="http://img/x.jpg", is_sealed=False,
    )


def _seed_mtg(db, external_id, set_code, number, name, phash=None):
    return cat._upsert_card(
        db, game="mtg", external_id=external_id,
        set_code=set_code, set_name=set_code, collector_number=number,
        name=name, rarity="rare", finishes=["normal"],
        languages=["en"], image_url="http://img/x.jpg", is_sealed=False,
        phash=phash,
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


def test_lookup_by_name_tolerates_trailing_ocr_junk(db):
    _seed_mtg(db, "1", "DFT", "57", "Riverchurn Monument")
    db.commit()
    hits = scanning.lookup_by_name(db, "mtg", "{ Riverchurn Monument Dy")
    assert [c.name for c in hits] == ["Riverchurn Monument"]


def test_lookup_by_name_prefers_longest_match(db):
    # "Fog" is a prefix of the normalized OCR text, but "Fog Bank" is a
    # longer, more specific prefix of the same text -- that's the real hit.
    _seed_mtg(db, "1", "ICE", "1", "Fog")
    _seed_mtg(db, "2", "ICE", "2", "Fog Bank")
    db.commit()
    hits = scanning.lookup_by_name(db, "mtg", "Fog Bank 1")
    assert [c.name for c in hits] == ["Fog Bank"]


def test_lookup_by_name_short_or_missing_text_returns_nothing(db):
    _seed_mtg(db, "1", "ICE", "1", "Fog")
    db.commit()
    assert scanning.lookup_by_name(db, "mtg", None) == []
    assert scanning.lookup_by_name(db, "mtg", "  Fo ") == []  # below min_len


def test_recognize_image_prefers_numset_over_name(db, monkeypatch):
    card = _seed_mtg(db, "1", "DFT", "57", "Riverchurn Monument")
    db.commit()
    monkeypatch.setattr(scanning, "ocr_extract", lambda db, path, game: {
        "set_code": "DFT", "number": "57", "raw": "", "name_raw": "Riverchurn Monument",
        "language": None, "ok": True,
    })
    rec = scanning.recognize_image(db, "unused.png", "mtg")
    assert rec["method"] == "ocr"
    assert rec["candidates"][0]["card_id"] == card.id
    assert rec["confidence"] == 0.95


def test_recognize_image_falls_back_to_unique_name_match(db, monkeypatch):
    card = _seed_mtg(db, "1", "DFT", "57", "Riverchurn Monument")
    db.commit()
    monkeypatch.setattr(scanning, "ocr_extract", lambda db, path, game: {
        "set_code": None, "number": None, "raw": "", "name_raw": "Riverchurn Monument",
        "language": None, "ok": False,
    })
    rec = scanning.recognize_image(db, "unused.png", "mtg")
    assert rec["method"] == "ocr_name"
    assert rec["candidates"][0]["card_id"] == card.id


def test_recognize_image_scopes_phash_to_name_matches(db, monkeypatch):
    # Two printings share a name (a reprint) -- name OCR alone can't pick one,
    # but the phash comparison should only ever see these two, not the whole
    # catalog.
    forest_ice = _seed_mtg(db, "1", "ICE", "1", "Forest", phash="0" * 16)
    forest_4ed = _seed_mtg(db, "2", "4ED", "2", "Forest", phash="f" * 16)
    _seed_mtg(db, "3", "ICE", "3", "Unrelated Card", phash="0" * 16)
    db.commit()
    expected_ids = {forest_ice.id, forest_4ed.id}
    monkeypatch.setattr(scanning, "ocr_extract", lambda db, path, game: {
        "set_code": None, "number": None, "raw": "", "name_raw": "Forest",
        "language": None, "ok": False,
    })
    seen_ids = {}

    def fake_phash_candidates(db, image_path, game, max_distance, top_n=5,
                              card_ids=None, set_code=None):
        seen_ids["card_ids"] = card_ids
        from sqlalchemy import select as _select
        pool = [c for c in db.execute(_select(CatalogCard)).scalars()
                if card_ids is None or c.id in card_ids]
        return [(pool[0], 0)] if pool else []

    monkeypatch.setattr(scanning, "phash_candidates", fake_phash_candidates)
    rec = scanning.recognize_image(db, "unused.png", "mtg")
    assert rec["method"] == "phash_name"
    assert seen_ids["card_ids"] == expected_ids  # the two "Forest" ids, not "Unrelated Card"


def test_recognize_image_retries_unscoped_when_scoped_phash_is_empty(db, monkeypatch):
    # Two "Forest" reprints (ambiguous by name -- doesn't uniquely resolve at
    # the name tier) whose phashes haven't been built yet, plus an unrelated
    # card that has one. The scoped search (restricted to the two Forests)
    # must come back empty and retry unscoped rather than reporting no match.
    _seed_mtg(db, "1", "ICE", "1", "Forest", phash=None)
    _seed_mtg(db, "2", "4ED", "2", "Forest", phash=None)
    other = _seed_mtg(db, "3", "ICE", "3", "Unrelated Card", phash="0" * 16)
    db.commit()
    monkeypatch.setattr(scanning, "ocr_extract", lambda db, path, game: {
        "set_code": None, "number": None, "raw": "", "name_raw": "Forest",
        "language": None, "ok": False,
    })
    calls = []

    def fake_phash_candidates(db, image_path, game, max_distance, top_n=5,
                              card_ids=None, set_code=None):
        calls.append(card_ids)
        if card_ids is not None:
            return []  # neither Forest reprint has a phash built yet
        return [(other, 0)]

    monkeypatch.setattr(scanning, "phash_candidates", fake_phash_candidates)
    rec = scanning.recognize_image(db, "unused.png", "mtg")
    assert rec["method"] == "phash"  # fell back to the unscoped sweep
    assert rec["candidates"][0]["card_id"] == other.id
    assert len(calls) == 2  # scoped attempt, then the unscoped retry
    assert calls[0] is not None and calls[1] is None
