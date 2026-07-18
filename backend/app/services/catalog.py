"""Card catalog sync from per-game public APIs (Section: Card Catalog Sources).

- MTG: Scryfall (bulk-data feed, per-set paged search for top-ups)
- Pokémon: TCGplayer catalog via TCGcsv (pokemontcg.io moved to a paid tier)
- Yu-Gi-Oh!: YGOPRODECK v7
- One Piece: TCGplayer catalog via TCGcsv
- Prices (all games): TCGcsv price feed

Pokémon and One Piece share the TCGcsv product feed, which gives us the
TCGplayer product id natively (so prices join back to cards) and the printed
collector number in ``extendedData``. Sealed product is filtered out at import
time so it never appears as a recognition candidate (it still exists via the
custom catalog, Section 4).
"""
import csv
import io
import logging
import os
import re
import tempfile
import unicodedata
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    AcquisitionLog, CatalogCard, CatalogSet, InventoryItem, PriceData,
    ScanQueueItem, StagingItem, collector_number_key, utcnow,
)
from .httpclient import client as _http_client

log = logging.getLogger(__name__)

TCGCSV_BASE = "https://tcgcsv.com/tcgplayer"
TCGPLAYER_CATEGORIES = {"mtg": 1, "yugioh": 2, "pokemon": 3, "onepiece": 68}

SEALED_KEYWORDS = (
    "booster box", "booster pack", "bundle", "case", "display", "collection box",
    "elite trainer", "starter deck", "structure deck", "precon", "commander deck",
    "fat pack", "blister", "tin ", " tin", "collector box",
)


def _client():
    """Rate-limited (100 ms), descriptive-User-Agent client for all API calls.

    The 100 ms floor satisfies TCGcsv's usage policy and Scryfall's request-
    spacing guidance regardless of which endpoint a given sync hits.
    """
    return _http_client(min_interval=0.1, timeout=60)


def _tcgplayer_hires(url: str | None) -> str | None:
    """TCGcsv serves the 200px-wide product image (``_200w``). That's fine for
    phashing but below eBay's 500px-long-edge minimum for listing photos, so
    request the 1000px variant instead. No-op for non-TCGplayer URLs."""
    return url.replace("_200w.", "_in_1000x1000.") if url else url


def _upsert_card(db: Session, **kw) -> CatalogCard:
    # Keep the numerator matching key in lockstep with the printed number.
    kw["collector_number_norm"] = collector_number_key(kw.get("collector_number"))
    existing = db.execute(
        select(CatalogCard).where(
            CatalogCard.game == kw["game"],
            CatalogCard.external_id == kw["external_id"],
        )
    ).scalars().first()
    if existing:
        for k, v in kw.items():
            if k != "phash" or v:  # never blank out a computed phash
                setattr(existing, k, v)
        existing.updated_at = utcnow()
        return existing
    card = CatalogCard(**kw)
    db.add(card)
    return card


def _upsert_set(db: Session, game: str, code: str, name: str,
                release_date: str | None = None) -> None:
    existing = db.execute(
        select(CatalogSet).where(CatalogSet.game == game, CatalogSet.code == code)
    ).scalars().first()
    if existing:
        existing.name = name
        if release_date:
            existing.release_date = release_date
    else:
        db.add(CatalogSet(game=game, code=code, name=name, release_date=release_date))


# ---------------------------------------------------------------------------
# MTG — Scryfall
# ---------------------------------------------------------------------------

def sync_mtg_sets(db: Session) -> int:
    with _client() as c:
        data = c.get("https://api.scryfall.com/sets").json()
    n = 0
    for s in data.get("data", []):
        if s.get("digital"):
            continue
        _upsert_set(db, "mtg", s["code"].upper(), s["name"], s.get("released_at"))
        n += 1
    db.commit()
    return n


def _upsert_mtg_card(db: Session, card: dict) -> None:
    """Map one Scryfall card object onto a CatalogCard (shared by the per-set
    search sync and the bulk-data full sync)."""
    faces = card.get("card_faces") or []
    image = (card.get("image_uris") or {}).get("normal")
    back = None
    if not image and faces:
        image = (faces[0].get("image_uris") or {}).get("normal")
        if len(faces) > 1:
            back = (faces[1].get("image_uris") or {}).get("normal")
    _upsert_card(
        db, game="mtg", external_id=card["id"],
        tcgplayer_product_id=card.get("tcgplayer_id"),
        set_code=str(card["set"]).upper(), set_name=card.get("set_name"),
        collector_number=str(card.get("collector_number", "")),
        name=card["name"], rarity=card.get("rarity"),
        finishes=list(card.get("finishes") or []),
        languages=[card.get("lang", "en")],
        image_url=image, back_image_url=back,
        is_double_faced=bool(back),
        is_sealed=False,
        release_date=card.get("released_at"),
    )


