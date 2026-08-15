"""Packing-slip order intake: PDF parsing, fee math, matching, commit."""
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import (CatalogCard, InventoryItem, Order, SlipOrder,
                        collector_number_key, name_key)
from app.services import order_intake as intake
from app.services import orders as order_svc
from app.services import pdf_slips
from app.services.inventory import add_stock, item_description

DATA = Path(__file__).parent / "data"
SAMPLE = DATA / "packing_slips_sample.pdf"   # 3 single-page orders
MULTI = DATA / "packing_slips_multi.pdf"     # 5 orders, incl. 2-page + foil
VINTAGE = DATA / "packing_slips_vintage.pdf"  # includes an unnumbered pre-numbering-era (Antiquities) line

LOTR = "Universes Beyond: The Lord of the Rings: Tales of Middle-earth"


def slips(path: Path) -> dict[str, dict]:
    orders = pdf_slips.parse_packing_slips(path.read_bytes())
    return {o["order_number"]: o for o in orders}


def make_card(db, *, name, set_name, number, game="mtg", set_code="TST",
              rarity="rare"):
    """Create a catalog card the way the catalog sync does — the _norm matching
    keys are set at write time, not by an ORM hook."""
    card = CatalogCard(
        game=game, external_id=f"ext-{name}-{number}", set_code=set_code,
        set_name=set_name, collector_number=number,
        collector_number_norm=collector_number_key(number),
        name=name, name_norm=name_key(name), rarity=rarity,
        finishes=["normal", "foil"], languages=["en"],
    )
    db.add(card)
    db.commit()
    return card


def stock(db, card, *, condition="NM", printing="normal", qty=1, cost=1.0,
          bin=""):
    item = InventoryItem(catalog_card_id=card.id, condition=condition,
                         printing=printing, language="en", bin=bin, quantity=0)
    db.add(item)
    db.flush()
    add_stock(db, item, qty, cost, cause="test")
    db.commit()
    return item


# --- parsing -----------------------------------------------------------------

def test_sample_pdf_parses_three_orders():
    parsed = slips(SAMPLE)
    assert len(parsed) == 3
    order = parsed["5BABB616-AACCBC-0B672"]
    assert order["order_date"] == "08/01/2026"
    assert order["buyer_name"] == "Shane Collins"
    assert order["ship_city"] == "INDIANAPOLIS"
    assert order["ship_state"] == "IN"
    assert order["ship_postal_code"] == "46239"
    assert order["item_total"] == 34.95
    assert order["quantity_total"] == 2
    assert len(order["lines"]) == 2


def test_every_line_parses_and_totals_reconcile():
    """The printed subtotal and quantity are an independent check on the parse."""
    for path in (SAMPLE, MULTI, VINTAGE):
        for number, order in slips(path).items():
            assert pdf_slips.reconciles(order), f"{number} did not reconcile"
            for line in order["lines"]:
                assert line["parse_ok"], f"{number}: {line.get('raw')}"


def test_vintage_set_line_parses_without_a_collector_number():
    """Pre-numbering-era sets (Antiquities here) print no '#number' segment at
    all, unlike every modern TCGplayer listing."""
    order = slips(VINTAGE)["5BABB616-3D99C2-46D93"]
    line = order["lines"][0]
    assert line["parse_ok"]
    assert line["game_label"] == "Magic"
    assert line["set_name"] == "Antiquities"
    assert line["card_name"] == "Hurkyl's Recall"
    assert line["collector_number"] is None
    assert line["rarity_letter"] == "R"
    assert line["condition_label"] == "Near Mint"


def test_hyphenated_words_rejoin_without_a_space():
    """PDFsharp breaks words at hyphens; the glyph-advance test must rejoin them
    rather than leaving 'Middle- earth'."""
    order = slips(SAMPLE)["5BABB616-AACCBC-0B672"]
    assert order["lines"][0]["set_name"] == LOTR
    assert "Middle-earth" in order["lines"][0]["set_name"]
    assert "- earth" not in order["lines"][0]["set_name"]
    # ZIP+4 is split the same way, and the order number three ways.
    assert "5BABB616-AACCBC-0B672" in slips(SAMPLE)


