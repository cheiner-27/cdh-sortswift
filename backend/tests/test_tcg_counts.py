"""CSV cycle-count approval, SKU learning and the two-part TCG round trip."""
import copy
import csv
import io

import pytest
from fastapi import HTTPException

from app.models import AcquisitionLog, InventoryLog, MarketplaceListing, PricingConfig
from app.services import inventory as inv
from app.services import tcg_counts as tcg
from app.services import pricing, exporting


@pytest.fixture(autouse=True)
def cached_catalog_only(monkeypatch):
    # Reconciliation tests exercise matching against local reference data.
    # HTTP refresh/cache behavior is covered separately.
    monkeypatch.setattr(tcg.tcg_catalog, "ensure_for_lines",
                        lambda db, lines: {"sets_loaded": 0, "sets_cached": 0, "warnings": []})


def item_for(db, card, qty=3, printing="normal", bin="A"):
    from app.models import name_key, collector_number_key
    card.name_norm = name_key(card.name)
    card.collector_number_norm = collector_number_key(card.collector_number)
    db.flush()
    item = inv.find_or_create_item(db, catalog_card_id=card.id, printing=printing, bin=bin)
    if qty:
        inv.add_stock(db, item, qty, 1.25)
    db.commit()
    return item


def csv_data(*changes):
    rows = []
    for change in changes or ({},):
        rows.append({
            "TCGplayer Id": "987654", "Product Line": "Magic",
            "Set Name": "TCG Set Spelling", "Product Name": "Test Bolt",
            "Title": "", "Number": "42", "Rarity": "R", "Condition": "Near Mint",
            "TCG Market Price": "10", "TCG Direct Low": "", "TCG Low Price With Shipping": "",
            "TCG Low Price": "8", "Total Quantity": "3", "Add to Quantity": "0",
            "TCG Marketplace Price": "6.50", "Photo URL": "", **change,
        })
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=exporting.TCGPLAYER_HEADERS)
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")


def count_for(db, **changes):
    return tcg.create_count(db, csv_data(changes), "listings.csv")


def test_upload_only_saves_review(db, card):
    item = item_for(db, card)
    item.price_override = 20
    db.commit()
    count = count_for(db, **{"Total Quantity": "1"})
    assert item.quantity == 3 and item.current_price is None and item.price_override == 20
    assert not item.listings
    assert count.source == "tcgplayer" and not tcg.view(db, count)["ready"]
    with pytest.raises(HTTPException):
        tcg.approve(db, count)


def test_accept_local_logs_once_and_consumes_fifo(db, card):
    item = item_for(db, card)
    item.price_override = 20
    db.commit()
    count = count_for(db, **{"Total Quantity": "1"})
    tcg.patch_line(db, count, 1, {"resolution": "accept_tcg"})
    assert tcg.approve(db, count) == {"adjusted": 1, "learned": 1}
    assert item.quantity == 1 and item.current_price == 6.5 and item.price_override == 20
    assert db.query(AcquisitionLog).one().quantity_remaining == 1
    assert db.query(InventoryLog).filter_by(cause="tcg_reconcile").one().quantity_delta == -2
    with pytest.raises(HTTPException):
        tcg.approve(db, count)
    with pytest.raises(HTTPException):
        tcg.patch_line(db, count, 1, {"resolution": "push_local"})


def test_positive_local_adjustment_records_acquisition(db, card):
    item = item_for(db, card, qty=0)
    count = count_for(db, **{"Total Quantity": "2"})
    assert count.source_data["lines"][0]["inventory_id"] == item.id
    tcg.patch_line(db, count, 1, {"resolution": "accept_tcg"})
    tcg.approve(db, count)
    assert item.quantity == 2
    assert db.query(AcquisitionLog).one().quantity_remaining == 2


