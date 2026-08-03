"""Payload validation: quantities are whole numbers, money is a number.

The write endpoints take raw dicts, so these are the checks standing between a
mistyped box and either a 500 or a silently corrupt row. Every case here was a
real failure before app/validate.py existed.
"""
import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import CatalogCard, PriceData
from app.validate import money, whole
from fastapi import HTTPException


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        db = SessionLocal()
        if not db.query(CatalogCard).filter_by(external_id="valid-card").first():
            db.add(CatalogCard(
                game="mtg", external_id="valid-card", tcgplayer_product_id=777,
                set_code="VAL", set_name="Validation Set", collector_number="1",
                name="Validation Card", rarity="rare", finishes=["normal"],
                languages=["en"], image_url="http://example.com/v.jpg"))
            db.add(PriceData(tcgplayer_product_id=777, sub_type="Normal", market=4.0))
            db.commit()
        db.close()
        yield c


@pytest.fixture(scope="module")
def inv_id(client):
    cid = client.get("/api/catalog/search?q=Validation Card").json()[0]["id"]
    r = client.post("/api/staging/manual-add", json={
        "catalog_card_id": cid, "condition": "NM", "bin": "VAL",
        "quantity": 10, "cost": 1.0, "direct": True})
    return r.json()["inventory_id"]


# --- the helpers themselves --------------------------------------------------

@pytest.mark.parametrize("value", ["2", 2, 2.0])
def test_whole_accepts_integral_forms(value):
    assert whole(value, "quantity") == 2


@pytest.mark.parametrize("value", ["abc", None, 2.7, float("nan"),
                                   float("inf"), True, {}, [], "1e999"])
def test_whole_rejects(value):
    with pytest.raises(HTTPException) as e:
        whole(value, "quantity")
    assert e.value.status_code == 400
    assert "quantity" in e.value.detail


def test_whole_bounds():
    with pytest.raises(HTTPException):
        whole(-1, "quantity")                      # default floor is 0
    with pytest.raises(HTTPException):
        whole(10**12, "quantity")                  # past the sanity ceiling
    assert whole(-5, "delta", min_value=None) == -5  # deltas may be negative


def test_whole_default_only_applies_to_empty():
    assert whole(None, "delta", default=0) == 0
    assert whole("", "delta", default=0) == 0
    assert whole(3, "delta", default=0) == 3


@pytest.mark.parametrize("value", ["1.50", 1.5, 2])
def test_money_accepts(value):
    assert money(value, "cost") == float(value)


@pytest.mark.parametrize("value", ["free", None, float("nan"), True, {}])
def test_money_rejects(value):
    with pytest.raises(HTTPException) as e:
        money(value, "cost")
    assert e.value.status_code == 400


def test_money_does_not_round():
    # Bulk piles divide a total by a card count; rounding here would change the
    # cost basis FIFO is built on.
    assert money(0.058333333, "unit_cost") == 0.058333333


# --- inventory adjust --------------------------------------------------------

@pytest.mark.parametrize("adj", [
    {"set_quantity": "abc"},
    {"set_quantity": 2.7},
    {"set_quantity": 10**12},
    {"delta": None},
    {"delta": 1, "unit_cost": -500.0},
    {"delta": 1, "unit_cost": "cheap"},
])
def test_adjust_rejects_bad_numbers(client, inv_id, adj):
    before = client.get(f"/api/inventory/{inv_id}").json()["quantity"]
    r = client.post("/api/inventory/adjust",
                    json={"adjustments": [{"inventory_id": inv_id, **adj}]})
    assert r.status_code == 400, r.json()
    after = client.get(f"/api/inventory/{inv_id}").json()["quantity"]
    assert after == before, "a rejected adjustment must not move stock"


def test_adjust_still_works(client, inv_id):
    before = client.get(f"/api/inventory/{inv_id}").json()["quantity"]
    r = client.post("/api/inventory/adjust", json={
        "adjustments": [{"inventory_id": inv_id, "delta": -2, "unit_cost": 1.25}]})
    assert r.status_code == 200
    assert client.get(f"/api/inventory/{inv_id}").json()["quantity"] == before - 2


# --- inventory PATCH ---------------------------------------------------------

@pytest.mark.parametrize("payload", [
    {"condition": "BANANA"},
    {"printing": "sparkly"},
    {"language": "klingon"},
    {"price_override": "free"},
    {"price_override": -99.0},
])
def test_patch_rejects(client, inv_id, payload):
    r = client.patch(f"/api/inventory/{inv_id}", json=payload)
    assert r.status_code == 400, r.json()


def test_patch_accepts_valid_and_clears_price(client, inv_id):
    assert client.patch(f"/api/inventory/{inv_id}",
                        json={"condition": "LP", "price_override": 12.5}).status_code == 200
    d = client.get(f"/api/inventory/{inv_id}").json()
    assert d["condition"] == "LP" and d["price_override"] == 12.5
    # null must still clear the override — that's how the UI empties the box.
    assert client.patch(f"/api/inventory/{inv_id}",
                        json={"price_override": None}).status_code == 200
    assert client.get(f"/api/inventory/{inv_id}").json()["price_override"] is None


# --- bulk edit ---------------------------------------------------------------

@pytest.mark.parametrize("changes", [
    {"quantity": {"set": "ten"}},
    {"quantity": {"set": -5}},
    {"quantity": 5},              # not the {"set": n} object shape
    {"price": "abc"},
    {"condition": "MINT-ISH"},
    {"ebay_listing_cap": 1.5},
])
def test_bulk_edit_rejects(client, inv_id, changes):
    before = client.get(f"/api/inventory/{inv_id}").json()["quantity"]
    r = client.post("/api/inventory/bulk-edit",
                    json={"filter": {"ids": [inv_id]}, "set": changes})
    assert r.status_code == 400, r.json()
    assert client.get(f"/api/inventory/{inv_id}").json()["quantity"] == before