def test_line_fields_split_out_of_the_description():
    line = slips(SAMPLE)["5BABB616-AACCBC-0B672"]["lines"][0]
    assert line["card_name"] == "Anduril, Flame of the West"
    assert line["set_name"] == LOTR
    assert line["collector_number"] == "236"
    assert line["rarity_letter"] == "M"
    assert line["condition_label"] == "Near Mint"
    assert line["printing"] == "normal"
    assert line["quantity"] == 1
    assert line["unit_price"] == 4.95
    assert line["line_total"] == 4.95


def test_parenthesized_name_suffix_survives():
    line = slips(SAMPLE)["5BABB616-378977-1134E"]["lines"][0]
    assert line["card_name"] == "Turbulent Springs (Extended Art)"
    assert line["set_name"] == "Commander: Secrets of Strixhaven"


def test_foil_is_split_off_the_condition():
    """TCGplayer prints the finish as part of the condition ('Near Mint Foil')."""
    order = slips(MULTI)["5BABB616-1B553A-89210"]
    foils = [l for l in order["lines"] if l["printing"] == "foil"]
    assert len(foils) == 2
    assert foils[0]["card_name"] == "Cauldron of Essence (Extended Art)"
    assert foils[0]["condition_label"] == "Near Mint"
    assert foils[0]["quantity"] == 2
    assert foils[0]["unit_price"] == 4.98
    assert foils[0]["line_total"] == 9.96
    assert foils[1]["condition_label"] == "Lightly Played"


def test_multi_page_order_merges_into_one():
    """A slip whose trailing boilerplate spills onto page 2 is still one order."""
    parsed = slips(MULTI)
    assert len(parsed) == 5
    order = parsed["5BABB616-70249F-12585"]
    assert order["page_count"] == 2
    assert len(order["lines"]) == 12
    assert order["quantity_total"] == 12
    assert pdf_slips.reconciles(order)


def test_slash_collector_number_and_played_conditions():
    lines = slips(MULTI)["5BABB616-70249F-12585"]["lines"]
    spirit = next(l for l in lines if l["card_name"] == "Spirit Mantle")
    assert spirit["collector_number"] == "35/249"
    assert collector_number_key(spirit["collector_number"]) == "35"
    assert {l["condition_label"] for l in lines} == {
        "Near Mint", "Lightly Played", "Moderately Played", "Heavily Played"}


def test_rejects_a_non_slip_upload():
    with pytest.raises(pdf_slips.SlipParseError):
        pdf_slips.parse_packing_slips(b"just a text file, not a PDF")
    with pytest.raises(pdf_slips.SlipParseError):
        pdf_slips.parse_packing_slips(b"%PDF-1.4\nnothing useful here")


# --- fee math ----------------------------------------------------------------

def test_fee_rounds_each_component_to_the_nearest_cent(db):
    """10.75% of 34.95 = 3.757125 -> 3.76; 2.5% of 34.95 = 0.873750 -> 0.87."""
    fee = order_svc.estimate_marketplace_fee(db, subtotal=34.95, tax=0.0)
    assert fee["commission"] == 3.76
    assert fee["processing"] == 1.17          # 0.87 + 0.30 flat
    assert fee["fee"] == 4.93
    assert fee["tax_estimated"] is False


def test_commission_covers_shipping_charged_to_the_buyer(db):
    fee = order_svc.estimate_marketplace_fee(db, subtotal=10.0,
                                             shipping_charged=5.0, tax=0.0)
    assert fee["fee_base"] == 15.0
    assert fee["commission"] == order_svc.round_cent(0.1075 * 15.0) == 1.61


def test_processing_is_charged_on_the_tax_inclusive_total(db):
    untaxed = order_svc.estimate_marketplace_fee(db, subtotal=100.0, tax=0.0)
    taxed = order_svc.estimate_marketplace_fee(db, subtotal=100.0, tax=7.0)
    assert taxed["commission"] == untaxed["commission"]      # tax-free base
    assert taxed["processing"] - untaxed["processing"] == pytest.approx(0.18)