@pytest.mark.parametrize("observed,delta", [("1", 2), ("5", -2), ("0", 3)])
def test_push_local_exports_signed_delta_with_original_price(db, card, observed, delta):
    item = item_for(db, card)
    count = count_for(db, **{"Total Quantity": observed})
    tcg.patch_line(db, count, 1, {"resolution": "push_local"})
    tcg.approve(db, count)
    headers, rows = tcg.correction_export(db, count)
    row = dict(zip(headers, rows[0]))
    assert row["Add to Quantity"] == delta
    assert row["TCG Marketplace Price"] == "6.50"
    assert item.quantity == 3
    assert db.query(InventoryLog).filter_by(cause="tcg_reconcile").count() == 0
    headers, rows = exporting.build_export(db, [item], layout="tcgplayer")
    assert dict(zip(headers, rows[0]))["Add to Quantity"] == 0


def test_learning_exact_reimport_and_correct_export(db, card):
    item = item_for(db, card, printing="foil")
    count = count_for(db, **{"Condition": "Near Mint Foil"})
    tcg.approve(db, count)
    headers, rows = exporting.build_export(db, [item], layout="tcgplayer")
    out = dict(zip(headers, rows[0]))
    assert out["TCGplayer Id"] == "987654" and out["TCGplayer Id"] != str(card.tcgplayer_product_id)
    assert out["Condition"] == "Near Mint Foil"
    assert out["Set Name"] == "TCG Set Spelling" and out["TCG Market Price"] == 25
    again = count_for(db, **{"Condition": "Near Mint Foil", "Product Name": "TCG renamed this"})
    assert again.source_data["lines"][0]["match_method"] == "sku"
    assert tcg.approve(db, again)["adjusted"] == 0


def test_unknown_sku_never_exports_product_id(db, card):
    item = item_for(db, card)
    headers, rows = exporting.build_export(db, [item], layout="tcgplayer")
    assert dict(zip(headers, rows[0]))["TCGplayer Id"] == ""


def test_changed_identity_invalidates_stored_sku(db, card):
    item = item_for(db, card)
    count = count_for(db)
    tcg.approve(db, count)
    item.condition = "LP"
    db.commit()
    headers, rows = exporting.build_export(db, [item], layout="tcgplayer")
    assert dict(zip(headers, rows[0]))["TCGplayer Id"] == ""
    assert not tcg.view(db, count_for(db))["ready"]


def test_ambiguous_match_requires_pick_and_variance_decision(db, card):
    first = item_for(db, card, qty=1, bin="A")
    item_for(db, card, qty=1, bin="B")
    count = count_for(db)
    assert count.source_data["lines"][0]["match_status"] == "ambiguous"
    tcg.patch_line(db, count, 1, {"inventory_id": first.id})
    assert not tcg.view(db, count)["ready"]
    tcg.patch_line(db, count, 1, {"resolution": "push_local"})
    assert tcg.view(db, count)["ready"]


def test_single_number_collision_does_not_learn_wrong_name(db, card):
    item_for(db, card)
    count = count_for(db, **{"Product Name": "Entirely Different Card"})
    assert count.source_data["lines"][0]["inventory_id"] is None


def test_same_inventory_cannot_be_learned_for_two_skus(db, card):
    item_for(db, card)
    count = tcg.create_count(db, csv_data({}, {"TCGplayer Id": "222222"}), "duplicate.csv")
    assert not tcg.view(db, count)["ready"]
    with pytest.raises(HTTPException):
        tcg.approve(db, count)
    tcg.patch_line(db, count, 2, {"resolution": "skip"})
    assert tcg.view(db, count)["ready"]
    assert tcg.approve(db, count)["learned"] == 1


def test_dead_sealed_missing_rows_are_non_destructive(db, card):
    item = item_for(db, card)
    count = tcg.create_count(db, csv_data(
        {"Product Name": "Unknown", "Number": "999", "Total Quantity": "0"},
        {"TCGplayer Id": "C-123", "Condition": "Unopened", "Total Quantity": "0"}
    ), "dead.csv")
    view = tcg.view(db, count)
    assert {l["match_status"] for l in view["lines"]} == {"dead", "sealed"}
    assert view["unlisted"][0]["inventory_id"] == item.id
    assert tcg.approve(db, count)["learned"] == 0
    assert item.quantity == 3