def test_bulk_edit_still_works(client, inv_id):
    r = client.post("/api/inventory/bulk-edit", json={
        "filter": {"ids": [inv_id]}, "set": {"price": 3.5, "quantity": {"add": 2}}})
    assert r.status_code == 200 and r.json()["affected"] == 1


# --- staging -----------------------------------------------------------------

@pytest.mark.parametrize("payload", [
    {"quantity": "five"},
    {"quantity": 0},
    {"quantity": -10},
    {"cost": "cheap"},
    {"cost": -3},
    {"condition": "PERFECT"},
])
def test_manual_add_rejects(client, payload):
    cid = client.get("/api/catalog/search?q=Validation Card").json()[0]["id"]
    r = client.post("/api/staging/manual-add", json={"catalog_card_id": cid, **payload})
    assert r.status_code == 400, r.json()


def test_bulk_add_rejects_bad_row(client):
    """bulk-add shares the validator, so a bad row fails the same way."""
    cid = client.get("/api/catalog/search?q=Validation Card").json()[0]["id"]
    r = client.post("/api/staging/bulk-add", json={
        "rows": [{"catalog_card_id": cid, "quantity": 1},
                 {"catalog_card_id": cid, "quantity": "many"}]})
    assert r.status_code == 400


# --- expenses ----------------------------------------------------------------

@pytest.mark.parametrize("payload", [
    {"subtotal": "abc"},
    {"quantity": "many"},
    {"quantity": 0},
    {"subtotal": -5},
    {"expense_class": "someclass"},
])
def test_expense_rejects(client, payload):
    r = client.post("/api/expenses", json={"name": "probe", **payload})
    assert r.status_code == 400, r.json()


def test_expense_still_works(client):
    r = client.post("/api/expenses", json={
        "name": "Sleeves", "category": "Supplies", "quantity": 2,
        "subtotal": "12.50", "date": "2026-08-01"})
    assert r.status_code == 200
    assert r.json()["subtotal"] == 12.5  # numeric string coerced, not rejected


# --- bulk piles --------------------------------------------------------------

def test_bulk_pile_purchase_validates(client):
    pile = client.post("/api/bulk/piles", json={"name": "Validation Bulk"}).json()
    pid = pile["id"]
    for bad in ({"quantity": 0}, {"quantity": "lots"},
                {"quantity": 100, "unit_cost": "cheap"},
                {"quantity": 100, "total_cost": -5}):
        assert client.post(f"/api/bulk/piles/{pid}/purchase", json=bad).status_code == 400
    r = client.post(f"/api/bulk/piles/{pid}/purchase",
                    json={"quantity": 500, "total_cost": 25.0})
    assert r.status_code == 200 and r.json()["on_hand"] == 500
    # 25/500 = 0.05 exactly; the divide must not be rounded away
    assert r.json()["avg_unit_cost"] == 0.05


# --- orders ------------------------------------------------------------------

def test_manual_order_validates_line_items(client, inv_id):
    r = client.post("/api/orders/manual", json={
        "buyer_name": "probe",
        "items": [{"inventory_id": inv_id, "quantity": "two", "unit_price": 5.0}]})
    assert r.status_code == 400
    r = client.post("/api/orders/manual", json={
        "buyer_name": "probe", "shipping_cost": "free",
        "items": [{"inventory_id": inv_id, "quantity": 1, "unit_price": 5.0}]})
    assert r.status_code == 400


# --- scan queue --------------------------------------------------------------

def test_scan_queue_edit_validates(client):
    """A confirmed queue row is copied into staging verbatim, so it's checked
    at the edit rather than one step later inside a FIFO batch."""
    from app.db import SessionLocal
    from app.models import ScanPull, ScanQueueItem
    db = SessionLocal()
    pull = ScanPull(folder="probe", image_count=1)
    db.add(pull)
    db.flush()
    row = ScanQueueItem(pull_id=pull.id, image_path="x.jpg", quantity=1)
    db.add(row)
    db.commit()
    row_id = row.id
    db.close()

    for bad in ({"quantity": "many"}, {"quantity": 0}, {"quantity": 1.5},
                {"cost": "cheap"}, {"cost": -1}, {"condition": "PRISTINE"}):
        assert client.patch(f"/api/scans/queue/{row_id}", json=bad).status_code == 400, bad
    assert client.patch(f"/api/scans/queue/{row_id}",
                        json={"quantity": 3, "cost": 0.25,
                              "condition": "LP"}).status_code == 200


# --- cycle counts ------------------------------------------------------------

def test_cycle_count_line_validates(client, inv_id):
    count_id = client.post("/api/inventory/cycle-counts",
                           json={"bin": "VAL"}).json()["count_id"]
    lines = client.get(f"/api/inventory/cycle-counts/{count_id}").json()["lines"]
    line_id = lines[0]["id"]
    assert client.patch(f"/api/inventory/cycle-counts/lines/{line_id}",
                        json={"counted": "abc"}).status_code == 400
    assert client.patch(f"/api/inventory/cycle-counts/lines/{line_id}",
                        json={"counted": 3}).status_code == 200
    # null is a real value here — it resets the row to uncounted.
    assert client.patch(f"/api/inventory/cycle-counts/lines/{line_id}",
                        json={"counted": None}).status_code == 200
