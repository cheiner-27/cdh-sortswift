"""TCGplayer CSV reconciliation inside the cycle-count review lifecycle."""
import copy
from collections import Counter, defaultdict
import csv
import io

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from ..domain import CONDITION_LABELS, normalize_printing
from ..models import CycleCount, InventoryItem, MarketplaceListing, collector_number_key, name_key, utcnow
from ..validate import choice, whole, money
from . import inventory as inv
from . import order_intake as matcher
from . import tcg_catalog
from .exporting import TCGPLAYER_HEADERS


def identity(item):
    return [item.catalog_card_id, item.custom_sku_id, item.condition,
            item.printing, item.language, item.bin]


def _snapshot(item):
    listing = next((l for l in item.listings if l.marketplace == "tcgplayer"), None)
    return {"identity": identity(item), "quantity": item.quantity,
            "current_price": item.current_price,
            "listed_price": listing.listed_price if listing else None,
            "sku": listing.tcg_sku_id if listing else None}


def _bind(line, item):
    line.update(inventory_id=item.id, expected=item.quantity,
                label=inv.item_description(item), bin=item.bin, snapshot=_snapshot(item),
                resolution="agree" if item.quantity == line["counted"] else None,
                match_status="matched")
    return line


def _variant_fits(line, item):
    return (item.card and item.card.game == matcher._game_code(line["game_label"])
            and item.condition == line["condition"]
            and item.printing == line["printing_canonical"] and (item.language or "").casefold() == "en")


class MatchIndex:
    """Load inventory once; all per-row matching uses bounded dictionary lookups."""

    def __init__(self, db, lines):
        self.products = tcg_catalog.ProductIndex(db, lines)
        self.items = {}
        self.by_sku = defaultdict(list)
        self.by_product = defaultdict(list)
        self.by_number = defaultdict(list)
        self.unlinked_by_set_number = defaultdict(list)
        self.by_name_prefix = defaultdict(list)
        query = select(InventoryItem).options(
            joinedload(InventoryItem.card), selectinload(InventoryItem.listings))
        for item in db.execute(query).scalars():
            self.items[item.id] = item
            for listing in item.listings:
                if listing.marketplace == "tcgplayer" and listing.tcg_sku_id:
                    self.by_sku[listing.tcg_sku_id].append(listing)
            if item.deleted or not item.card:
                continue
            game = item.card.game
            if item.card.tcgplayer_product_id:
                self.by_product[(game, item.card.tcgplayer_product_id)].append(item)
            else:
                key = (game, item.card.set_code.casefold(), tcg_catalog.number_key(item.card.collector_number))
                self.unlinked_by_set_number[key].append(item)
            self.by_number[(game, collector_number_key(item.card.collector_number))].append(item)
            key = name_key(item.card.name) or ""
            # Support names shorter than the normal ten-character comparison.
            for n in range(1, min(len(key), matcher.NAME_PREFIX) + 1):
                self.by_name_prefix[(game, key[:n])].append(item)

    def unlinked_candidates(self, line, product_id):
        # Missing IDs can use an exact set code and complete collector number.
        # Never override a known, different product ID or truncate token numbers.
        number = tcg_catalog.number_key(line.get("collector_number"))
        if not number:
            return []
        game = matcher._game_code(line["game_label"])
        key = name_key(line["card_name"]) or ""
        return [it for code in self.products.set_codes(line, product_id)
                for it in self.unlinked_by_set_number.get((game, code, number), [])
                if matcher._name_agrees(key, name_key(it.card.name))]

    def legacy_candidates(self, line):
        game = matcher._game_code(line["game_label"])
        key = name_key(line["card_name"]) or ""
        number = collector_number_key(line.get("collector_number"))
        bucket = (self.by_number.get((game, number), []) if number else
                  self.by_name_prefix.get((game, key[:matcher.NAME_PREFIX]), []))
        return [it for it in bucket if matcher._name_agrees(key, name_key(it.card.name))]