@pytest.mark.parametrize("field,value", [("Total Quantity", "-1"), ("Total Quantity", "2.5"),
                                         ("TCG Marketplace Price", "nan")])
def test_malformed_numeric_values_rejected(db, field, value):
    with pytest.raises(HTTPException):
        count_for(db, **{field: value})


def test_duplicate_csv_skus_rejected(db):
    with pytest.raises(HTTPException):
        tcg.create_count(db, csv_data({}, {}), "dup.csv")


def test_missing_price_requires_explicit_skip(db, card):
    item_for(db, card)
    count = count_for(db, **{"TCG Marketplace Price": ""})
    assert not tcg.view(db, count)["ready"]
    tcg.patch_line(db, count, 1, {"resolution": "skip"})
    assert tcg.approve(db, count)["learned"] == 0


def test_stale_review_blocks_entire_approval(db, card):
    item = item_for(db, card)
    count = count_for(db, **{"Total Quantity": "2"})
    tcg.patch_line(db, count, 1, {"resolution": "accept_tcg"})
    inv.apply_delta(db, item, -1)
    db.commit()
    with pytest.raises(HTTPException, match="Inventory changed"):
        tcg.approve(db, count)
    assert not item.listings
    tcg.rematch(db, count)
    assert tcg.approve(db, count)["adjusted"] == 0


def test_stale_correction_and_uploaded_correction_blocked(db, card):
    item = item_for(db, card)
    count = count_for(db, **{"Total Quantity": "1"})
    tcg.patch_line(db, count, 1, {"resolution": "push_local"})
    tcg.approve(db, count)
    item.quantity = 4
    db.commit()
    with pytest.raises(HTTPException, match="stale"):
        tcg.correction_export(db, count)
    item.quantity = 3
    count.source_data = {**count.source_data, "corrections_uploaded": True}
    db.commit()
    with pytest.raises(HTTPException, match="already marked"):
        tcg.correction_export(db, count)


def test_ignore_override_reprice_export_uses_rules_target(db, card):
    item = item_for(db, card)
    item.price_override = 99
    count = count_for(db)
    tcg.approve(db, count)
    cfg = pricing.default_config()
    cfg["tiers"][0]["guards"]["max_move_pct"] = 10
    db.add(PricingConfig(game="mtg", config=cfg))
    db.commit()
    preview = pricing.simulate(db, "tcgplayer", [item], ignore_overrides=True)[0]
    assert preview["old_price"] == 6.5 and preview["new_price"] == 7.15
    assert preview["override_price"] == 99 and preview["had_override"]
    pricing.apply_reprice(db, "tcgplayer", [item], ignore_overrides=True)
    assert item.price_override == 99
    headers, rows = exporting.build_export(db, [item], layout="tcgplayer")
    assert dict(zip(headers, rows[0]))["TCG Marketplace Price"] == 7.15


def test_additive_migration_preserves_existing_counts_and_listings(monkeypatch):
    from sqlalchemy import create_engine, text, inspect
    from app import db as database
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE cycle_counts (id INTEGER PRIMARY KEY, bin VARCHAR)"))
        conn.execute(text("INSERT INTO cycle_counts VALUES (1, 'Keep me')"))
        conn.execute(text("CREATE TABLE marketplace_listings (id INTEGER PRIMARY KEY, tcg_sku_id VARCHAR)"))
        conn.execute(text("INSERT INTO marketplace_listings VALUES (1, '987')"))
    monkeypatch.setattr(database, "engine", engine)
    database.ensure_schema()
    database.ensure_schema()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT bin, source FROM cycle_counts")).one() == ("Keep me", "physical")
        assert conn.execute(text("SELECT tcg_sku_id FROM marketplace_listings")).scalar() == "987"
    assert "tcg_metadata" in {c["name"] for c in inspect(engine).get_columns("marketplace_listings")}