def test_tax_is_estimated_from_the_destination_state(db):
    fee = order_svc.estimate_marketplace_fee(db, subtotal=100.0, state="CA")
    assert fee["tax_estimated"] is True
    assert fee["tax_rate"] == pytest.approx(0.0885)
    assert fee["tax"] == 8.85
    # An unknown or foreign destination falls back rather than assuming zero.
    assert order_svc.estimate_marketplace_fee(
        db, subtotal=100.0, state="ZZ")["tax_rate"] == pytest.approx(0.07)


def test_round_cent_leaves_exact_cents_alone():
    assert order_svc.round_cent(0.30) == 0.30
    assert order_svc.round_cent(1.00) == 1.00
    assert order_svc.round_cent(0.8701) == 0.87
    assert order_svc.round_cent(0.001) == 0.00


def test_round_cent_does_not_round_float_noise_up_a_cent():
    """2.5% of $12 is exactly 30c, but in binary floating point it lands a hair
    over, which naive rounding would bill as 31c."""
    assert 0.025 * 12.0 > 0.30           # the noise is real
    assert order_svc.round_cent(0.025 * 12.0) == 0.30
    assert order_svc.round_cent(0.1075 * 40.0) == 4.30


# --- intake, matching, commit ------------------------------------------------

def test_upload_blocks_orders_whose_cards_are_not_in_stock(db):
    batch = intake.build_batch(db, filename="s.pdf", content=SAMPLE.read_bytes())
    assert len(batch.orders) == 3
    assert all(s.status == "blocked" for s in batch.orders)
    assert all(s.reconciled for s in batch.orders)
    # Nothing went live.
    assert db.query(Order).count() == 0


def test_matched_order_becomes_ready_and_commits_open_without_deducting(db):
    anduril = make_card(db, name="Anduril, Flame of the West", set_name=LOTR,
                        number="236")
    halfling = make_card(db, name="Delighted Halfling", set_name=LOTR,
                         number="158")
    a_item = stock(db, anduril, qty=3, cost=1.0, bin="A1")
    h_item = stock(db, halfling, qty=1, cost=9.0, bin="B2")

    batch = intake.build_batch(db, filename="s.pdf", content=SAMPLE.read_bytes())
    slip = next(s for s in batch.orders
                if s.order_number == "5BABB616-AACCBC-0B672")
    assert slip.status == "ready", slip.error
    assert [l["match_status"] for l in slip.lines] == ["matched", "matched"]
    assert {l["inventory_id"] for l in slip.lines} == {a_item.id, h_item.id}

    order = intake.commit_order(db, slip)
    assert order.status == "open"
    assert order.marketplace == "tcgplayer"
    assert order.external_order_id == "5BABB616-AACCBC-0B672"
    assert order.order_total == 34.95
    assert order.marketplace_fees == slip.estimated_fee
    assert order.ship_to["state"] == "IN" and order.ship_to["city"] == "INDIANAPOLIS"
    assert order.ordered_at.strftime("%Y-%m-%d") == "2026-08-01"
    assert [li.quantity for li in order.items] == [1, 1]
    # Open, not shipped: stock is still on the shelf and no COGS is booked yet.
    assert order.deduction_applied is False
    db.refresh(a_item)
    db.refresh(h_item)
    assert (a_item.quantity, h_item.quantity) == (3, 1)
    assert all(li.cogs == 0.0 for li in order.items)
    assert slip.status == "committed" and slip.order_id == order.id


def test_vintage_card_matches_by_name_when_the_slip_prints_no_number(db):
    """No collector number is printed for this line, so matching has to fall
    back to the card name, still scoped to inventory like the numbered path."""
    card = make_card(db, name="Hurkyl's Recall", set_name="Antiquities", number="58")
    item = stock(db, card, qty=1, cost=20.0, bin="V1")

    batch = intake.build_batch(db, filename="v.pdf", content=VINTAGE.read_bytes())
    slip = next(s for s in batch.orders
                if s.order_number == "5BABB616-3D99C2-46D93")
    assert slip.status == "ready", slip.error
    assert slip.lines[0]["match_status"] == "matched"
    assert slip.lines[0]["inventory_id"] == item.id

    order = intake.commit_order(db, slip)
    assert order.order_total == 85.0