def _choose_candidate(line, candidates):
    variants = [it for it in candidates if _variant_fits(line, it)]
    in_stock = [it for it in variants if it.quantity > 0]
    choices = in_stock or variants
    if len(choices) == 1:
        return _bind(line, choices[0])
    line["candidates"] = [matcher._candidate(it) for it in (choices or candidates)]
    if choices:
        line.update(match_status="ambiguous", match_note="More than one inventory record fits; choose the record")
    else:
        line.update(match_status="unmatched", match_note=(
            "Product identified; inventory has a different condition, printing or language"
            if candidates else "Product identified; no local inventory record with this product ID"))
    return line


def match_row(db: Session, line: dict, index=None) -> dict:
    line = copy.deepcopy(line)
    line.update(inventory_id=None, expected=None, snapshot=None, resolution=None,
                candidates=[], label=None, bin=None, match_method=None, product_id=None)
    if line["sealed"]:
        line.update(match_status="sealed", resolution="excluded", match_note="Sealed/custom listing; outside singles reconciliation")
        return line
    if not line["parse_ok"]:
        line.update(match_status="unsupported", match_note="Unsupported product line, condition or finish; skip explicitly")
        return line
    index = index or MatchIndex(db, [line])
    links = index.by_sku.get(line["sku"], [])
    if links:
        valid = [l for l in links if not l.item.deleted and _variant_fits(line, l.item)
                 and (l.tcg_metadata or {}).get("identity") == identity(l.item)]
        if len(links) == len(valid) == 1:
            line.update(match_note="Exact stored SKU", match_method="sku")
            return _bind(line, valid[0].item)
        line.update(match_status="ambiguous", match_note="Stored SKU conflicts with inventory; review identity",
                    candidates=[matcher._candidate(l.item) for l in links if not l.item.deleted])
        return line

    product_ids = index.products.lookup(line)
    game = matcher._game_code(line["game_label"])
    if product_ids:
        line["match_method"] = "tcgcsv"
        if len(product_ids) > 1:
            # In particular, a missing vintage number must not pick arbitrarily.
            line.update(match_status="ambiguous", match_note="TCGplayer name/set identifies multiple products; choose manually")
            line["candidates"] = [matcher._candidate(it) for pid in product_ids
                                  for it in index.by_product.get((game, pid), [])]
            return line
        line["product_id"] = product_ids[0]
        line["match_note"] = ("Exact TCGplayer name and set (number blank)" if not line.get("collector_number")
                              else "Exact TCGplayer name, set and number")
        candidates = index.by_product.get((game, product_ids[0]), [])
        if not candidates:
            candidates = index.unlinked_candidates(line, product_ids[0])
            if candidates:
                line["match_note"] = "TCGCSV set code, name and number agree; local product ID missing"
        _choose_candidate(line, candidates)
        if line["counted"] == 0 and not any(_variant_fits(line, it) for it in candidates):
            line.update(match_status="dead", resolution="excluded")
        # A known TCG product must not fall back to an unrelated number/name hit.
        return line

    if index.products.covers(line):
        line.update(match_status="unmatched", match_method="tcgcsv",
                    match_note="No exact name/number in this TCGCSV set; review the listing identity")
        if line["counted"] == 0:
            line.update(match_status="dead", resolution="excluded")
        return line

    candidates = index.legacy_candidates(line)
    line["match_method"] = "name_number"
    line["match_note"] = "Matched against primary catalog name and number"
    _choose_candidate(line, candidates)
    if not line.get("inventory_id") and not candidates:
        line["match_note"] = "No exact TCGplayer product or primary catalog inventory match"
        if line["counted"] == 0:
            line.update(match_status="dead", resolution="excluded")
    return line


def match_rows(db, lines, *, sync_catalog=True):
    catalog = tcg_catalog.ensure_for_lines(db, lines) if sync_catalog else {}
    index = MatchIndex(db, lines)
    return [match_row(db, line, index) for line in lines], catalog