def _norm_name(nm: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (nm or "").lower())


def _norm_set_name(nm: str) -> str:
    """Loosely-canonical key for a set NAME, so the same set spelled differently
    across sources collapses together. Repairs the ``�`` mojibake seen in the
    legacy Pokémon data (e.g. ``HS�Triumphant``), strips accents (``Pokémon`` →
    ``Pokemon``) and drops everything but alphanumerics — so ``HeartGold &
    SoulSilver`` and ``HeartGold SoulSilver`` match, while distinct sets stay
    distinct."""
    if not nm:
        return ""
    nm = nm.replace("�", " ")  # replacement char from a prior bad decode
    nm = unicodedata.normalize("NFKD", nm).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", nm.lower())


def _strip_token_suffix(nm: str) -> str:
    for suf in ("Double-Sided Token", "Double Sided Token", "Token", "Emblem"):
        nm = nm.replace(suf, "")
    return nm.strip()


def backfill_mtg_tcgplayer_ids(db: Session) -> dict:
    """Reconcile the MTG catalog with TCGplayer's product list via TCGcsv.

    MTG is sourced from Scryfall, which (a) leaves ``tcgplayer_id`` null on a lot
    of tokens/promos, and (b) models double-sided tokens as separate single
    tokens, whereas TCGplayer sells each physical double-sided card as one
    product (so one Scryfall "Dragon Elemental" maps to several TCGplayer
    products with different backs and prices). Two passes, filling only blanks:

    - **Single-face products** (no "//") backfill their id onto the matching
      Scryfall card in the set *family* {ABBR, T/P/A+ABBR} by number + name.
    - **Double-sided / token products** that aren't already linked are created as
      their own catalog cards (name kept as TCGplayer's, e.g. "Dragon Elemental
      // Treasure Double-Sided Token") so each sellable SKU is distinctly
      findable, priceable and exportable.

    Run "Sync prices" afterward to pull the now-linkable prices. Idempotent.
    """
    cat = TCGPLAYER_CATEGORIES["mtg"]
    gap_index: dict[tuple, list] = defaultdict(list)
    for card in db.execute(select(CatalogCard).where(
            CatalogCard.game == "mtg",
            CatalogCard.tcgplayer_product_id.is_(None))).scalars():
        gap_index[(card.set_code, card.collector_number_norm)].append(card)
    linked_pids = {pid for (pid,) in db.execute(select(CatalogCard.tcgplayer_product_id).where(
        CatalogCard.game == "mtg", CatalogCard.tcgplayer_product_id.isnot(None))).all()}
    set_names = {s.code: s.name for s in db.execute(
        select(CatalogSet).where(CatalogSet.game == "mtg")).scalars()}
    filled = created = 0
    with _client() as c:
        groups = c.get(f"{TCGCSV_BASE}/{cat}/groups").json().get("results", [])
        for group in groups:
            abbr = (group.get("abbreviation") or "").upper()
            if not abbr:
                continue
            family = {abbr, "T" + abbr, "P" + abbr, "A" + abbr}
            try:
                products = c.get(
                    f"{TCGCSV_BASE}/{cat}/{group['groupId']}/products").json().get("results", [])
            except Exception:
                continue
            for p in products:
                pid = p.get("productId")
                if not pid or pid in linked_pids:
                    continue
                ext = {e["name"]: e["value"] for e in (p.get("extendedData") or [])}
                number = ext.get("Number")
                name = str(p.get("name", ""))
                if not number:
                    continue
                is_dft = "//" in str(number)
                nums = [x.strip() for x in str(number).split("//")]
                names = [_strip_token_suffix(x) for x in name.split("//")]
                matched = False
                if not is_dft:  # single-face -> backfill onto a Scryfall card
                    key = collector_number_key(nums[0])
                    target = _norm_name(names[0] if names else "")
                    for sc in family:
                        bucket = gap_index.get((sc, key))
                        hit = bucket and next((card for card in bucket if target and (
                            _norm_name(card.name) == target
                            or target in _norm_name(card.name)
                            or _norm_name(card.name) in target)), None)
                        if hit:
                            hit.tcgplayer_product_id = pid
                            bucket.remove(hit)
                            linked_pids.add(pid)
                            filled += 1
                            matched = True
                            break
                if not matched and (is_dft or "token" in name.lower() or "emblem" in name.lower()):
                    tset = "T" + abbr
                    _upsert_card(
                        db, game="mtg", external_id=f"tcgcsv-{pid}",
                        tcgplayer_product_id=pid, set_code=tset,
                        set_name=set_names.get(tset, group.get("name")),
                        collector_number=nums[0], name=name, rarity="Token",
                        finishes=["normal"], languages=["en"],
                        image_url=_tcgplayer_hires(p.get("imageUrl")),
                        is_double_faced=is_dft, is_sealed=False)
                    linked_pids.add(pid)
                    created += 1
            db.commit()
    return {"filled": filled, "created": created}