def test_batch_commit_skips_blocked_orders_and_lets_the_rest_through(db):
    """One unmatchable card blocks its own order only."""
    card = make_card(db, name="Shifting Woodland", set_name="Modern Horizons 3",
                     number="228")
    stock(db, card, qty=2, cost=1.0)

    batch = intake.build_batch(db, filename="m.pdf", content=MULTI.read_bytes())
    ready = [s for s in batch.orders if s.status == "ready"]
    blocked = [s for s in batch.orders if s.status == "blocked"]
    assert [s.order_number for s in ready] == ["5BABB616-5A5A6F-972D0"]
    assert len(blocked) == 4

    result = intake.commit_batch(db, batch)
    assert [c["order_number"] for c in result["committed"]] == \
        ["5BABB616-5A5A6F-972D0"]
    assert len(result["skipped"]) == 4
    assert result["batch_status"] == "partially_committed"
    assert db.query(Order).count() == 1
    assert db.query(Order).one().external_order_id == "5BABB616-5A5A6F-972D0"


def test_reuploading_the_same_pdf_marks_orders_duplicate(db):
    card = make_card(db, name="Shifting Woodland", set_name="Modern Horizons 3",
                     number="228")
    stock(db, card, qty=2, cost=1.0)
    first = intake.build_batch(db, filename="m.pdf", content=MULTI.read_bytes())
    intake.commit_batch(db, first)
    assert db.query(Order).count() == 1

    again = intake.build_batch(db, filename="m.pdf", content=MULTI.read_bytes())
    dup = next(s for s in again.orders
               if s.order_number == "5BABB616-5A5A6F-972D0")
    assert dup.status == "duplicate"
    assert dup.order_id == db.query(Order).one().id
    intake.commit_batch(db, again)
    assert db.query(Order).count() == 1  # no second copy


def test_same_card_in_two_bins_is_ambiguous_until_resolved(db):
    """Inventory identity includes the bin, so two in-stock rows can both fit —
    guessing would send the picker to the wrong shelf."""
    card = make_card(db, name="Shifting Woodland", set_name="Modern Horizons 3",
                     number="228")
    a = stock(db, card, qty=1, cost=1.0, bin="A1")
    b = stock(db, card, qty=1, cost=1.0, bin="B2")

    batch = intake.build_batch(db, filename="m.pdf", content=MULTI.read_bytes())
    slip = next(s for s in batch.orders
                if s.order_number == "5BABB616-5A5A6F-972D0")
    assert slip.status == "blocked"
    line = slip.lines[0]
    assert line["match_status"] == "ambiguous"
    assert {c["inventory_id"] for c in line["candidates"]} == {a.id, b.id}

    intake.resolve_line(db, slip, 0, inventory_id=b.id)
    assert slip.lines[0]["match_status"] == "matched"
    assert slip.lines[0]["inventory_id"] == b.id
    assert slip.status == "ready"


def test_wrong_printing_does_not_match_a_normal_copy(db):
    """Foil vs non-foil is a different physical card, so a non-foil record must
    not satisfy a foil sale."""
    card = make_card(db, name="Cauldron of Essence (Extended Art)",
                     set_name="Secrets of Strixhaven", number="347")
    stock(db, card, qty=5, cost=1.0, printing="normal")
    batch = intake.build_batch(db, filename="m.pdf", content=MULTI.read_bytes())
    slip = next(s for s in batch.orders
                if s.order_number == "5BABB616-1B553A-89210")
    line = next(l for l in slip.lines
                if l["card_name"] == "Cauldron of Essence (Extended Art)")
    assert line["printing_canonical"] == "foil"
    assert line["match_status"] == "unmatched"
    assert line["inventory_id"] is None

    stock(db, card, qty=2, cost=1.0, printing="foil")
    intake.match_line(db, line)
    slip.lines = [intake.match_line(db, l) for l in slip.lines]
    intake.refresh(db, slip)
    db.commit()
    refreshed = next(l for l in slip.lines
                     if l["card_name"] == "Cauldron of Essence (Extended Art)")
    assert refreshed["match_status"] == "matched"