def parse_csv(data: bytes) -> list[dict]:
    try:
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")), strict=True)
        required = {"TCGplayer Id", "Product Line", "Product Name", "Set Name",
                    "Number", "Condition", "Total Quantity", "TCG Marketplace Price"}
        if not required.issubset(reader.fieldnames or []):
            raise HTTPException(400, "Upload a TCGplayer Pricing Custom Export with quantity and price columns")
        if len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise HTTPException(400, "CSV has duplicate column headers")
        lines, seen = [], set()
        for index, raw in enumerate(reader, 1):
            if index > 20000:
                raise HTTPException(400, "CSV must contain at most 20,000 rows")
            if None in raw or any(v is None for v in raw.values()):
                raise HTTPException(400, f"CSV row {index}: incorrect column count")
            raw = {k: v.strip() for k, v in raw.items()}
            sku = raw["TCGplayer Id"]
            if not sku or sku in seen:
                raise HTTPException(400, f"CSV row {index}: missing or duplicate TCGplayer Id")
            seen.add(sku)
            qty = whole(raw["Total Quantity"], f"row {index} quantity")
            price = money(raw["TCG Marketplace Price"], f"row {index} listed price", default=None)
            label, finish = raw["Condition"], "normal"
            sealed = sku.startswith("C-") or label.lower() == "unopened"
            for suffix, printing in ((" Reverse Holofoil", "reverse_holo"),
                                     (" Holofoil", "holo"), (" Foil", "foil"),
                                     (" 1st Edition", "first_edition")):
                if label.endswith(suffix):
                    label, finish = label[:-len(suffix)], printing
                    break
            condition = next((k for k, v in CONDITION_LABELS.items() if v == label), None)
            game = matcher._game_code(raw["Product Line"])
            lines.append({"id": index, "sku": sku, "raw": raw, "counted": qty,
                          "listed_price": price, "sealed": sealed,
                          "game_label": raw["Product Line"], "card_name": raw["Product Name"],
                          "set_name": raw["Set Name"], "collector_number": raw["Number"],
                          "condition_label": label, "condition": condition,
                          "printing": finish, "printing_canonical": normalize_printing(finish, game),
                          "parse_ok": bool(condition and game and sku.isdecimal())})
    except (UnicodeError, csv.Error) as e:
        raise HTTPException(400, f"Unable to read CSV: {e}")
    if not lines:
        raise HTTPException(400, "CSV is empty")
    return lines


def create_count(db: Session, data: bytes, filename: str) -> CycleCount:
    lines, catalog = match_rows(db, parse_csv(data))
    count = CycleCount(bin="", source="tcgplayer", source_data={
        "filename": filename, "lines": lines, "catalog": catalog, "corrections_uploaded": False})
    db.add(count)
    db.commit()
    return count


def _editable(count):
    if count.source != "tcgplayer" or count.status != "in_progress":
        raise HTTPException(409, "This CSV count is no longer editable")


def patch_line(db: Session, count: CycleCount, line_id: int, payload: dict):
    _editable(count)
    data = copy.deepcopy(count.source_data)
    line = next((l for l in data["lines"] if l["id"] == line_id), None)
    if line is None:
        raise HTTPException(404, "Count row not found")
    if "inventory_id" in payload:
        item = db.get(InventoryItem, whole(payload["inventory_id"], "inventory_id", min_value=1))
        if not item or item.deleted or not line["parse_ok"] or not _variant_fits(line, item):
            raise HTTPException(400, "Choose active English inventory with the same game, condition and printing")
        _bind(line, item)
        line.update(match_method="manual", match_note="Identity selected by reviewer")
    if "resolution" in payload:
        resolution = choice(payload["resolution"], "resolution", ("accept_tcg", "push_local", "skip"))
        if resolution != "skip" and not line.get("inventory_id"):
            raise HTTPException(400, "Resolve the inventory identity first")
        line["resolution"] = resolution
    count.source_data = data
    db.commit()