def sync_mtg_set_cards(db: Session, set_code: str) -> int:
    url = "https://api.scryfall.com/cards/search"
    params = {"q": f"e:{set_code.lower()}", "unique": "prints", "include_extras": "true"}
    n = 0
    with _client() as c:
        while url:
            r = c.get(url, params=params)
            params = None
            if r.status_code == 404:
                break
            data = r.json()
            for card in data.get("data", []):
                if card.get("digital"):
                    continue
                _upsert_mtg_card(db, card)
                n += 1
            db.commit()
            url = data.get("next_page") if data.get("has_more") else None
    return n


def sync_mtg_all_cards(db: Session) -> int:
    """Full MTG catalog via Scryfall's bulk-data feed.

    Scryfall explicitly asks that whole-catalog consumers use the bulk-data
    files rather than hammering the search API. We grab the ``default_cards``
    file (one object per printing), stream it to a temp file so it never all
    sits in memory, then upsert. Only image *URLs* are stored, so this stays
    a modestly-sized DB, not the multi-GB the raw file would suggest.
    """
    try:
        import ijson  # streaming JSON parser; see backend/requirements.txt
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "ijson is required for a full MTG sync. Install deps with "
            "`pip install -r backend/requirements.txt`."
        ) from e

    fd, tmp_path = tempfile.mkstemp(suffix=".json", prefix="scryfall_bulk_")
    os.close(fd)
    try:
        # timeout=None: the bulk file is large and slow to stream.
        with _http_client(min_interval=0.1, timeout=None) as c:
            manifest = c.get("https://api.scryfall.com/bulk-data").json()
            entry = next((b for b in manifest.get("data", [])
                          if b.get("type") == "default_cards"), None)
            if not entry or not entry.get("download_uri"):
                raise RuntimeError("Scryfall bulk-data 'default_cards' unavailable")
            with c.stream("GET", entry["download_uri"]) as resp:
                resp.raise_for_status()
                with open(tmp_path, "wb") as out:
                    for chunk in resp.iter_bytes(1 << 20):
                        out.write(chunk)

        n = 0
        seen_sets: set[str] = set()
        with open(tmp_path, "rb") as f:
            for card in ijson.items(f, "item"):
                if card.get("digital"):
                    continue
                set_code = str(card.get("set", "")).upper()
                if set_code and set_code not in seen_sets:
                    _upsert_set(db, "mtg", set_code,
                                card.get("set_name") or set_code,
                                card.get("released_at"))
                    seen_sets.add(set_code)
                _upsert_mtg_card(db, card)
                n += 1
                if n % 2000 == 0:
                    db.commit()
        db.commit()
        return n
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Pokémon — TCGplayer catalog via TCGcsv
# ---------------------------------------------------------------------------