def test_api_upload_review_approve_export_and_pricing():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db import Base, get_db
    from app.models import CatalogCard
    from app.routers import inventory, exports, pricing as pricing_api

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    app = FastAPI()
    for router in (inventory.router, exports.router, pricing_api.router):
        app.include_router(router)
    app.dependency_overrides[get_db] = lambda: session
    card = CatalogCard(game="mtg", name="Test Bolt", external_id="api-test",
                       collector_number="42", set_code="TEST", tcgplayer_product_id=111)
    session.add(card)
    session.flush()
    item = item_for(session, card, qty=3)
    with TestClient(app) as client:
        result = client.post("/api/inventory/cycle-counts/tcgplayer/upload",
                             files={"file": ("live.csv", csv_data({"Total Quantity": "1"}), "text/csv")})
        assert result.status_code == 200, result.text
        count_id = result.json()["count_id"]
        base = f"/api/inventory/cycle-counts/{count_id}"
        assert client.get("/api/inventory/cycle-counts/list").json()[0]["source"] == "tcgplayer"
        assert not client.get(base).json()["ready"]
        assert client.post(base + "/approve").status_code == 409
        assert client.patch(base + "/tcg-lines/1", json={"resolution": "push_local"}).status_code == 200
        assert client.post(base + "/approve").json()["adjusted"] == 0
        correction = client.get(base + "/corrections")
        assert correction.status_code == 200
        row = next(csv.DictReader(io.StringIO(correction.content.decode("utf-8-sig"))))
        assert row["Add to Quantity"] == "2"
        assert client.post(base + "/corrections-uploaded").json()["corrections_uploaded"]
        assert client.get(base + "/corrections").status_code == 409
        assert client.post(base + "/approve").status_code == 409
        # An unknown foil identity is omitted and counted in the uploadable export.
        item_for(session, card, qty=1, printing="foil")
        result = client.post("/api/exports/inventory", json={"layout": "tcgplayer"})
        assert result.status_code == 200
        assert result.headers["X-Export-Skipped"] == "1"
        rows = list(csv.DictReader(io.StringIO(result.content.decode("utf-8-sig"))))
        assert len(rows) == 1 and rows[0]["TCGplayer Id"] == "987654"
        assert rows[0]["Add to Quantity"] == "0"
        config = pricing.default_config()
        assert client.put("/api/pricing/config/mtg", json=config).status_code == 200
        result = client.post("/api/pricing/simulate/tcgplayer", json={"ignore_overrides": True})
        assert result.status_code == 200 and "age_days_effective" in result.json()["results"][0]
        assert client.post("/api/pricing/apply/tcgplayer", json={"ignore_overrides": "false"}).status_code == 400
        assert item.quantity == 3
    session.close()
    engine.dispose()



def test_legacy_uppercase_english_is_matchable_but_foreign_is_not(db, card):
    item = item_for(db, card)
    item.language = "EN"
    db.commit()
    count = count_for(db)
    assert count.source_data["lines"][0]["inventory_id"] == item.id
    item.language = "ja"
    db.commit()
    assert count_for(db).source_data["lines"][0]["inventory_id"] is None


def cache_products(db, products, *, group_id=1, set_name="TCG Set Spelling", abbreviation=None):
    from app.models import TcgCatalogGroup, utcnow
    from app.services.catalog import _norm_set_name
    db.add(TcgCatalogGroup(game="mtg", group_id=group_id, name=set_name,
                           name_norm=_norm_set_name(set_name), products=products, abbreviation=abbreviation,
                           groups_updated_at=utcnow(), products_updated_at=utcnow()))
    db.commit()