def rematch(db: Session, count: CycleCount):
    _editable(count)
    data = copy.deepcopy(count.source_data)
    previous = data["lines"]
    refreshed, catalog = match_rows(db, previous)
    for old, new in zip(previous, refreshed):
        if old.get("resolution") == "skip":
            new["resolution"] = "skip"
        elif old.get("match_method") == "manual" and old.get("inventory_id"):
            item = db.get(InventoryItem, old["inventory_id"])
            if (item and not item.deleted and _variant_fits(old, item)
                    and identity(item) == (old.get("snapshot") or {}).get("identity")):
                _bind(new, item)
                new.update(match_method="manual", match_note="Identity selected by reviewer")
                if new["snapshot"] == old.get("snapshot"):
                    new["resolution"] = old.get("resolution") or new["resolution"]
                elif new["expected"] != new["counted"]:
                    new["match_note"] += "; stock or pricing changed — review quantity decision again"
            else:
                new.update(inventory_id=None, expected=None, snapshot=None, resolution=None,
                           match_status="unmatched", match_method="manual", label=None, bin=None, candidates=[],
                           match_note="Your selected inventory record changed identity or was deleted; choose again")
        elif (old.get("inventory_id") and new.get("inventory_id") == old["inventory_id"]
              and new.get("snapshot") == old.get("snapshot") and old.get("resolution")):
            new["resolution"] = old["resolution"]
    data.update(lines=refreshed, catalog=catalog)
    count.source_data = data
    db.commit()


def view(db: Session, count: CycleCount):
    data = count.source_data or {}
    lines = copy.deepcopy(data.get("lines", []))
    assigned = Counter(l["inventory_id"] for l in lines
                       if l.get("inventory_id") and l["resolution"] != "skip")
    for line in lines:
        line["conflict"] = bool(line.get("inventory_id") and line["resolution"] != "skip"
                                and assigned[line["inventory_id"]] > 1)
        line["price_missing"] = line["resolution"] not in ("skip", "excluded") and (
            line["listed_price"] is None or line["listed_price"] < .01)
        line["ready"] = bool(line["resolution"]) and not line["conflict"] and not line["price_missing"]
    represented = {l["inventory_id"] for l in lines if l.get("inventory_id")}
    games = {matcher._game_code(l["game_label"]) for l in lines}
    unlisted = [dict(inventory_id=it.id, label=inv.item_description(it), quantity=it.quantity, bin=it.bin)
                for it in db.execute(select(InventoryItem).where(
                    InventoryItem.deleted == False, InventoryItem.quantity > 0).options(joinedload(InventoryItem.card))).scalars()  # noqa: E712
                if it.card and it.card.game in games and it.id not in represented]
    return {"id": count.id, "source": count.source, "status": count.status,
            "filename": data.get("filename"), "lines": lines, "unlisted": unlisted,
            "catalog": data.get("catalog", {}),
            "summary": {
                "unmatched": sum(l["match_status"] in ("unmatched", "unsupported") and l["resolution"] != "skip" for l in lines),
                "missing_product_link": sum(l["match_status"] == "unmatched" and bool(l.get("product_id")) and not l["candidates"] and l["resolution"] != "skip" for l in lines),
                "variant_mismatch": sum(l["match_status"] == "unmatched" and bool(l.get("product_id")) and bool(l["candidates"]) and l["resolution"] != "skip" for l in lines),
                "ambiguous": sum(l["match_status"] == "ambiguous" and l["resolution"] != "skip" for l in lines),
                "variances": sum(bool(l.get("inventory_id")) and l["expected"] != l["counted"] and l["resolution"] != "skip" for l in lines),
                "conflicts": sum(l["conflict"] for l in lines),
                "tcgcsv_matches": sum(l.get("match_method") == "tcgcsv" and bool(l.get("inventory_id")) for l in lines),
            },
            "ready": all(l["ready"] for l in lines),
            "corrections": sum(l.get("resolution") == "push_local" and l["counted"] != l["expected"] for l in lines),
            "corrections_uploaded": data.get("corrections_uploaded", False)}