def test_condition_must_match_the_condition_sold(db):
    card = make_card(db, name="Shifting Woodland", set_name="Modern Horizons 3",
                     number="228")
    stock(db, card, qty=4, cost=1.0, condition="LP")  # slip says Near Mint
    batch = intake.build_batch(db, filename="m.pdf", content=MULTI.read_bytes())
    slip = next(s for s in batch.orders
                if s.order_number == "5BABB616-5A5A6F-972D0")
    assert slip.lines[0]["condition"] == "NM"
    assert slip.lines[0]["match_status"] == "unmatched"
    # The LP copy is surfaced: selling NM while holding only LP usually means
    # the card was graded wrong on intake, which is worth pointing at.
    assert "you have this card as LP/normal" in slip.lines[0]["match_note"]
    assert len(slip.lines[0]["candidates"]) == 1


def test_zero_quantity_record_gets_its_own_state(db):
    """Out of stock is different from unknown: the right record exists and holds
    nothing, which means the stock record is what's wrong, not the slip."""
    card = make_card(db, name="Shifting Woodland", set_name="Modern Horizons 3",
                     number="228")
    item = stock(db, card, qty=1, cost=1.0)
    item.quantity = 0
    db.commit()
    batch = intake.build_batch(db, filename="m.pdf", content=MULTI.read_bytes())
    slip = next(s for s in batch.orders
                if s.order_number == "5BABB616-5A5A6F-972D0")
    line = slip.lines[0]
    assert line["match_status"] == "out_of_stock"
    assert "holds 0" in line["match_note"]
    assert [c["inventory_id"] for c in line["candidates"]] == [item.id]
    assert slip.status == "blocked"          # must be decided, never auto-committed


def test_short_stock_still_matches_but_flags_the_shortfall(db):
    card = make_card(db, name="Cauldron of Essence (Extended Art)",
                     set_name="Secrets of Strixhaven", number="347")
    stock(db, card, qty=1, cost=1.0, printing="foil")  # 2 were sold
    batch = intake.build_batch(db, filename="m.pdf", content=MULTI.read_bytes())
    slip = next(s for s in batch.orders
                if s.order_number == "5BABB616-1B553A-89210")
    line = next(l for l in slip.lines
                if l["card_name"] == "Cauldron of Essence (Extended Art)")
    assert line["match_status"] == "matched"
    assert "only 1 in stock, 2 sold" in line["match_note"]


def test_skipping_a_line_keeps_its_revenue_but_drops_its_cogs(db):
    card = make_card(db, name="Anduril, Flame of the West", set_name=LOTR,
                     number="236")
    stock(db, card, qty=1, cost=1.0)
    batch = intake.build_batch(db, filename="s.pdf", content=SAMPLE.read_bytes())
    slip = next(s for s in batch.orders
                if s.order_number == "5BABB616-AACCBC-0B672")
    assert slip.status == "blocked"          # Delighted Halfling is unknown
    intake.skip_line(db, slip, 1)
    assert slip.status == "ready"

    order = intake.commit_order(db, slip)
    assert order.order_total == 34.95        # the buyer still paid for both
    skipped = [li for li in order.items if li.inventory_id is None]
    assert len(skipped) == 1
    assert skipped[0].unit_price == 30.0


def test_pinning_the_tax_makes_the_fee_exact(db):
    batch = intake.build_batch(db, filename="s.pdf", content=SAMPLE.read_bytes())
    slip = next(s for s in batch.orders
                if s.order_number == "5BABB616-AACCBC-0B672")
    assert slip.fee_detail["tax_estimated"] is True
    assert slip.fee_detail["tax_rate"] == pytest.approx(0.07)   # IN

    slip.tax = 2.10
    intake.refresh(db, slip)
    db.commit()
    assert slip.fee_detail["tax_estimated"] is False
    assert slip.fee_detail["tax"] == 2.10
    assert slip.estimated_fee == round(3.76 + 0.30 + order_svc.round_cent(
        0.025 * (34.95 + 2.10)), 2)