def test_exact_tcg_name_set_links_different_scryfall_name_and_number(db, card):
    item = item_for(db, card)
    cache_products(db, [{"product_id": 111, "name": "TCG Display Name (Borderless)", "number": "0099"}])
    line = tcg.create_count(db, csv_data({"Product Name": "TCG Display Name (Borderless)", "Number": "99"}), "a.csv").source_data["lines"][0]
    assert line["inventory_id"] == item.id
    assert line["match_method"] == "tcgcsv"


@pytest.mark.parametrize("tcg_number", ["", "054b", "123"])
def test_blank_vintage_number_matches_unique_name_and_set(db, card, tcg_number):
    item = item_for(db, card)
    cache_products(db, [{"product_id": 111, "name": "Test Bolt (Artist)", "number": tcg_number}])
    line = tcg.create_count(db, csv_data({"Product Name": "Test Bolt (Artist)", "Number": ""}), "a.csv").source_data["lines"][0]
    assert line["inventory_id"] == item.id
    assert line["match_method"] == "tcgcsv"


def test_blank_number_never_picks_between_two_products_even_if_only_one_owned(db, card):
    item_for(db, card)
    cache_products(db, [{"product_id": 111, "name": "Test Bolt", "number": "1"},
                        {"product_id": 222, "name": "Test Bolt", "number": "2"}])
    count = count_for(db, Number="")
    assert count.source_data["lines"][0]["match_status"] == "ambiguous"
    assert count.source_data["lines"][0]["inventory_id"] is None


def test_tcg_set_and_number_disambiguate_products(db, card):
    item = item_for(db, card)
    cache_products(db, [{"product_id": 999, "name": "Test Bolt", "number": "41"},
                        {"product_id": 111, "name": "Test Bolt", "number": "042"}])
    cache_products(db, [{"product_id": 999, "name": "Test Bolt", "number": "42"}],
                   group_id=2, set_name="Another Set")
    assert count_for(db).source_data["lines"][0]["inventory_id"] == item.id
    other = count_for(db, **{"Set Name": "Another Set"})
    assert other.source_data["lines"][0]["inventory_id"] is None
    assert other.source_data["lines"][0]["product_id"] == 999


def test_zero_listing_without_its_variant_is_dead_but_stocked_requires_review(db, card):
    item_for(db, card, printing="foil")
    cache_products(db, [{"product_id": 111, "name": "Test Bolt", "number": "42"}])
    assert count_for(db, **{"Total Quantity": "0"}).source_data["lines"][0]["resolution"] == "excluded"
    count = count_for(db)
    assert count.source_data["lines"][0]["match_status"] == "unmatched"
    assert tcg.view(db, count)["summary"]["variant_mismatch"] == 1


def test_manual_match_and_quantity_decision_survive_refresh(db, card):
    item = item_for(db, card)
    count = count_for(db, **{"Product Name": "Unrecognized identity", "Total Quantity": "1"})
    tcg.patch_line(db, count, 1, {"inventory_id": item.id, "resolution": "push_local"})
    tcg.rematch(db, count)
    line = count.source_data["lines"][0]
    assert line["inventory_id"] == item.id and line["resolution"] == "push_local"
    assert line["match_method"] == "manual"
    tcg.approve(db, count)
    future = count_for(db, **{"Product Name": "Name changed again", "Total Quantity": "1"})
    assert future.source_data["lines"][0]["inventory_id"] == item.id
    assert future.source_data["lines"][0]["match_method"] == "sku"


def test_refresh_keeps_manual_link_but_requires_new_quantity_decision_after_change(db, card):
    item = item_for(db, card)
    count = count_for(db, **{"Total Quantity": "1"})
    tcg.patch_line(db, count, 1, {"inventory_id": item.id, "resolution": "push_local"})
    item.quantity = 4
    db.commit()
    tcg.rematch(db, count)
    line = count.source_data["lines"][0]
    assert line["inventory_id"] == item.id and line["expected"] == 4
    assert line["resolution"] is None


