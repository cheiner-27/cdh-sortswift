"""Card scanning & recognition (Section 1).

Pipeline per image:
1. SHA-256 dedup against processed_scans (files never moved/deleted).
2. OCR (pytesseract) on game-specific crop regions, tried in order of
   specificity:
   a. Title band -> card name -> catalog rows sharing that name (many, if
      it's a reprinted card -- narrows the field rather than pinning a print).
   b. Bottom-of-card set code / collector number -> a specific printing,
      cross-narrowed against (a) if both hit.
3. Perceptual-hash fallback against pre-computed catalog phashes, scoped to
   whatever (2) narrowed the field to (a name match, or just a set code) so
   the hash only has to disambiguate a handful of prints, not the catalog.
   Falls back to an unscoped hash sweep if the scoped pool comes up empty,
   since a bad OCR read narrowing to the wrong pool must never be worse than
   no narrowing at all.
4. Anything unresolved -> manual search in the UI.

OCR and phash dependencies are optional imports so the app runs without
tesseract installed (recognition then degrades to phash/manual).
"""
import hashlib
import logging
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    CatalogCard, ProcessedScan, ScanPull, ScanQueueItem, collector_number_key,
    name_key,
)
from .settings import get_setting

log = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

try:
    import imagehash
except ImportError:  # pragma: no cover
    imagehash = None

try:
    import pytesseract
except ImportError:  # pragma: no cover
    pytesseract = None


# --- OCR text regions (fractions of the *card*, not the raw image) --------
# (left, top, right, bottom) crop boxes over likely text locations, expressed
# as fractions of the detected card bounding box (see _locate_card). A flatbed
# scan sits inside a fairly uniform scanner-bed background with the card
# placed a few percent off-edge, and that placement varies scan to scan --
# fractions of the raw image drift out of alignment with the actual text by
# exactly that placement variance, which is large relative to a text band
# only ~5-10% of the card's height. Cropping relative to the card itself
# keeps these aligned regardless of where the card landed on the bed.
OCR_REGIONS = {
    "mtg": [(0.0, 0.90, 0.45, 1.0)],                       # bottom-left
    "pokemon": [(0.55, 0.90, 1.0, 1.0), (0.0, 0.90, 0.45, 1.0)],  # bottom-right (older: left)
    "onepiece": [(0.0, 0.88, 0.5, 1.0), (0.5, 0.0, 1.0, 0.12)],
    "yugioh": [(0.45, 0.55, 1.0, 0.68), (0.4, 0.88, 1.0, 1.0)],   # right-middle set code
}

# Title band, same card-relative coordinate system as OCR_REGIONS. The title
# has sat top-left on every mainstream MTG frame since Alpha (1993), which is
# what's actually been tuned here against a real scan batch (see
# backend/tests/test_scanning.py). The other games default to the same
# generic top band -- name position is standardized top-left across these
# TCGs too -- but that hasn't been checked against real scans of them, so
# treat it as a reasonable starting point rather than a verified fit.
NAME_REGIONS = {
    "mtg": [(0.05, 0.015, 0.90, 0.11)],
    "pokemon": [(0.05, 0.02, 0.85, 0.11)],
    "onepiece": [(0.05, 0.02, 0.85, 0.11)],
    "yugioh": [(0.05, 0.02, 0.85, 0.11)],
}

