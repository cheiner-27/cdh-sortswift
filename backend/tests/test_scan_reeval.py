"""The manual cloud re-identify endpoint updates the selected queue items with
the vision model's mapped result (the model call itself is stubbed)."""
import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import CatalogCard, ScanPull, ScanQueueItem
from app.services import cloud_recognition


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_reeval_updates_selected_items(client, monkeypatch):
    db = SessionLocal()
    card = CatalogCard(
        game="mtg", external_id="reeval-1", set_code="RVL", set_name="Reeval Set",
        collector_number="7", name="Reeval Card", rarity="rare", finishes=["normal"],
        languages=["en"], image_url="http://example.com/reeval.jpg")
    db.add(card)
    pull = ScanPull(folder="/tmp/scans")
    db.add(pull)
    db.flush()
    item = ScanQueueItem(
        pull_id=pull.id, seq=1, image_path="/tmp/scans/scan.png", file_name="scan.png",
        status="needs_review", confidence=0.2, method="img_unscoped", candidates=[])
    db.add(item)
    db.commit()
    item_id, card_id = item.id, card.id
    db.close()

    def fake_reidentify(db, image_path, game="mtg", model=None):
        return {"candidates": [{
            "card_id": card_id, "name": "Reeval Card", "set_code": "RVL",
            "set_name": "Reeval Set", "collector_number": "7", "rarity": "rare",
            "image_url": "http://example.com/reeval.jpg", "score": 0.95,
            "method": "cloud_setnum"}],
            "method": "cloud_setnum", "confidence": 0.95, "language": "en"}

    monkeypatch.setattr(cloud_recognition, "reidentify", fake_reidentify)

    r = client.post("/api/scans/queue/reeval", json={"ids": [item_id]})
    assert r.status_code == 200
    body = r.json()
    assert body["errors"] == []
    assert len(body["updated"]) == 1
    upd = body["updated"][0]
    assert upd["method"] == "cloud_setnum"
    assert upd["confidence"] == 0.95
    assert upd["card_id"] == card_id
    assert upd["card"]["id"] == card_id
    assert upd["status"] == "pending"  # 0.95 >= default confidence_threshold (0.75)


def test_reeval_requires_ids(client):
    assert client.post("/api/scans/queue/reeval", json={"ids": []}).status_code == 400
