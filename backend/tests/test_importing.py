"""CSV import: normalization, stable-ID matching, modes, undo."""
from app.domain import normalize_condition, normalize_printing
from app.models import InventoryItem, StagingItem
from app.services import importing
from app.services import inventory as inv


def test_printing_normalization_per_game():
    # "holo" means foil for MTG, holo for Pokémon (migration note)
    assert normalize_printing("holo", "mtg") == "foil"
    assert normalize_printing("holo", "pokemon") == "holo"
    assert normalize_printing("Reverse Holo", "pokemon") == "reverse_holo"
    assert normalize_printing("1st Edition") == "first_edition"
    assert normalize_printing("") == "normal"


def test_condition_normalization():
    assert normalize_condition("Near Mint") == "NM"
    assert normalize_condition("lp") == "LP"
    assert normalize_condition("Damaged") == "DMG"


CSV = (b"My ID,Card,SetCode,Num,Cond,Finish,Count,Paid\n"
       b"test-uuid-1,Test Bolt,MH3,42,Near Mint,holo,3,1.25\n")
MAPPING = {"My ID": "external_id", "Card": "name", "SetCode": "set_code",
           "Num": "collector_number", "Cond": "condition", "Finish": "printing",
           "Count": "quantity", "Paid": "cost"}


def test_add_import_routes_to_staging(db, card):
    batch = importing.run_import(db, filename="t.csv", content=CSV,
                                 mapping=MAPPING, value_maps=None,
                                 mode="add", to_staging=True)
    assert batch.status == "completed"
    staged = db.query(StagingItem).all()
    assert len(staged) == 1
    assert staged[0].quantity == 3
    assert staged[0].cost == 1.25


def test_direct_import_and_undo(db, card):
    batch = importing.run_import(db, filename="t.csv", content=CSV,
                                 mapping=MAPPING, value_maps=None,
                                 mode="add", to_staging=False)
    item = db.query(InventoryItem).one()
    assert item.quantity == 3
    # "holo" remapped to "foil" using the matched card's game (mtg)
    assert item.printing == "foil"
    result = importing.undo_import(db, batch)
    assert result["undone"] == 1
    assert item.quantity == 0


def test_overwrite_mode_sets_exact(db, card):
    item = inv.find_or_create_item(db, catalog_card_id=card.id, condition="NM",
                                   printing="foil", bin="")
    inv.add_stock(db, item, 10, 0.5)
    importing.run_import(db, filename="t.csv", content=CSV, mapping=MAPPING,
                         value_maps=None, mode="overwrite", to_staging=False)
    db.refresh(item)
    assert item.quantity == 3


def test_name_only_rows_queue_ambiguous(db, card):
    csv_data = b"Card,Count\nTest Bolt,1\n"
    batch = importing.run_import(db, filename="t.csv", content=csv_data,
                                 mapping={"Card": "name", "Count": "quantity"},
                                 value_maps=None, mode="add", to_staging=False)
    rows = batch.rows
    assert rows[0].status == "ambiguous"
    assert rows[0].candidates[0]["name"] == "Test Bolt"
    importing.resolve_ambiguous_row(db, rows[0], card.id, to_staging=False)
    assert rows[0].status == "imported"


def test_deduction_mode(db, card):
    item = inv.find_or_create_item(db, catalog_card_id=card.id, condition="NM",
                                   printing="foil", bin="A")
    inv.add_stock(db, item, 5, 1.0)
    importing.run_import(db, filename="orders.csv", content=CSV, mapping=MAPPING,
                         value_maps=None, mode="deduction", to_staging=False)
    db.refresh(item)
    assert item.quantity == 2


def test_value_maps_apply(db, card):
    mapped = importing.apply_mapping(
        {"Finish": "shiny", "Cond": "Mint"},
        {"Finish": "printing", "Cond": "condition"},
        {"printing": {"shiny": "foil"}, "condition": {"Mint": "NM"}})
    assert mapped["printing"] == "foil"
    assert mapped["condition"] == "NM"