def test_refresh_rejects_manual_record_that_changed_card_identity(db, card):
    item = item_for(db, card)
    count = count_for(db)
    tcg.patch_line(db, count, 1, {"inventory_id": item.id})
    item.bin = "Different identity"
    db.commit()
    tcg.rematch(db, count)
    assert count.source_data["lines"][0]["inventory_id"] is None
    assert count.source_data["lines"][0]["resolution"] is None


def test_refresh_preserves_skip_and_unchanged_automatic_decision(db, card):
    item_for(db, card)
    count = count_for(db, **{"Total Quantity": "1"})
    tcg.patch_line(db, count, 1, {"resolution": "push_local"})
    tcg.rematch(db, count)
    assert count.source_data["lines"][0]["resolution"] == "push_local"
    tcg.patch_line(db, count, 1, {"resolution": "skip"})
    tcg.rematch(db, count)
    assert count.source_data["lines"][0]["resolution"] == "skip"


def test_ten_thousand_rows_match_without_per_row_database_queries(db):
    from sqlalchemy import event, insert
    from app.models import CatalogCard, InventoryItem
    size = 10000
    db.execute(insert(CatalogCard), [
        dict(id=n, game="mtg", external_id=f"bulk-{n}", tcgplayer_product_id=n,
             name=f"Local Name {n}", set_code="BULK", collector_number=str(n))
        for n in range(1, size + 1)])
    db.execute(insert(InventoryItem), [
        dict(id=n, catalog_card_id=n, condition="NM", printing="normal",
             language="en", quantity=3, bin="")
        for n in range(1, size + 1)])
    cache_products(db, [{"product_id": n, "name": f"Export Name {n}", "number": str(n)}
                       for n in range(1, size + 1)])
    lines = tcg.parse_csv(csv_data(*[
        {"TCGplayer Id": str(n + 100000), "Product Name": f"Export Name {n}", "Number": ""}
        for n in range(1, size + 1)]))
    queries = []
    def record(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)
    event.listen(db.bind, "before_cursor_execute", record)
    try:
        matched, _ = tcg.match_rows(db, lines, sync_catalog=False)
    finally:
        event.remove(db.bind, "before_cursor_execute", record)
    assert [line["inventory_id"] for line in matched] == list(range(1, size + 1))
    assert len(queries) < 50


def test_cached_set_with_contradictory_number_does_not_fall_back_to_other_set(db, card):
    item_for(db, card)
    cache_products(db, [{"product_id": 999, "name": "Test Bolt", "number": "99"}])
    line = count_for(db).source_data["lines"][0]
    assert line["inventory_id"] is None
    assert line["match_status"] == "unmatched"

def test_missing_product_id_uses_verified_set_code_and_number_without_changing_catalog(db, card):
    card.tcgplayer_product_id = None
    item = item_for(db, card)
    cache_products(db, [{"product_id": 111, "name": "Test Bolt (Foil Etched)", "number": "042"}],
                   abbreviation="MH3")
    line = count_for(db, **{"Product Name": "Test Bolt (Foil Etched)"}).source_data["lines"][0]
    assert line["inventory_id"] == item.id and line["product_id"] == 111
    assert card.tcgplayer_product_id is None


@pytest.mark.parametrize("set_code,number,pid", [("OTHER", "42", None), ("MH3", "43", None),
                                              ("MH3", "42", 999), ("MH3", "42 // 43", None)])
def test_missing_id_bridge_rejects_wrong_set_number_or_conflicting_product(db, card, set_code, number, pid):
    card.set_code, card.collector_number, card.tcgplayer_product_id = set_code, number, pid
    item_for(db, card)
    cache_products(db, [{"product_id": 111, "name": "Test Bolt", "number": "42"}], abbreviation="MH3")
    assert count_for(db).source_data["lines"][0]["inventory_id"] is None