# Extraction patterns, roughly ordered most-specific first.
PATTERNS = {
    "onepiece": [re.compile(r"\b(OP\d{2}|ST\d{2}|EB\d{2}|P)-(\d{3})\b", re.I)],
    "yugioh": [re.compile(r"\b([A-Z0-9]{2,5})-([A-Z]{2})?(\d{3})\b")],
    "mtg": [
        # Separator is required (not optional): pre-8th-edition cards print no
        # collector number at all, only a bare copyright year ("(c) 1997") in
        # this crop region. An optional separator let this pattern split that
        # year into a fake number+set-code pair (e.g. "1997" -> "1" / "997"),
        # so vintage cards got bogus high-"confidence" OCR hits instead of
        # correctly falling through to phash.
        re.compile(r"\b(\d{1,4})[a-z]?\s*[/·•*]\s*(\d{1,4})?\s*\n?\s*([A-Z0-9]{3,5})\b"),
        re.compile(r"\b([A-Z0-9]{3,5})\s*[·•*]\s*[A-Z]{2}\b"),
        re.compile(r"\b(\d{1,4})\s*/\s*(\d{1,4})\b"),
    ],
    "pokemon": [
        re.compile(r"\b([A-Z]{2,4})\b[^\d]{0,8}(\d{1,3})\s*/\s*(\d{1,3})"),
        re.compile(r"\b(\d{1,3})\s*/\s*(\d{1,3})\b"),
    ],
}