def _upsert_pokemon_product(db: Session, set_code: str, set_name: str,
                            release_date: str | None, p: dict) -> bool:
    """Map one TCGcsv product onto a CatalogCard.

    Returns True if a card row was written, False if the product was skipped
    as sealed / non-single (no printed collector number, or a sealed keyword).
    """
    ext = {e["name"]: e["value"] for e in (p.get("extendedData") or [])}
    number = ext.get("Number", "")
    raw_name = p.get("name") or p.get("cleanName") or ""
    if not number or any(k in raw_name.lower() for k in SEALED_KEYWORDS):
        return False  # sealed filtered out of the recognition catalog
    # TCGplayer disambiguates duplicate card names with a " - 029/086" suffix.
    name = raw_name
    if name.endswith(f" - {number}"):
        name = name[: -len(f" - {number}")].strip()
    _upsert_card(
        db, game="pokemon", external_id=str(p["productId"]),
        tcgplayer_product_id=p["productId"],
        set_code=set_code, set_name=set_name,
        collector_number=number,  # printed form, e.g. "029/086"
        name=name, rarity=ext.get("Rarity"),
        finishes=["normal"], languages=["en"],
        image_url=_tcgplayer_hires(p.get("imageUrl")),
        is_sealed=False, release_date=release_date,
    )
    return True


def _sync_pokemon_group(db: Session, c, group: dict) -> int:
    """Sync one TCGplayer group (= one Pokémon set) and its singles."""
    cat = TCGPLAYER_CATEGORIES["pokemon"]
    gid = group["groupId"]
    code = group.get("abbreviation") or str(gid)
    release = (group.get("publishedOn") or "")[:10] or None
    _upsert_set(db, "pokemon", code, group["name"], release)
    products = c.get(f"{TCGCSV_BASE}/{cat}/{gid}/products").json().get("results", [])
    return sum(_upsert_pokemon_product(db, code, group["name"], release, p)
               for p in products)


def sync_pokemon_catalog(db: Session) -> int:
    """Full Pokémon catalog — every set + single via TCGcsv (one file per set)."""
    cat = TCGPLAYER_CATEGORIES["pokemon"]
    n = 0
    with _client() as c:
        groups = c.get(f"{TCGCSV_BASE}/{cat}/groups").json().get("results", [])
        for group in groups:
            n += _sync_pokemon_group(db, c, group)
            db.commit()
    return n


def sync_pokemon_set_cards(db: Session, set_code: str) -> int:
    """Sync a single Pokémon set by its code (TCGplayer group abbreviation)."""
    cat = TCGPLAYER_CATEGORIES["pokemon"]
    with _client() as c:
        groups = c.get(f"{TCGCSV_BASE}/{cat}/groups").json().get("results", [])
        group = next(
            (g for g in groups
             if (g.get("abbreviation") or str(g["groupId"])) == set_code), None)
        if group is None:
            return 0
        n = _sync_pokemon_group(db, c, group)
    db.commit()
    return n