def test_committing_a_blocked_order_is_refused(db):
    batch = intake.build_batch(db, filename="s.pdf", content=SAMPLE.read_bytes())
    slip = batch.orders[0]
    assert slip.status == "blocked"
    with pytest.raises(ValueError):
        intake.commit_order(db, slip)
    assert db.query(Order).count() == 0


def test_deleting_a_batch_leaves_committed_orders_alone(db):
    card = make_card(db, name="Shifting Woodland", set_name="Modern Horizons 3",
                     number="228")
    stock(db, card, qty=2, cost=1.0)
    batch = intake.build_batch(db, filename="m.pdf", content=MULTI.read_bytes())
    intake.commit_batch(db, batch)
    db.delete(batch)
    db.commit()
    assert db.query(SlipOrder).count() == 0
    assert db.query(Order).count() == 1


def test_matches_despite_a_different_set_name(db):
    """TCGplayer's set naming diverges from the catalog's constantly ("Commander:
    Innistrad: Crimson Vow" vs "Crimson Vow Commander"), so set name must not be
    part of the key. Collector number plus the card name carries the match."""
    card = make_card(db, name="Darksteel Mutation",
                     set_name="Crimson Vow Commander", number="84")
    item = stock(db, card, qty=1, cost=1.0)
    line = next(l for l in slips(MULTI)["5BABB616-70249F-12585"]["lines"]
                if l["card_name"] == "Darksteel Mutation")
    assert line["set_name"] == "Commander: Innistrad: Crimson Vow"   # differs
    matched = intake.match_line(db, line)
    assert matched["match_status"] == "matched"
    assert matched["inventory_id"] == item.id


def test_matches_through_a_treatment_suffix_on_the_name(db):
    """TCGplayer appends "(Extended Art)"; the catalog doesn't carry it."""
    card = make_card(db, name="Cauldron of Essence",
                     set_name="Secrets of Strixhaven", number="347")
    item = stock(db, card, qty=2, cost=1.0, printing="foil")
    line = next(l for l in slips(MULTI)["5BABB616-1B553A-89210"]["lines"]
                if l["card_name"].startswith("Cauldron"))
    assert line["card_name"] == "Cauldron of Essence (Extended Art)"
    assert intake.match_line(db, line)["inventory_id"] == item.id


def test_a_different_card_at_the_same_number_is_not_matched(db):
    """The looser key is only safe because it stays inside your own stock — it
    still must not match a different card that shares a collector number."""
    card = make_card(db, name="Completely Different Card",
                     set_name="Modern Horizons 3", number="228")
    stock(db, card, qty=3, cost=1.0)
    other = make_card(db, name="Shifting Woodland",
                      set_name="Modern Horizons 3", number="228")
    keep = stock(db, other, qty=1, cost=1.0)
    line = slips(MULTI)["5BABB616-5A5A6F-972D0"]["lines"][0]
    assert intake.match_line(db, line)["inventory_id"] == keep.id


def test_matching_never_reaches_for_a_card_you_do_not_stock(db):
    """A catalog hit with no inventory behind it is not an answer — it would
    defeat the point of checking the slip against what's on the shelf."""
    make_card(db, name="Shifting Woodland", set_name="Modern Horizons 3",
              number="228")   # in the catalog, never stocked
    line = slips(MULTI)["5BABB616-5A5A6F-972D0"]["lines"][0]
    matched = intake.match_line(db, line)
    assert matched["match_status"] == "unmatched"
    assert matched["inventory_id"] is None
    assert matched["catalog_card_id"] is None


def test_buyer_name_is_kept_for_review_but_never_on_the_order(db):
    card = make_card(db, name="Shifting Woodland", set_name="Modern Horizons 3",
                     number="228")
    stock(db, card, qty=2, cost=1.0)
    batch = intake.build_batch(db, filename="m.pdf", content=MULTI.read_bytes())
    slip = next(s for s in batch.orders
                if s.order_number == "5BABB616-5A5A6F-972D0")
    assert slip.buyer_name == "Ridge Froehlich"      # visible while reviewing
    order = intake.commit_order(db, slip)
    assert order.buyer_name == ""
    assert "name" not in order.ship_to
    assert order.ship_to["state"] == "FL"            # destination still kept


