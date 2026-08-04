"""End-to-end API flow: manual add -> staging -> approve -> reprice -> dry-run
listing -> manual sale -> P&L."""
import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import CatalogCard, PriceData


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        db = SessionLocal()
        if not db.query(CatalogCard).filter_by(external_id="flow-card").first():
            db.add(CatalogCard(
                game="mtg", external_id="flow-card", tcgplayer_product_id=222,
                set_code="FLW", set_name="Flow Set", collector_number="1",
                name="Flow Test Card", rarity="rare", finishes=["normal"],
                languages=["en"], image_url="http://example.com/img.jpg"))
            db.add(PriceData(tcgplayer_product_id=222, sub_type="Normal",
                             market=20.0, mid=21.0, low=18.0, direct_low=19.0))
            db.commit()
        db.close()
        yield c


def test_full_flow(client):
    # catalog search
    cards = client.get("/api/catalog/search?q=Flow Test").json()
    assert cards, "seeded card should be searchable"
    card_id = cards[0]["id"]

    # manual add -> staging
    r = client.post("/api/staging/manual-add", json={
        "catalog_card_id": card_id, "condition": "NM", "bin": "FLOW-1",
        "quantity": 4, "cost": 5.0})
    assert r.status_code == 200 and not r.json()["direct"]

    # approve staging -> live inventory
    staged = client.get("/api/staging").json()
    ids = [s["id"] for s in staged if s["card"] and s["card"]["id"] == card_id]
    r = client.post("/api/staging/approve", json={"ids": ids})
    assert r.json()["approved"] == len(ids)

    inv = client.post("/api/inventory/search", json={"q": "Flow Test", "with_age": True}).json()
    assert inv["total"] >= 1
    item = inv["items"][0]
    assert item["quantity"] >= 4
    assert item["bin"] == "FLOW-1"
    inv_id = item["id"]

    # reprice (eBay default config: market price)
    r = client.post("/api/pricing/apply/ebay", json={"filter": {"q": "Flow Test"}})
    assert r.json()["updated"] >= 1
    item = client.get(f"/api/inventory/{inv_id}").json()
    assert item["current_price"] == 20.0

    # connect eBay in dry-run, add a matching rule, push
    client.put("/api/marketplaces/accounts/ebay", json={
        "credentials": {"dry_run": True}, "status": "connected"})
    client.post("/api/marketplaces/rules", json={
        "marketplace": "ebay", "name": "all", "priority": 0, "filters": {}})
    r = client.post("/api/marketplaces/ebay/push-remaining",
                    json={"filter": {"q": "Flow Test"}})
    assert r.json()["created"] >= 1
    item = client.get(f"/api/inventory/{inv_id}").json()
    ebay = [l for l in item["listings"] if l["marketplace"] == "ebay"][0]
    assert ebay["status"] == "listed" and ebay["ebay_listing_id"]

    # manual sale of 1 unit -> FIFO COGS -> mark shipped -> P&L
    r = client.post("/api/orders/manual", json={
        "platform": "tester",
        "items": [{"inventory_id": inv_id, "quantity": 1, "unit_price": 22.0,
                   "description": "Flow Test Card"}]})
    order = r.json()
    assert order["deduction_applied"]
    assert order["items"][0]["cogs"] == 5.0
    item = client.get(f"/api/inventory/{inv_id}").json()
    assert item["quantity"] == 3

    client.post(f"/api/orders/{order['id']}/mark-shipped", json={})
    pnl = client.get("/api/reports/pnl?group_by=month").json()
    assert any(row["revenue"] >= 22.0 and row["cogs"] >= 5.0 for row in pnl)

    # refund reverses the deduction
    client.post(f"/api/orders/{order['id']}/refund")
    item = client.get(f"/api/inventory/{inv_id}").json()
    assert item["quantity"] == 4

    # audit trail exists for every mutation
    detail = client.get(f"/api/inventory/{inv_id}").json()
    causes = {h["cause"] for h in detail["history"]}
    assert "sale" in causes and "refund" in causes


def test_bulk_pile_api_flow(client):
    """Bulk router is mounted and buy -> FIFO -> sell works over HTTP."""
    pile = client.post("/api/bulk/piles", json={
        "name": "MTG Bulk Commons", "game": "Magic"}).json()
    pid = pile["id"]
    client.post(f"/api/bulk/piles/{pid}/purchase",
                json={"quantity": 500, "unit_cost": 0.05, "acquired_at": "2025-01-01"})
    client.post(f"/api/bulk/piles/{pid}/purchase",
                json={"quantity": 1000, "unit_cost": 0.06, "acquired_at": "2025-02-01"})

    piles = client.get("/api/bulk/piles").json()
    mine = [p for p in piles if p["id"] == pid][0]
    assert mine["on_hand"] == 1500
    assert round(mine["cost_basis"], 2) == 85.0        # 500×.05 + 1000×.06
    assert mine["next_unit_cost"] == 0.05

    r = client.post(f"/api/bulk/piles/{pid}/sell",
                    json={"quantity": 800, "total_price": 40.0, "platform": "Whatnot"})
    assert r.status_code == 200
    order = client.get("/api/orders").json()
    sold = [o for o in order if o["id"] == r.json()["order_id"]][0]
    assert round(sum(li["cogs"] or 0 for li in sold["items"]), 2) == 43.0  # 500×.05 + 300×.06
    assert sold["marketplace"] == "whatnot"  # platform recorded like a regular sale
    assert sold["buyer_name"] == ""          # buyer names are not retained
    assert [p for p in client.get("/api/bulk/piles").json() if p["id"] == pid][0]["on_hand"] == 700

    # delete the (stocked) pile -> hidden from the pile list, sale history kept
    d = client.delete(f"/api/bulk/piles/{pid}")
    assert d.json()["soft_deleted"] is True
    assert all(p["id"] != pid for p in client.get("/api/bulk/piles").json())
    assert any(o["id"] == sold["id"] for o in client.get("/api/orders").json())