def deduplicate_pokemon_catalog(db: Session) -> dict:
    """Merge legacy pokemontcg.io Pokémon cards into their TCGcsv equivalents.

    Older builds seeded the Pokémon catalog from pokemontcg.io — set codes like
    ``base4``/``neo1``, external ids like ``base4-1``, and crucially *no*
    TCGplayer product id (so no prices). The catalog now comes from TCGcsv
    (uppercase abbreviations like ``BS2``, numeric external ids, a product id
    that joins to the price feed), so every set that existed in both sources
    now shows up twice — e.g. "Base Set 2" under both ``base4`` and ``BS2``.

    The discriminator is reliable: a TCGcsv card always has a
    ``tcgplayer_product_id``; a legacy card never does. For each legacy card we
    find its TCGcsv twin *within the same set name* by normalized collector
    number (breaking ties on name), repoint every reference that could hold it
    — inventory, staging, the scan queue and the FIFO acquisition log — onto the
    twin, then delete the orphaned legacy card. Empty legacy sets are removed
    last. Inventory rows are only repointed, never deleted or merged, so no past
    sale/lot/cycle-count link is ever broken. Idempotent — safe to re-run.
    """
    legacy = db.execute(select(CatalogCard).where(
        CatalogCard.game == "pokemon",
        CatalogCard.tcgplayer_product_id.is_(None))).scalars().all()
    if not legacy:
        return {"remapped_cards": 0, "repointed_refs": 0, "deleted_sets": 0,
                "unmatched_cards": 0, "note": "no legacy Pokémon cards found"}

    # Index canonical (TCGcsv) cards two ways within each normalized set name:
    # by collector-number key (primary), and by normalized card name (fallback
    # for holo/secret subsets numbered differently across the two sources, e.g.
    # Aquapolis "H1" vs the TCGcsv number).
    by_num: dict[tuple, list[CatalogCard]] = defaultdict(list)
    by_name: dict[tuple, list[CatalogCard]] = defaultdict(list)
    for c in db.execute(select(CatalogCard).where(
            CatalogCard.game == "pokemon",
            CatalogCard.tcgplayer_product_id.isnot(None))).scalars():
        sk = _norm_set_name(c.set_name)
        by_num[(sk, collector_number_key(c.collector_number))].append(c)
        by_name[(sk, _norm_name(c.name))].append(c)

    def find_twin(card: CatalogCard) -> CatalogCard | None:
        sk = _norm_set_name(card.set_name)
        bucket = by_num.get((sk, collector_number_key(card.collector_number)))
        if bucket:
            if len(bucket) == 1:
                return bucket[0]
            target = _norm_name(card.name)  # multiple cards share the number
            hit = (next((c for c in bucket if _norm_name(c.name) == target), None)
                   or next((c for c in bucket if target and
                            (target in _norm_name(c.name)
                             or _norm_name(c.name) in target)), None))
            if hit:
                return hit
        # Fallback: unique card of the same name in the same set. Requiring
        # uniqueness keeps a regular print from being mistaken for its holo.
        named = by_name.get((sk, _norm_name(card.name)))
        return named[0] if named and len(named) == 1 else None

    remapped = repointed = unmatched = 0
    for lc in legacy:
        twin = find_twin(lc)
        if twin is None:
            unmatched += 1
            continue
        for inv in db.execute(select(InventoryItem).where(
                InventoryItem.catalog_card_id == lc.id)).scalars():
            inv.catalog_card_id = twin.id
            repointed += 1
        for st in db.execute(select(StagingItem).where(
                StagingItem.catalog_card_id == lc.id)).scalars():
            st.catalog_card_id = twin.id
            repointed += 1
        for sc in db.execute(select(ScanQueueItem).where(
                ScanQueueItem.card_id == lc.id)).scalars():
            sc.card_id = twin.id
            repointed += 1
        for aq in db.execute(select(AcquisitionLog).where(
                AcquisitionLog.catalog_card_id == lc.id)).scalars():
            aq.catalog_card_id = twin.id  # FIFO batches carry over to the twin
            repointed += 1
        db.delete(lc)
        remapped += 1
    db.flush()

    # Drop any Pokémon set that no longer has cards (the emptied legacy sets).
    deleted_sets = 0
    for s in db.execute(select(CatalogSet).where(
            CatalogSet.game == "pokemon")).scalars().all():
        still_used = db.execute(select(CatalogCard.id).where(
            CatalogCard.game == "pokemon",
            CatalogCard.set_code == s.code).limit(1)).first()
        if not still_used:
            db.delete(s)
            deleted_sets += 1
    db.commit()
    return {"remapped_cards": remapped, "repointed_refs": repointed,
            "deleted_sets": deleted_sets, "unmatched_cards": unmatched}


# ---------------------------------------------------------------------------
# Yu-Gi-Oh! — YGOPRODECK
# ---------------------------------------------------------------------------

def sync_yugioh_cards(db: Session) -> int:
    """YGOPRODECK ships the whole card DB in one call; one CatalogCard per set printing."""
    with _client() as c:
        data = c.get("https://db.ygoprodeck.com/api/v7/cardinfo.php").json()
    n = 0
    seen_sets = set()
    for card in data.get("data", []):
        image = None
        images = card.get("card_images") or []
        if images:
            image = images[0].get("image_url")
        for printing in card.get("card_sets", []) or [{}]:
            set_code_full = printing.get("set_code", "")  # e.g. LOB-EN005
            set_prefix = set_code_full.split("-")[0] if set_code_full else "UNK"
            ext_id = f"{card['id']}:{set_code_full or 'base'}"
            if set_prefix not in seen_sets and printing.get("set_name"):
                _upsert_set(db, "yugioh", set_prefix, printing["set_name"])
                seen_sets.add(set_prefix)
            _upsert_card(
                db, game="yugioh", external_id=ext_id,
                tcgplayer_product_id=None,
                set_code=set_prefix, set_name=printing.get("set_name"),
                collector_number=set_code_full,  # yugioh cards print the full code
                name=card["name"], rarity=printing.get("set_rarity"),
                finishes=["normal"], languages=["en"],
                image_url=image, is_sealed=False,
            )
            n += 1
        if n and n % 5000 == 0:
            db.commit()
    db.commit()
    return n


# ---------------------------------------------------------------------------
# One Piece — TCGplayer catalog via TCGcsv
# ---------------------------------------------------------------------------

