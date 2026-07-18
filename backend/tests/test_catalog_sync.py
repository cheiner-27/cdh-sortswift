"""Catalog full-sync mapping + card-detail endpoint regression tests.

These cover the code paths that back the "Sync entire catalog" button and the
catalog card-detail popup without hitting any live API. The Scryfall bulk sync
is exercised by feeding the ijson streaming parser a small synthetic file that
mimics the shape of Scryfall's ``default_cards`` feed.
"""
import json

import ijson
import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import CatalogCard, PriceData
from app.services import catalog as cat


def test_upsert_mtg_card_double_faced_and_normal(db):
    normal = {
        "id": "u-normal", "set": "mh3", "set_name": "Modern Horizons 3",
        "collector_number": "42", "name": "Test Bolt", "rarity": "rare",
        "finishes": ["nonfoil", "foil"], "lang": "en",
        "image_uris": {"normal": "http://img/normal.jpg"},
        "tcgplayer_id": 999, "released_at": "2024-06-14",
    }
    dfc = {
        "id": "u-dfc", "set": "neo", "set_name": "Kamigawa",
        "collector_number": "100", "name": "Front // Back", "rarity": "mythic",
        "finishes": ["nonfoil"], "lang": "en",
        "card_faces": [
            {"image_uris": {"normal": "http://img/front.jpg"}},
            {"image_uris": {"normal": "http://img/back.jpg"}},
        ],
    }
    cat._upsert_mtg_card(db, normal)
    cat._upsert_mtg_card(db, dfc)
    db.commit()

    n = db.query(CatalogCard).filter_by(external_id="u-normal").one()
    assert n.set_code == "MH3"  # upper-cased
    assert n.image_url == "http://img/normal.jpg"
    assert n.tcgplayer_product_id == 999
    assert n.finishes == ["nonfoil", "foil"]
    assert n.is_double_faced is False

    d = db.query(CatalogCard).filter_by(external_id="u-dfc").one()
    assert d.is_double_faced is True
    assert d.image_url == "http://img/front.jpg"
    assert d.back_image_url == "http://img/back.jpg"


def test_bulk_stream_parse_and_upsert(db, tmp_path):
    """Feed a synthetic bulk array through ijson exactly like sync_mtg_all_cards,
    including the digital-skip filter and per-set upsert."""
    bulk = [
        {"id": "a", "set": "abc", "set_name": "Set ABC", "collector_number": "1",
         "name": "Card A", "finishes": ["nonfoil"], "lang": "en",
         "image_uris": {"normal": "http://img/a.jpg"}, "released_at": "2023-01-01"},
        {"id": "b", "set": "abc", "set_name": "Set ABC", "collector_number": "2",
         "name": "Card B", "finishes": ["nonfoil"], "lang": "en",
         "image_uris": {"normal": "http://img/b.jpg"}},
        {"id": "c", "set": "xyz", "set_name": "Set XYZ", "collector_number": "5",
         "name": "Digital Only", "digital": True, "finishes": ["nonfoil"]},
    ]
    path = tmp_path / "bulk.json"
    path.write_text(json.dumps(bulk), encoding="utf-8")

    n = 0
    seen = set()
    with open(path, "rb") as f:
        for c in ijson.items(f, "item"):
            if c.get("digital"):
                continue
            code = str(c.get("set", "")).upper()
            if code and code not in seen:
                cat._upsert_set(db, "mtg", code, c.get("set_name") or code,
                                c.get("released_at"))
                seen.add(code)
            cat._upsert_mtg_card(db, c)
            n += 1
    db.commit()

    assert n == 2  # digital card skipped
    assert db.query(CatalogCard).filter_by(game="mtg").count() == 2
    assert not db.query(CatalogCard).filter_by(external_id="c").first()
    assert seen == {"ABC"}  # only the one non-digital set upserted


def test_upsert_pokemon_product_from_tcgcsv(db):
    """TCGcsv product -> CatalogCard: printed number kept, numerator key set,
    TCGplayer id carried, name de-duplicated, image bumped to hi-res."""
    single = {
        "productId": 693445, "name": "Ampharos - 029/086", "cleanName": "Ampharos 029 086",
        "imageUrl": "https://tcgplayer-cdn.tcgplayer.com/product/693445_200w.jpg",
        "extendedData": [
            {"name": "Number", "value": "029/086"},
            {"name": "Rarity", "value": "Rare"},
        ],
    }
    added = cat._upsert_pokemon_product(db, "CRI", "Chaos Rising", "2026-01-01", single)
    db.commit()
    assert added is True
    row = db.query(CatalogCard).filter_by(external_id="693445").one()
    assert row.game == "pokemon"
    assert row.set_code == "CRI"
    assert row.tcgplayer_product_id == 693445  # fixes price join
    assert row.collector_number == "029/086"   # printed form preserved
    assert row.collector_number_norm == "29"   # matching key (zero-stripped numerator)
    assert row.name == "Ampharos"              # " - 029/086" disambiguator dropped
    assert row.rarity == "Rare"
    assert row.image_url.endswith("693445_in_1000x1000.jpg")


def test_upsert_pokemon_product_skips_sealed(db):
    """Products with no printed Number (or a sealed keyword) are not cards."""
    box = {
        "productId": 684444, "name": "Chaos Rising Booster Box",
        "imageUrl": "https://tcgplayer-cdn.tcgplayer.com/product/684444_200w.jpg",
        "extendedData": [{"name": "UPC", "value": "1234"}],
    }
    added = cat._upsert_pokemon_product(db, "CRI", "Chaos Rising", None, box)
    db.commit()
    assert added is False
    assert db.query(CatalogCard).filter_by(external_id="684444").first() is None


# --- card-detail endpoint (backs the catalog "Details" button) --------------

@pytest.fixture(scope="module")
def api_client():
    with TestClient(app) as c:
        s = SessionLocal()
        if not s.query(CatalogCard).filter_by(external_id="detail-card").first():
            s.add(CatalogCard(
                game="mtg", external_id="detail-card", tcgplayer_product_id=333,
                set_code="DET", set_name="Detail Set", collector_number="7",
                name="Detail Test Card", rarity="rare", finishes=["normal"],
                languages=["en"], image_url="http://example.com/det.jpg"))
            s.add(PriceData(tcgplayer_product_id=333, sub_type="Normal",
                            market=5.0, mid=6.0, low=4.0, direct_low=4.5))
            s.add(PriceData(tcgplayer_product_id=333, sub_type="Foil",
                            market=15.0, mid=16.0, low=12.0, direct_low=None))
            s.commit()
        s.close()
        yield c


def test_card_detail_returns_card_and_prices(api_client):
    cards = api_client.get("/api/catalog/search?q=Detail Test").json()
    assert cards
    cid = cards[0]["id"]
    r = api_client.get(f"/api/catalog/card/{cid}")
    assert r.status_code == 200
    body = r.json()
    assert body["card"]["name"] == "Detail Test Card"
    subtypes = {p["sub_type"] for p in body["prices"]}
    assert subtypes == {"Normal", "Foil"}


def test_card_detail_404(api_client):
    assert api_client.get("/api/catalog/card/99999999").status_code == 404
