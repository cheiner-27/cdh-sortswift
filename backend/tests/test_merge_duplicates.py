"""Duplicate identity normalization must preserve quantities, FIFO and SKU links."""
from datetime import datetime, timezone

import pytest

from app.models import AcquisitionLog, FifoConsumption, InventoryItem, InventoryLog, MarketplaceListing
from app.routers.inventory import merge_duplicates
from app.services import inventory as inv, tcg_counts


def row(db, card, language="en", **changes):
    item = InventoryItem(catalog_card_id=card.id, condition="NM", printing="foil",
                         language=language, bin="", quantity=0, **changes)
    db.add(item)
    db.flush()
    return item


def test_blood_crypt_case_difference_merges_without_changing_fifo(db, card):
    card.name, card.collector_number = "Blood Crypt", "262"
    first = row(db, card, "en", price_override=9.7, current_price=9.9)
    second = row(db, card, "EN", price_override=9.7, current_price=9.7)
    inv.add_stock(db, first, 1, 3.25, acquired_at=datetime(2026, 7, 18, tzinfo=timezone.utc))
    inv.add_stock(db, second, 1, 4.50, acquired_at=datetime(2026, 7, 25, tzinfo=timezone.utc))
    db.commit()
    def batches():
        return [(b.id, b.quantity, b.quantity_remaining, b.unit_cost, b.acquired_at, b.language)
                for b in db.query(AcquisitionLog).order_by(AcquisitionLog.id)]
    before = batches()
    result = merge_duplicates({"filter": {"ids": [first.id, second.id]}}, db)
    assert result == {"merged_rows": 1}
    assert first.quantity == 2 and not first.deleted
    assert second.quantity == 0 and second.deleted
    assert first.price_override == 9.7 and first.current_price == 9.9
    assert batches() == before
    assert inv.fifo_rollup(db, [first])[first.id]["cost_basis"] == 7.75
    assert db.query(FifoConsumption).count() == 0
    logs = db.query(InventoryLog).filter_by(cause="bulk_update").all()
    assert sorted(l.quantity_delta for l in logs) == [-1, 1]
    assert merge_duplicates({"filter": {"include_deleted": True}}, db) == {"merged_rows": 0}


@pytest.mark.parametrize("stored,incoming", [("EN", "en"), ("en", "EN"), (" EN ", " en ")])
def test_intake_reuses_legacy_language_casing_without_rewriting_identity(db, card, stored, incoming):
    item = row(db, card, stored)
    identity = tcg_counts.identity(item)
    found = inv.find_or_create_item(db, catalog_card_id=card.id, printing="foil", language=incoming)
    assert found.id == item.id
    assert tcg_counts.identity(found) == identity
    assert db.query(InventoryItem).count() == 1


def test_new_intake_stores_canonical_language_and_keeps_languages_separate(db, card):
    english = inv.find_or_create_item(db, catalog_card_id=card.id, language=" EN ")
    japanese = inv.find_or_create_item(db, catalog_card_id=card.id, language="JA")
    assert english.language == "en" and japanese.language == "ja"
    assert english.id != japanese.id


@pytest.mark.parametrize("field,value", [("language", "ja"), ("bin", "B"), ("printing", "normal"),
                                         ("condition", "LP"), ("comment", "Different"),
                                         ("price_override", 5), ("price_floor", 2)])
def test_meaningful_differences_still_prevent_merging(db, card, field, value):
    first, second = row(db, card), row(db, card, "EN")
    setattr(second, field, value)
    first.quantity = second.quantity = 1
    db.commit()
    assert merge_duplicates({}, db) == {"merged_rows": 0}
    assert not first.deleted and not second.deleted


@pytest.mark.parametrize("field,value", [("tcg_sku_id", "123"), ("ebay_offer_id", "offer"),
                                         ("listing_cap", 0), ("reserve_quantity", 1)])
def test_different_marketplace_links_or_controls_prevent_merging(db, card, field, value):
    first, second = row(db, card), row(db, card, "EN")
    listing = inv.get_or_create_listing(db, second, "tcgplayer" if field != "ebay_offer_id" else "ebay")
    setattr(listing, field, value)
    db.commit()
    assert merge_duplicates({}, db) == {"merged_rows": 0}


def test_merging_shared_sku_preserves_the_keeper_mapping(db, card):
    first, second = row(db, card, "EN"), row(db, card, "en")
    for item in (first, second):
        item.quantity = 1
        listing = inv.get_or_create_listing(db, item, "tcgplayer")
        listing.tcg_sku_id = "987654"
        listing.tcg_metadata = {"identity": tcg_counts.identity(item), "raw": {"Product Name": "Test Bolt"}}
        listing.listed_price = 6.5
        listing.listed_quantity = 2
    db.commit()
    before = list(first.listings[0].tcg_metadata["identity"])
    assert merge_duplicates({}, db) == {"merged_rows": 1}
    assert first.listings[0].tcg_metadata["identity"] == before == tcg_counts.identity(first)
    assert db.query(MarketplaceListing).filter_by(tcg_sku_id="987654").count() == 1
    # Even an already-loaded retired row must no longer claim the same SKU.
    assert second.listings == []
    line = {"sku": "987654", "sealed": False, "parse_ok": True, "game_label": "Magic",
            "condition": "NM", "printing_canonical": "foil", "set_name": "Test Set",
            "card_name": "Test Bolt", "collector_number": "42", "counted": 2}
    matched = tcg_counts.match_row(db, line)
    assert matched["inventory_id"] == first.id and matched["match_method"] == "sku"


def test_deleted_stock_is_not_added_back_when_show_deleted_is_on(db, card):
    first, second, deleted = row(db, card), row(db, card, "EN"), row(db, card, "en")
    first.quantity = second.quantity = 1
    deleted.quantity, deleted.deleted = 8, True
    db.commit()
    assert merge_duplicates({"filter": {"include_deleted": True}}, db) == {"merged_rows": 1}
    assert first.quantity == 2 and deleted.quantity == 8 and deleted.deleted