LANG_HINTS = {
    "ja": re.compile(r"[぀-ヿ]"),
    "ko": re.compile(r"[가-힯]"),
    "zhs": re.compile(r"[一-鿿]"),
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _configure_tesseract(db: Session) -> bool:
    if pytesseract is None:
        return False
    cmd = get_setting(db, "tesseract_cmd")
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    return True


def _locate_card(img) -> tuple[int, int, int, int]:
    """Bounding box (l, t, r, b) of the physical card within a raw scan.

    Flatbed scans sit inside a scanner-bed background that's fairly uniform
    in a given corner but placed a few percent off-edge each time. Detected
    by sampling the four corners as the background color and walking inward
    from the mid-row/mid-column until the color departs from it. Falls back
    to the full image when the corners don't look like a uniform mat (e.g. a
    full-bleed/borderless crop already) or the detected box looks wrong.
    """
    w, h = img.size
    full = (0, 0, w, h)
    gray = img.convert("L")
    px = gray.load()
    corners = [px[2, 2], px[w - 3, 2], px[2, h - 3], px[w - 3, h - 3]]
    if max(corners) - min(corners) > 20:
        return full  # corners disagree too much to be a uniform mat
    bg = sum(corners) / len(corners)
    thresh = 12

    def is_bg(x, y):
        return abs(px[x, y] - bg) <= thresh

    midy, midx = h // 2, w // 2
    left = next((x for x in range(w) if not is_bg(x, midy)), 0)
    right = next((x for x in range(w - 1, -1, -1) if not is_bg(x, midy)), w - 1)
    top = next((y for y in range(h) if not is_bg(midx, y)), 0)
    bottom = next((y for y in range(h - 1, -1, -1) if not is_bg(midx, y)), h - 1)
    if right - left < w * 0.5 or bottom - top < h * 0.5:
        return full  # detection went wrong; don't trust an implausible box
    return (left, top, right + 1, bottom + 1)


def _ocr_regions(img, bbox, regions: list, psm: int = 6) -> str | None:
    """OCR each (l, t, r, b) card-fraction region and join the results.

    Returns None (rather than partial text) if tesseract itself fails, so
    callers can distinguish "tesseract broken" from "ran fine, found nothing".
    """
    bl, bt, br, bb = bbox
    bw, bh = br - bl, bb - bt
    texts = []
    for (l, t, r, b) in regions:
        crop = img.crop((bl + int(l * bw), bt + int(t * bh),
                         bl + int(r * bw), bt + int(b * bh)))
        crop = crop.resize((crop.width * 2, crop.height * 2))
        try:
            texts.append(pytesseract.image_to_string(crop, config=f"--psm {psm}"))
        except Exception as e:  # tesseract missing/broken at runtime
            log.warning("tesseract failed: %s", e)
            return None
    return "\n".join(texts)


def ocr_extract(db: Session, image_path: str, game: str) -> dict:
    """OCR the game-specific regions.

    Returns {set_code, number, raw, name_raw, language, ok}. ``ok`` reflects
    only the set-code/collector-number read (the historical contract of this
    function); ``name_raw`` is populated independently whenever a title band
    is defined for the game and OCR found anything there.
    """
    result = {"set_code": None, "number": None, "raw": "", "name_raw": None,
              "language": None, "ok": False}
    if Image is None or not _configure_tesseract(db):
        return result
    try:
        img = Image.open(image_path).convert("L")
    except Exception as e:
        log.warning("cannot open %s: %s", image_path, e)
        return result
    bbox = _locate_card(img)

    raw = _ocr_regions(img, bbox, OCR_REGIONS.get(game, [(0, 0.85, 1, 1)]))
    if raw is None:
        return result
    result["raw"] = raw

    name_regions = NAME_REGIONS.get(game)
    if name_regions:
        name_text = _ocr_regions(img, bbox, name_regions)
        if name_text and name_text.strip():
            result["name_raw"] = name_text.strip()

    for lang, rx in LANG_HINTS.items():
        if rx.search(raw):
            result["language"] = lang
            break
    for rx in PATTERNS.get(game, []):
        m = rx.search(raw)
        if not m:
            continue
        groups = [g for g in m.groups() if g]
        if game == "onepiece":
            result["set_code"] = groups[0].upper()
            result["number"] = f"{groups[0].upper()}-{groups[1]}"
        elif game == "yugioh":
            result["number"] = m.group(0).upper()
            result["set_code"] = groups[0].upper()
        elif game == "mtg":
            nums = [g for g in groups if g.isdigit()]
            alphas = [g for g in groups if not g.isdigit()]
            if nums:
                result["number"] = str(int(nums[0]))
            if alphas:
                result["set_code"] = alphas[0].upper()
        elif game == "pokemon":
            nums = [g for g in groups if g.isdigit()]
            alphas = [g for g in groups if not g.isdigit()]
            if nums:
                result["number"] = str(int(nums[0]))
            if alphas:
                result["set_code"] = alphas[0].upper()
        result["ok"] = bool(result["number"])
        if result["ok"]:
            break
    return result


def lookup_by_ocr(db: Session, game: str, set_code: str | None,
                  number: str | None) -> list[CatalogCard]:
    if not number:
        return []
    q = select(CatalogCard).where(CatalogCard.game == game,
                                  CatalogCard.is_sealed == False)  # noqa: E712
    if game == "yugioh":
        # collector_number stores the full print code (LOB-EN005)
        q = q.where(CatalogCard.collector_number.ilike(f"%{number}%"))
    else:
        # Match on the numerator key so printed/stored variants collapse:
        # OCR "4" hits catalog "4/102", "004/102" and "4" alike.
        q = q.where(CatalogCard.collector_number_norm == collector_number_key(number))
        if set_code:
            exact = db.execute(q.where(CatalogCard.set_code == set_code)).scalars().all()
            if exact:
                return exact
    return db.execute(q.limit(25)).scalars().all()


def lookup_by_name(db: Session, game: str, name_raw: str | None,
                   min_len: int = 3, max_len: int = 60) -> list[CatalogCard]:
    """Catalog rows whose name is (very likely) what the title-band OCR read.

    The title crop also catches mana-cost symbols etc., so the OCR text is
    usually the real name plus trailing junk rather than an exact match.
    Rather than an exact-equality lookup (breaks on any junk) or a full-table
    fuzzy scan (too slow against a catalog with hundreds of thousands of
    printings), generate every prefix of the normalized OCR text up to
    `max_len` and look those up with a single indexed IN query -- the real
    name, if OCR got it right, is one of those prefixes.
    """
    norm = name_key(name_raw)
    if not norm or len(norm) < min_len:
        return []
    prefixes = [norm[:i] for i in range(min_len, min(len(norm), max_len) + 1)]
    rows = db.execute(
        select(CatalogCard).where(
            CatalogCard.game == game, CatalogCard.is_sealed == False,  # noqa: E712
            CatalogCard.name_norm.in_(prefixes),
        )
    ).scalars().all()
    if not rows:
        return []
    # Prefer the longest matching name: a short catalog name that happens to
    # be a prefix of a longer one (or of OCR junk) is the coincidence to
    # avoid, not the signal to trust.
    best_len = max(len(c.name_norm) for c in rows)
    return [c for c in rows if len(c.name_norm) == best_len]


def compute_phash(image_path: str) -> str | None:
    if Image is None or imagehash is None:
        return None
    try:
        with Image.open(image_path) as img:
            return str(imagehash.phash(img.convert("RGB")))
    except Exception:
        return None


def phash_candidates(db: Session, image_path: str, game: str | None,
                     max_distance: int, top_n: int = 5,
                     card_ids: set[int] | None = None,
                     set_code: str | None = None) -> list[tuple[CatalogCard, int]]:
    """Compare scan phash against catalog phashes; return (card, distance) top-N.

    ``card_ids`` (preferred) or ``set_code`` scope the comparison pool to
    candidates already narrowed by OCR, so a hash only has to disambiguate a
    handful of prints instead of the whole catalog -- fewer chances for an
    accidental hash collision, and faster. Omit both for the old unscoped
    full-catalog sweep.
    """
    if imagehash is None:
        return []
    scan_hash_s = compute_phash(image_path)
    if not scan_hash_s:
        return []
    scan_hash = imagehash.hex_to_hash(scan_hash_s)
    q = select(CatalogCard).where(CatalogCard.phash.isnot(None),
                                  CatalogCard.is_sealed == False)  # noqa: E712
    if game:
        q = q.where(CatalogCard.game == game)
    if card_ids is not None:
        q = q.where(CatalogCard.id.in_(card_ids))
    elif set_code:
        q = q.where(CatalogCard.set_code == set_code)
    scored = []
    for card in db.execute(q).scalars():
        try:
            d = scan_hash - imagehash.hex_to_hash(card.phash)
        except ValueError:
            continue
        if d <= max_distance:
            scored.append((card, d))
    scored.sort(key=lambda x: x[1])
    return scored[:top_n]


def build_catalog_phashes(db: Session, game: str, set_code: str | None = None,
                          limit: int | None = None) -> int:
    """Download catalog reference images and store their phashes (opt-in, slow).

    Uses the shared rate-limited client so image fetches carry a descriptive
    User-Agent — Scryfall's CDN 400s the default httpx User-Agent, which is
    what caused the wall of errors when building MTG phashes.
    """
    from .httpclient import client as _http_client
    if Image is None or imagehash is None:
        return 0
    q = select(CatalogCard).where(
        CatalogCard.game == game, CatalogCard.phash.is_(None),
        CatalogCard.image_url.isnot(None),
        CatalogCard.is_sealed == False)  # noqa: E712
    if set_code:
        q = q.where(CatalogCard.set_code == set_code)
    if limit:
        q = q.limit(limit)
    cards = db.execute(q).scalars().all()
    n = 0
    import io as _io
    with _http_client(min_interval=0.1, timeout=30) as c:
        for card in cards:
            try:
                r = c.get(card.image_url)
                r.raise_for_status()
                with Image.open(_io.BytesIO(r.content)) as img:
                    card.phash = str(imagehash.phash(img.convert("RGB")))
                n += 1
            except Exception as e:
                log.warning("phash build failed for %s: %s", card.name, e)
            if n and n % 100 == 0:
                db.commit()
    db.commit()
    return n


def check_resolution(image_path: str, min_edge: int) -> bool:
    """True if the image is BELOW the resolution threshold (likely to fail OCR)."""
    if Image is None:
        return False
    try:
        with Image.open(image_path) as img:
            return min(img.size) < min_edge
    except Exception:
        return True


def _candidate_dict(card: CatalogCard, score: float, method: str) -> dict:
    return {
        "card_id": card.id, "name": card.name, "set_code": card.set_code,
        "set_name": card.set_name, "collector_number": card.collector_number,
        "rarity": card.rarity, "image_url": card.image_url,
        "score": round(score, 3), "method": method,
    }


def recognize_image(db: Session, image_path: str, game: str) -> dict:
    """Run the full recognition pipeline on one image.

    Tiered so each stage narrows rather than replaces the last:
    1. Title-band OCR -> name matches (possibly many, if reprinted).
    2. Collector-number/set-code OCR -> a specific printing, cross-narrowed
       against (1) when both hit (never *less* trusted than (1) alone).
    3. If (2) didn't resolve it: perceptual hash, scoped to (1)'s name
       matches, or to (2)'s set code if there was no name match, so the hash
       only has to pick among a handful of prints. Retried unscoped if the
       scoped pool comes up empty, so a bad OCR read can't leave a scan worse
       off than before this narrowing existed.
    """
    phash_max = int(get_setting(db, "phash_max_distance"))
    candidates: list[dict] = []
    method = None
    confidence = 0.0

    ocr = ocr_extract(db, image_path, game)
    language = ocr["language"]

    name_matches = lookup_by_name(db, game, ocr["name_raw"])

    numset_matches = []
    if ocr["ok"]:
        numset_matches = lookup_by_ocr(db, game, ocr["set_code"], ocr["number"])
        if numset_matches and name_matches:
            name_ids = {c.id for c in name_matches}
            narrowed = [c for c in numset_matches if c.id in name_ids]
            if narrowed:
                numset_matches = narrowed

    if numset_matches:
        method = "ocr"
        # Unique exact match with set code = high confidence
        confidence = 0.95 if (len(numset_matches) == 1 and ocr["set_code"]) else \
            0.8 if len(numset_matches) == 1 else 0.6
        candidates = [_candidate_dict(m, confidence if i == 0 else 0.5, "ocr")
                      for i, m in enumerate(numset_matches[:10])]
    elif len(name_matches) == 1:
        # Number/set OCR didn't pin a print, but the name is unique across
        # this game's *entire* catalog (no reprints) -- as good as a print id.
        method = "ocr_name"
        confidence = 0.7
        candidates = [_candidate_dict(name_matches[0], confidence, "ocr_name")]

    if not candidates:
        name_ids = {c.id for c in name_matches} if name_matches else None
        scope_set_code = ocr["set_code"] if not name_ids else None
        ph = phash_candidates(db, image_path, game, phash_max,
                              card_ids=name_ids, set_code=scope_set_code)
        scoped = bool(name_ids or scope_set_code)
        if not ph and scoped:
            ph = phash_candidates(db, image_path, game, phash_max)
            scoped = False
        if ph:
            method = "phash_name" if (scoped and name_ids) else \
                "phash_set" if scoped else "phash"
            best_d = ph[0][1]
            confidence = max(0.0, 1.0 - best_d / 20.0)
            candidates = [
                _candidate_dict(card, max(0.0, 1.0 - d / 20.0), method)
                for card, d in ph
            ]

    return {"candidates": candidates, "method": method,
            "confidence": confidence, "language": language}


# Trailing OS duplicate-suffix, e.g. "...0007F (1).jpg" -> ignored when
# matching so a scanner naming glitch doesn't break front/back pairing.
_DUP_SUFFIX_RE = re.compile(r"\s*\(\d+\)$")
_SIDE_RE = re.compile(r"^(?P<key>.+?)(?P<side>[FB])$", re.IGNORECASE)


def _parse_scan_name(path: Path) -> tuple[str, str | None]:
    """Split a scan filename into its pairing key and side (F/B).

    Expected convention: ``{date}-image-{####}{F|B}``, e.g.
    ``20260718-image-0007F.jpg`` paired with ``20260718-image-0007B.jpg``.
    Returns (key, None) when the name has no recognized F/B suffix.
    """
    stem = _DUP_SUFFIX_RE.sub("", path.stem)
    m = _SIDE_RE.match(stem)
    if m:
        return m.group("key"), m.group("side").upper()
    return stem, None


def _pair_scan_files(
    files: list[Path], pair_front_back: bool,
) -> list[tuple[Path, Path | None]]:
    """Pair front/back images by filename rather than by folder order.

    Files with no matching counterpart (an orphaned back, or a name with no
    F/B suffix) are kept as front-only rather than dropped, since we only
    ever run recognition on the front image.
    """
    if not pair_front_back:
        return [(f, None) for f in files]

    fronts: dict[str, Path] = {}
    backs: dict[str, Path] = {}
    loners: list[Path] = []
    for path in files:
        key, side = _parse_scan_name(path)
        if side == "F":
            fronts[key] = path
        elif side == "B":
            backs[key] = path
        else:
            loners.append(path)

    pairs = [(front, backs.pop(key, None)) for key, front in sorted(fronts.items())]
    pairs.extend((p, None) for p in sorted(backs.values(), key=lambda p: p.name))
    pairs.extend((p, None) for p in loners)
    return pairs


def pull_scans(
    db: Session, folder: str, game: str, *,
    use_subfolder_bins: bool = False, pair_front_back: bool = False,
    session_defaults: dict | None = None,
) -> ScanPull:
    """Pull new images from the scan folder into the scan queue.

    Files are never moved or deleted; dedup is SHA-256 via processed_scans.
    """
    defaults = session_defaults or {}
    root = Path(folder)
    if not root.is_dir():
        raise FileNotFoundError(f"Scan folder not found: {folder}")
    min_edge = int(get_setting(db, "min_scan_resolution"))
    threshold = float(get_setting(db, "confidence_threshold"))

    if use_subfolder_bins:
        groups = [(sub.name, sorted(p for p in sub.iterdir()
                                    if p.suffix.lower() in SUPPORTED_EXTENSIONS))
                  for sub in sorted(root.iterdir()) if sub.is_dir()]
    else:
        groups = [(defaults.get("bin", ""),
                   sorted(p for p in root.iterdir()
                          if p.suffix.lower() in SUPPORTED_EXTENSIONS))]

    pull = ScanPull(folder=str(root), use_subfolder_bins=use_subfolder_bins,
                    pair_front_back=pair_front_back)
    db.add(pull)
    db.flush()

    seq = 0
    count = 0
    known_hashes = {
        h for (h,) in db.execute(select(ProcessedScan.sha256)).all()
    }
    for bin_name, files in groups:
        new_files = []
        for path in files:
            digest = sha256_file(path)
            if digest in known_hashes:
                continue
            known_hashes.add(digest)
            db.add(ProcessedScan(sha256=digest, file_path=str(path), pull_id=pull.id))
            new_files.append(path)

        for front, back in _pair_scan_files(new_files, pair_front_back):
            seq += 1
            count += 1 + (1 if back else 0)
            rec = recognize_image(db, str(front), game)
            top = rec["candidates"][0] if rec["candidates"] else None
            item = ScanQueueItem(
                pull_id=pull.id, seq=seq,
                image_path=str(front),
                back_image_path=str(back) if back else None,
                file_name=front.name,
                low_resolution=check_resolution(str(front), min_edge),
                method=rec["method"],
                confidence=rec["confidence"],
                candidates=rec["candidates"],
                card_id=top["card_id"] if top else None,
                status="pending" if (top and rec["confidence"] >= threshold) else "needs_review",
                condition=defaults.get("condition", "NM"),
                # Printing is a per-card property (foil/holo/etc.), never a whole
                # session — it defaults to normal and is set during review.
                printing="normal",
                language=rec["language"] or defaults.get("language", "en"),
                bin=bin_name or defaults.get("bin", ""),
                cost=defaults.get("cost"),
            )
            db.add(item)

    pull.image_count = count
    db.commit()
    return pull