def approve(db: Session, count: CycleCount):
    _editable(count)
    if not view(db, count)["ready"]:
        raise HTTPException(409, "Review every variance and resolve or skip unmatched/conflicting rows")
    selected = [l for l in count.source_data["lines"] if l["resolution"] not in ("skip", "excluded")]
    # Validate the entire batch before the first mutation.
    for line in selected:
        item = db.get(InventoryItem, line["inventory_id"])
        if not item or item.deleted or _snapshot(item) != line["snapshot"]:
            raise HTTPException(409, f"Inventory changed for row {line['id']}; refresh matches and review again")
        if not line["listed_price"] or line["listed_price"] < .01:
            raise HTTPException(409, f"Row {line['id']} has no usable listed price; skip it or upload a corrected CSV")
        links = db.execute(select(MarketplaceListing).where(
            MarketplaceListing.marketplace == "tcgplayer",
            MarketplaceListing.tcg_sku_id == line["sku"],
            MarketplaceListing.inventory_id != item.id)).scalars().first()
        if links or (line["snapshot"]["sku"] and line["snapshot"]["sku"] != line["sku"]):
            raise HTTPException(409, f"SKU link conflict on row {line['id']}; no changes applied")
    adjusted = 0
    for line in selected:
        item = db.get(InventoryItem, line["inventory_id"])
        delta = line["counted"] - item.quantity
        if line["resolution"] == "accept_tcg" and delta:
            inv.apply_delta(db, item, delta, type="adjustment", cause="tcg_reconcile",
                            comment=f"TCGplayer cycle count #{count.id}, row {line['id']}")
            if delta > 0:
                inv.record_acquisition(db, item, delta, None)
            else:
                inv.consume_fifo(db, item, -delta)
            adjusted += 1
        listing = inv.get_or_create_listing(db, item, "tcgplayer")
        listing.tcg_sku_id = line["sku"]
        listing.tcg_metadata = {"identity": identity(item), "raw": line["raw"], "count_id": count.id}
        listing.listed_price = line["listed_price"]
        listing.listed_quantity = line["counted"]
        listing.status = "listed" if line["counted"] else "unlisted"
        listing.last_synced_at = utcnow()
        listing.dirty = line["resolution"] == "push_local" and delta != 0
        item.current_price = line["listed_price"]
    count.status = "completed"
    count.completed_at = utcnow()
    db.commit()
    return {"adjusted": adjusted, "learned": len(selected)}


def correction_export(db: Session, count: CycleCount):
    if count.source != "tcgplayer" or count.status != "completed":
        raise HTTPException(409, "Approve the count before exporting corrections")
    if count.source_data.get("corrections_uploaded"):
        raise HTTPException(409, "Corrections already marked uploaded; use a fresh TCGplayer export")
    rows = []
    for line in count.source_data["lines"]:
        if line["resolution"] != "push_local" or line["expected"] == line["counted"]:
            continue
        item = db.get(InventoryItem, line["inventory_id"])
        listing = next((l for l in item.listings if l.marketplace == "tcgplayer"), None) if item else None
        if (not item or item.deleted or identity(item) != line["snapshot"]["identity"]
                or item.quantity != line["expected"] or not listing
                or (listing.tcg_metadata or {}).get("count_id") != count.id
                or listing.listed_price != line["listed_price"]):
            raise HTTPException(409, "This correction is stale after inventory/pricing changes; upload a fresh TCGplayer CSV")
        raw = {**line["raw"], "Add to Quantity": line["expected"] - line["counted"]}
        rows.append([raw.get(h, "") for h in TCGPLAYER_HEADERS])
    return TCGPLAYER_HEADERS, rows