def test_committed_line_is_described_like_a_manual_sale(db):
    """Same card, same wording, whichever path recorded the sale."""
    card = make_card(db, name="Shifting Woodland", set_name="Modern Horizons 3",
                     number="228", set_code="MH3")
    item = stock(db, card, qty=2, cost=1.0)
    batch = intake.build_batch(db, filename="m.pdf", content=MULTI.read_bytes())
    slip = next(s for s in batch.orders
                if s.order_number == "5BABB616-5A5A6F-972D0")
    order = intake.commit_order(db, slip)
    assert order.items[0].description == item_description(item)
    assert order.items[0].description == "Shifting Woodland [MH3 228] NM normal"


def test_shipping_charged_is_prefilled_for_small_orders(db):
    batch = intake.build_batch(db, filename="m.pdf", content=MULTI.read_bytes())
    small = next(s for s in batch.orders if s.item_total == 4.95)
    big = next(s for s in batch.orders if s.item_total == 28.64)
    assert small.shipping_charged == 1.49
    assert big.shipping_charged == 0.0
    # and it lands in the commission base
    assert small.fee_detail["fee_base"] == 6.44


def test_a_typed_fee_survives_recalculation(db):
    batch = intake.build_batch(db, filename="s.pdf", content=SAMPLE.read_bytes())
    slip = batch.orders[0]
    estimated = slip.estimated_fee
    slip.estimated_fee = 9.99
    slip.fee_overridden = True
    intake.refresh(db, slip)                 # would otherwise overwrite it
    assert slip.estimated_fee == 9.99
    slip.fee_overridden = False
    intake.refresh(db, slip)
    assert slip.estimated_fee == estimated   # cleared -> estimate returns


def test_same_sale_under_a_foreign_id_warns_without_blocking(db):
    """Duplicate detection keys on the order number, so a sale recorded by
    another route (the Airtable migration uses 'airtable-SALE-*') is invisible to
    it. Same marketplace + day + total is warned about, never enforced."""
    card = make_card(db, name="Shifting Woodland", set_name="Modern Horizons 3",
                     number="228")
    stock(db, card, qty=2, cost=1.0)
    db.add(Order(marketplace="tcgplayer", external_order_id="airtable-SALE-00123",
                 order_total=4.95, status="shipped",
                 ordered_at=datetime(2026, 8, 1, 14, 30, tzinfo=timezone.utc)))
    db.commit()

    batch = intake.build_batch(db, filename="m.pdf", content=MULTI.read_bytes())
    slip = next(s for s in batch.orders
                if s.order_number == "5BABB616-5A5A6F-972D0")
    assert slip.warning and "airtable-SALE-00123" in slip.warning
    assert slip.status == "ready"          # a warning must not block
    intake.commit_order(db, slip)
    assert slip.status == "committed"


def test_no_warning_when_nothing_looks_alike(db):
    card = make_card(db, name="Shifting Woodland", set_name="Modern Horizons 3",
                     number="228")
    stock(db, card, qty=2, cost=1.0)
    db.add(Order(marketplace="tcgplayer", external_order_id="airtable-SALE-00999",
                 order_total=99.99, status="shipped",
                 ordered_at=datetime(2026, 8, 1, tzinfo=timezone.utc)))
    db.commit()
    batch = intake.build_batch(db, filename="m.pdf", content=MULTI.read_bytes())
    slip = next(s for s in batch.orders
                if s.order_number == "5BABB616-5A5A6F-972D0")
    assert slip.warning is None


# --- API ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        db = SessionLocal()
        if not db.query(CatalogCard).filter_by(external_id="intake-woodland").first():
            db.add(CatalogCard(
                game="mtg", external_id="intake-woodland", set_code="MH3",
                set_name="Modern Horizons 3", collector_number="228",
                collector_number_norm="228", name="Shifting Woodland",
                name_norm=name_key("Shifting Woodland"), rarity="rare",
                finishes=["normal"], languages=["en"]))
            db.commit()
        db.close()
        yield c