def sync_onepiece_catalog(db: Session) -> int:
    cat = TCGPLAYER_CATEGORIES["onepiece"]
    n = 0
    with _client() as c:
        groups = c.get(f"{TCGCSV_BASE}/{cat}/groups").json().get("results", [])
        for group in groups:
            gid = group["groupId"]
            _upsert_set(db, "onepiece", group.get("abbreviation") or str(gid),
                        group["name"], group.get("publishedOn", "")[:10] or None)
            products = c.get(f"{TCGCSV_BASE}/{cat}/{gid}/products").json().get("results", [])
            for p in products:
                ext = {e["name"]: e["value"] for e in (p.get("extendedData") or [])}
                number = ext.get("Number", "")
                name_l = p["name"].lower()
                is_sealed = not number or any(k in name_l for k in SEALED_KEYWORDS)
                if is_sealed:
                    continue  # sealed filtered out of recognition catalog
                _upsert_card(
                    db, game="onepiece", external_id=str(p["productId"]),
                    tcgplayer_product_id=p["productId"],
                    set_code=group.get("abbreviation") or str(gid),
                    set_name=group["name"],
                    collector_number=number,
                    name=p["cleanName"] or p["name"],
                    rarity=ext.get("Rarity"),
                    finishes=["normal"], languages=["en"],
                    image_url=_tcgplayer_hires(p.get("imageUrl")), is_sealed=False,
                )
                n += 1
            db.commit()
    return n


# ---------------------------------------------------------------------------
# TCGplayer product-id matching for MTG/Pokémon/YGO + price feed via TCGcsv
# ---------------------------------------------------------------------------

def sync_tcgcsv_prices(db: Session, game: str) -> int:
    """Pull current TCGplayer prices for one game's category via TCGcsv."""
    cat = TCGPLAYER_CATEGORIES[game]
    # Which product ids do we actually stock/know? Only store prices for those.
    known_ids = {
        pid for (pid,) in db.execute(
            select(CatalogCard.tcgplayer_product_id).where(
                CatalogCard.game == game,
                CatalogCard.tcgplayer_product_id.isnot(None),
            )
        ).all()
    }
    n = 0
    with _client() as c:
        groups = c.get(f"{TCGCSV_BASE}/{cat}/groups").json().get("results", [])
        for group in groups:
            gid = group["groupId"]
            try:
                prices = c.get(f"{TCGCSV_BASE}/{cat}/{gid}/prices").json().get("results", [])
            except Exception:
                continue
            for p in prices:
                pid = p["productId"]
                if known_ids and pid not in known_ids:
                    continue
                sub = p.get("subTypeName") or "Normal"
                row = db.execute(
                    select(PriceData).where(
                        PriceData.tcgplayer_product_id == pid,
                        PriceData.sub_type == sub,
                    )
                ).scalars().first()
                if row is None:
                    row = PriceData(tcgplayer_product_id=pid, sub_type=sub)
                    db.add(row)
                row.market = p.get("marketPrice")
                row.mid = p.get("midPrice")
                row.low = p.get("lowPrice")
                row.direct_low = p.get("directLowPrice")
                row.updated_at = utcnow()
                n += 1
            db.commit()
    return n


def import_prices_csv(db: Session, content: bytes) -> int:
    """Import a downloaded TCGcsv price CSV file (offline alternative)."""
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    n = 0
    for rec in reader:
        try:
            pid = int(rec.get("productId") or rec.get("Product Id") or 0)
        except ValueError:
            continue
        if not pid:
            continue
        sub = rec.get("subTypeName") or rec.get("Sub Type Name") or "Normal"

        def num(*keys):
            for k in keys:
                v = rec.get(k)
                if v not in (None, ""):
                    try:
                        return float(v)
                    except ValueError:
                        pass
            return None

        row = db.execute(
            select(PriceData).where(PriceData.tcgplayer_product_id == pid,
                                    PriceData.sub_type == sub)
        ).scalars().first()
        if row is None:
            row = PriceData(tcgplayer_product_id=pid, sub_type=sub)
            db.add(row)
        row.market = num("marketPrice", "Market Price")
        row.mid = num("midPrice", "Mid Price")
        row.low = num("lowPrice", "Low Price")
        row.direct_low = num("directLowPrice", "Direct Low Price")
        row.updated_at = utcnow()
        n += 1
    db.commit()
    return n
