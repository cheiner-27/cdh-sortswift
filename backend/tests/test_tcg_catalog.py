"""TCGCSV reference cache and optional collector-number matching."""
from contextlib import contextmanager
from datetime import timedelta

import httpx
import pytest

from app.models import TcgCatalogGroup, utcnow
from app.services import tcg_catalog as catalog

LINE = {"game_label": "Magic", "set_name": "Vintage Set", "card_name": "Card",
        "collector_number": "", "sealed": False}


def fake_http(monkeypatch, payloads):
    calls = []
    class Client:
        def get(self, url):
            calls.append(url)
            payload = payloads[url.rsplit("/", 1)[-1]]
            if isinstance(payload, Exception):
                raise payload
            return httpx.Response(200, json={"results": payload}, request=httpx.Request("GET", url))
    @contextmanager
    def client(**kwargs):
        yield Client()
    monkeypatch.setattr(catalog, "client", client)
    return calls


def test_many_rows_fetch_each_set_once_then_reuse_cache(db, monkeypatch):
    calls = fake_http(monkeypatch, {
        "groups": [{"groupId": 7, "name": "Vintage Set", "abbreviation": "VNT"}],
        "products": [{"productId": 123, "name": "Card", "extendedData": []}]})
    first = catalog.ensure_for_lines(db, [LINE] * 1000)
    second = catalog.ensure_for_lines(db, [LINE] * 1000)
    assert catalog.ProductIndex(db, [LINE]).set_codes(LINE, 123) == {"vnt"}
    assert len(calls) == 2
    assert first["sets_loaded"] == 1 and second["sets_cached"] == 1
    assert catalog.ProductIndex(db, [LINE]).lookup(LINE) == [123]


def test_stale_products_refresh_and_failed_refresh_preserves_cache(db, monkeypatch):
    group = TcgCatalogGroup(game="mtg", group_id=7, name="Vintage Set",
                            name_norm="vintageset", groups_updated_at=utcnow(),
                            products_updated_at=utcnow() - timedelta(days=2),
                            products=[{"product_id": 123, "name": "Card", "number": ""}])
    db.add(group)
    db.commit()
    calls = fake_http(monkeypatch, {"products": httpx.ConnectError("offline")})
    result = catalog.ensure_for_lines(db, [LINE])
    assert len(calls) == 1 and result["warnings"]
    assert catalog.ProductIndex(db, [LINE]).lookup(LINE) == [123]
    fake_http(monkeypatch, {"products": [{"productId": 456, "name": "Card", "extendedData": []}]})
    assert catalog.ensure_for_lines(db, [LINE])["sets_loaded"] == 1
    assert catalog.ProductIndex(db, [LINE]).lookup(LINE) == [456]


def test_invalid_group_response_does_not_cache_partial_set_list(db, monkeypatch):
    fake_http(monkeypatch, {"groups": [{"groupId": 7, "name": "Vintage Set"}, {"bad": "row"}]})
    result = catalog.ensure_for_lines(db, [LINE])
    assert result["warnings"]
    assert db.query(TcgCatalogGroup).count() == 0


@pytest.mark.parametrize("number,expected", [("", [1, 2]), ("003 // 022", [1]), ("3 // 21", [2]), ("3 // 99", [])])
def test_double_sided_numbers_remain_distinct(db, number, expected):
    db.add(TcgCatalogGroup(game="mtg", group_id=7, name="Vintage Set",
                           name_norm="vintageset", products=[
                               {"product_id": 1, "name": "Card", "number": "3 // 22"},
                               {"product_id": 2, "name": "Card", "number": "3 // 21"}]))
    db.commit()
    assert catalog.ProductIndex(db, [LINE]).lookup({**LINE, "collector_number": number}) == expected
