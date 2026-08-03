"""Bulk piles get a market value from their grade mix, not $0.

A pile has no catalog card and so no TCGplayer price. Before this it fell
through market_values_for_items as None and the Inventory page's market total
read $0 for every card sitting in bulk — which for a sift-and-sort workflow is
most of the cards in the app.
"""
import pytest
from fastapi.testclient import TestClient

from app.domain import bulk_grades_for, default_bulk_rates
from app.main import app
from app.services.pricing import bulk_unit_value


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# --- the calculation ---------------------------------------------------------

def test_weighted_average():
    rates = {"mtg": {"rare": 0.03, "common_uncommon": 0.005, "land": 0.002}}
    # 5% x 0.03 + 90% x 0.005 + 5% x 0.002 = 0.0015 + 0.0045 + 0.0001
    v = bulk_unit_value(rates, "Magic", {"rare": 5, "common_uncommon": 90, "land": 5})
    assert v == pytest.approx(0.0061)


def test_unset_mix_is_none_not_zero():
    """None means 'nobody has said yet' — the UI shows 'Set mix', not $0.00."""
    assert bulk_unit_value(default_bulk_rates(), "Magic", {}) is None
    assert bulk_unit_value(default_bulk_rates(), "Magic", None) is None


def test_partial_mix_values_remainder_at_zero():
    rates = {"mtg": {"rare": 0.10, "common_uncommon": 0.005, "land": 0.002}}
    # Only 50% accounted for; the rest is worth nothing, not extrapolated.
    assert bulk_unit_value(rates, "Magic", {"rare": 50}) == pytest.approx(0.05)


def test_non_card_category_has_no_grades():
    assert bulk_grades_for("Supplies") == []
    assert bulk_unit_value(default_bulk_rates(), "Supplies", {"rare": 100}) is None


def test_falls_back_to_default_rate_when_setting_omits_a_grade():
    """A rates blob saved before a grade existed must not zero that grade out."""
    v = bulk_unit_value({"mtg": {"rare": 1.0}}, "Magic", {"rare": 10, "land": 100})
    # rare uses the override (0.10), land falls back to its 0.002 default
    assert v == pytest.approx(0.10 + 0.002)


def test_accepts_game_code_as_well_as_display_category():
    rates = {"pokemon": {"energy": 0.001}}
    assert bulk_unit_value(rates, "pokemon", {"energy": 100}) == pytest.approx(0.001)
    assert bulk_unit_value(rates, "Pokémon", {"energy": 100}) == pytest.approx(0.001)


# --- end to end --------------------------------------------------------------

def test_pile_valuation_end_to_end(client):
    pile = client.post("/api/bulk/piles",
                       json={"name": "Mix Test Pile", "game": "Magic"}).json()
    pid = pile["id"]
    assert pile["unit_value"] is None, "a new pile has no mix yet"
    assert [g["key"] for g in pile["grades"]] == ["rare", "common_uncommon", "land"]

    client.post(f"/api/bulk/piles/{pid}/purchase",
                json={"quantity": 10000, "total_cost": 40.0})
    r = client.patch(f"/api/bulk/piles/{pid}", json={
        "composition": {"rare": 5, "common_uncommon": 90, "land": 5}})
    assert r.status_code == 200
    body = r.json()
    assert body["unit_value"] == pytest.approx(0.0061)
    assert body["market_value"] == pytest.approx(61.0)  # 10,000 x 0.0061

    # ...and it reaches the Inventory page's market total, which is the number
    # that used to read $0.
    inv_id = body["inventory_id"]
    totals = client.post("/api/inventory/search",
                         json={"ids": [inv_id]}).json()["totals"]
    assert totals["market"] == pytest.approx(61.0)
    assert totals["cost"] == pytest.approx(40.0)


def test_rate_change_reprices_every_pile(client):
    """Rates live in Settings, so a market move is one edit, not per pile."""
    pile = client.post("/api/bulk/piles",
                       json={"name": "Rate Test Pile", "game": "Magic"}).json()
    pid = pile["id"]
    client.post(f"/api/bulk/piles/{pid}/purchase", json={"quantity": 1000, "unit_cost": 0.004})
    client.patch(f"/api/bulk/piles/{pid}", json={"composition": {"common_uncommon": 100}})
    before = [p for p in client.get("/api/bulk/piles").json() if p["id"] == pid][0]
    assert before["unit_value"] == pytest.approx(0.005)

    rates = client.get("/api/settings").json()["bulk_rates"]
    rates["mtg"]["common_uncommon"] = 0.010
    client.put("/api/settings", json={"bulk_rates": rates})

    after = [p for p in client.get("/api/bulk/piles").json() if p["id"] == pid][0]
    assert after["unit_value"] == pytest.approx(0.010)
    assert after["market_value"] == pytest.approx(10.0)


@pytest.mark.parametrize("mix,expected_error", [
    ({"rare": 60, "common_uncommon": 60}, "cannot exceed"),
    ({"energy": 50}, "unknown grade"),          # Pokémon grade on an MTG pile
    ({"rare": "lots"}, "must be"),
    ({"rare": 150}, "at most"),
    ({"rare": -5}, "at least"),
])
def test_mix_is_validated(client, mix, expected_error):
    pid = client.post("/api/bulk/piles",
                      json={"name": f"Bad Mix {mix}", "game": "Magic"}).json()["id"]
    r = client.patch(f"/api/bulk/piles/{pid}", json={"composition": mix})
    assert r.status_code == 400
    assert expected_error in r.json()["detail"]


def test_mix_survives_a_purchase(client):
    """Buying more into a pile must not disturb the mix or its per-card value."""
    pid = client.post("/api/bulk/piles",
                      json={"name": "Sticky Mix", "game": "Pokémon"}).json()["id"]
    client.patch(f"/api/bulk/piles/{pid}", json={"composition": {"energy": 100}})
    r = client.post(f"/api/bulk/piles/{pid}/purchase",
                    json={"quantity": 5000, "unit_cost": 0.001})
    assert r.json()["composition"] == {"energy": 100.0}
    assert r.json()["unit_value"] == pytest.approx(0.001)
    assert r.json()["market_value"] == pytest.approx(5.0)
