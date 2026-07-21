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


# --- MTG collector-layout parser (the two-line modern layout + bullet noise) ---

@pytest.mark.parametrize("text, set_code, number", [
    # 2024+ layout: "R 0012" then "SET <bullet> EN ARTIST"; bullet OCRs as '*'
    ("R 0012\nTLA * EN AINEZU", "TLA", "12"),
    # 2015-2023 layout: fraction + rarity, then set line; bullet as '¢'
    ("015/281 M\nSNC ¢ EN ANASTASIA", "SNC", "15"),
    ("234/342 R\nC15 ° EN KARLA ORTIZ", "C15", "234"),
    ("492 U\nCLB*EN JOSH HASS", "CLB", "492"),        # no spaces around bullet
    ("R 0198\nMOM*:+EN DAARKEN", "MOM", "198"),        # multi-char bullet run
    ("268 R\nSLD © EN DOMINIK MAYER", "SLD", "268"),   # copyright glyph as bullet
])
def test_mtg_parse_bottom_reads_modern_layouts(text, set_code, number):
    p = scanning._mtg_parse_bottom(text)
    assert p["set_code"] == set_code
    assert p["number"] == number
    assert p["language"] == "EN"


def test_mtg_parse_bottom_vintage_has_no_setcode_or_number():
    # Pre-2003 cards print only a copyright year -- no set line, so the number
    # must stay None (a bare year must never be read as a collector number).
    p = scanning._mtg_parse_bottom(
        "Illus. Sandra Everingham\n(c) 1995 Wizards of the Coast, Inc.")
    assert p["set_code"] is None
    assert p["number"] is None
    assert p["year"] == 1995


# --- recognize_image tiers ---

def _ocr(**kw):
    base = {"set_code": None, "number": None, "raw": "", "name_raw": None,
            "name_candidates": [], "year": None, "language": None, "ok": False}
    base.update(kw)
    if base["name_raw"] and not base["name_candidates"]:
        base["name_candidates"] = [base["name_raw"]]
    return base


def test_recognize_prefers_printed_set_and_number(db, monkeypatch):
    card = _seed_mtg(db, "1", "DFT", "57", "Riverchurn Monument")
    db.commit()
    monkeypatch.setattr(scanning, "ocr_extract", lambda db, p, g: _ocr(
        set_code="DFT", number="57", name_raw="Riverchurn Monument", ok=True))
    rec = scanning.recognize_image(db, "unused.png", "mtg")
    assert rec["method"] == "ocr_setnum"
    assert rec["candidates"][0]["card_id"] == card.id
    assert rec["confidence"] == 0.97


def test_recognize_unique_name_when_no_reference_phash(db, monkeypatch):
    # No set/number, no phash on disk to hash -> a name unique across the
    # catalog resolves on its own.
    card = _seed_mtg(db, "1", "DFT", "57", "Riverchurn Monument")
    db.commit()
    monkeypatch.setattr(scanning, "ocr_extract", lambda db, p, g: _ocr(
        name_raw="Riverchurn Monument"))
    monkeypatch.setattr(scanning, "_card_crop_phash", lambda p: None)
    rec = scanning.recognize_image(db, "unused.png", "mtg")
    assert rec["method"] == "ocr_name"
    assert rec["candidates"][0]["card_id"] == card.id


def test_recognize_image_scopes_to_name_pool(db, monkeypatch):
    # A reprint: two "Forest" printings, plus an unrelated card that happens to
    # share the scan's hash. The scoped image match must pick the same-name
    # printing, never the hash-twin with a different name.
    import imagehash
    match, far = "f" * 16, "0" * 16
    _seed_mtg(db, "1", "ICE", "1", "Forest", phash=far)
    forest_4ed = _seed_mtg(db, "2", "4ED", "2", "Forest", phash=match)
    _seed_mtg(db, "3", "OTH", "9", "Unrelated Card", phash=match)
    db.commit()
    monkeypatch.setattr(scanning, "ocr_extract", lambda db, p, g: _ocr(name_raw="Forest"))
    monkeypatch.setattr(scanning, "_card_crop_phash",
                        lambda p: imagehash.hex_to_hash(match))
    rec = scanning.recognize_image(db, "unused.png", "mtg")
    assert rec["method"] == "img_scoped"
    assert rec["candidates"][0]["card_id"] == forest_4ed.id


def test_recognize_unscoped_wins_when_scoped_match_is_poor(db, monkeypatch):
    # OCR mis-reads the name onto the wrong card whose art is nothing like the
    # scan; the true card lives elsewhere with a near-identical hash. The
    # unscoped match must override the poor scoped (mis-read) one.
    import imagehash
    scan, far = "f0f0f0f0f0f0f0f0", "0f0f0f0f0f0f0f0f"
    _seed_mtg(db, "1", "M13", "1", "Fervent Charge", phash=far)   # mis-read pool
    real = _seed_mtg(db, "2", "WTH", "99", "Fervor", phash=scan)  # true card
    db.commit()
    monkeypatch.setattr(scanning, "ocr_extract", lambda db, p, g: _ocr(name_raw="Fervent Charge"))
    monkeypatch.setattr(scanning, "_card_crop_phash",
                        lambda p: imagehash.hex_to_hash(scan))
    rec = scanning.recognize_image(db, "unused.png", "mtg")
    assert rec["method"] == "img_unscoped"
    assert rec["candidates"][0]["card_id"] == real.id


def test_recognize_year_narrows_same_name_reprints(db, monkeypatch):
    # Two printings of one name; the copyright year picks the era, then the
    # image confirms the exact printing.
    import imagehash
    h = "ffff0000ffff0000"
    old = _seed_mtg(db, "1", "4ED", "1", "Dark Ritual", phash="0" * 16)
    old.release_date = "1995-04-01"
    new = _seed_mtg(db, "2", "TMP", "2", "Dark Ritual", phash=h)
    new.release_date = "1997-10-14"
    db.commit()
    monkeypatch.setattr(scanning, "ocr_extract", lambda db, p, g: _ocr(
        name_raw="Dark Ritual", year=1997))
    monkeypatch.setattr(scanning, "_card_crop_phash", lambda p: imagehash.hex_to_hash(h))
    rec = scanning.recognize_image(db, "unused.png", "mtg")
    assert rec["candidates"][0]["card_id"] == new.id