def upload(client, path: Path):
    with path.open("rb") as fh:
        return client.post("/api/order-intake/upload",
                           files={"file": (path.name, fh, "application/pdf")})


def test_api_upload_review_and_commit(client):
    r = upload(client, MULTI)
    assert r.status_code == 200
    batch = r.json()
    assert batch["order_count"] == 5
    assert len(batch["orders"]) == 5

    slip = next(o for o in batch["orders"]
                if o["order_number"] == "5BABB616-5A5A6F-972D0")
    # No stock for the seeded card yet, so it's held back rather than committed.
    assert slip["status"] == "blocked"
    assert slip["lines"][0]["match_status"] == "unmatched"
    assert slip["lines"][0]["inventory_id"] is None
    assert slip["fee_detail"]["tax_estimated"] is True

    # The fee can be corrected directly, without touching tax.
    r = client.patch(f"/api/order-intake/orders/{slip['id']}",
                     json={"estimated_fee": 1.11})
    assert r.status_code == 200
    assert r.json()["estimated_fee"] == 1.11 and r.json()["fee_overridden"] is True

    # Skipping the only line unblocks the order.
    r = client.post(
        f"/api/order-intake/orders/{slip['id']}/lines/0/resolve", json={"skip": True})
    assert r.status_code == 200 and r.json()["status"] == "ready"

    r = client.post(f"/api/order-intake/orders/{slip['id']}/commit")
    assert r.status_code == 200
    order_id = r.json()["order_id"]
    live = client.get(f"/api/orders/{order_id}").json()
    assert live["status"] == "open"
    assert live["marketplace"] == "tcgplayer"
    assert live["external_order_id"] == "5BABB616-5A5A6F-972D0"
    assert live["deduction_applied"] is False
    assert live["ship_to"]["state"] == "FL"

    # Batch commit reports the ones still needing review instead of failing.
    r = client.post(f"/api/order-intake/batches/{batch['id']}/commit").json()
    assert r["batch_status"] == "partially_committed"
    assert len(r["skipped"]) == 4

    # Re-upload is idempotent: the committed order comes back as a duplicate.
    again = upload(client, MULTI).json()
    dup = next(o for o in again["orders"]
               if o["order_number"] == "5BABB616-5A5A6F-972D0")
    assert dup["status"] == "duplicate"
    assert dup["order_id"] == order_id

    client.delete(f"/api/order-intake/batches/{batch['id']}")
    client.delete(f"/api/order-intake/batches/{again['id']}")
    assert client.get(f"/api/orders/{order_id}").status_code == 200


def test_api_rejects_a_non_pdf_upload(client):
    r = client.post("/api/order-intake/upload",
                    files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 400
    assert "PDF" in r.json()["detail"]


def test_api_refuses_to_edit_a_committed_order(client):
    batch = upload(client, SAMPLE).json()
    slip = batch["orders"][0]
    r = client.post(f"/api/order-intake/orders/{slip['id']}/lines/0/resolve",
                    json={"skip": True})
    assert r.status_code == 200
    client.post(f"/api/order-intake/orders/{slip['id']}/lines/1/resolve",
                json={"skip": True})
    assert client.post(
        f"/api/order-intake/orders/{slip['id']}/commit").status_code == 200
    r = client.patch(f"/api/order-intake/orders/{slip['id']}", json={"tax": 1.0})
    assert r.status_code == 400
    client.delete(f"/api/order-intake/batches/{batch['id']}")


def test_api_resolve_rejects_an_out_of_range_line(client):
    batch = upload(client, SAMPLE).json()
    slip = batch["orders"][0]
    r = client.post(f"/api/order-intake/orders/{slip['id']}/lines/99/resolve",
                    json={"skip": True})
    assert r.status_code == 400
    r = client.post(f"/api/order-intake/orders/{slip['id']}/lines/0/resolve",
                    json={})
    assert r.status_code == 400
    client.delete(f"/api/order-intake/batches/{batch['id']}")
