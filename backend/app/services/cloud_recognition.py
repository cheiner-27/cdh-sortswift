"""Manual cloud re-identification (Section 1).

The automatic scan pipeline (services/scanning.recognize_image) is fully local
and never calls out to a third party. This module powers the *explicit*
"re-identify selected with AI" action in the review queue: the user picks the
scans they want re-checked (typically the low-confidence / unresolved ones)
and only those images are sent to a hosted vision model.

Cards are mapped back to the local catalog with the same lookups the local
pipeline uses, so the two paths agree on identity. The API key is read from the
``openai_api_key`` setting if set, else the ``OPENAI_API_KEY`` environment
variable; the model from the ``openai_model`` setting (default gpt-5.2).
"""
import base64
import io
import json
import logging
import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CatalogCard, name_key
from . import scanning
from .settings import get_setting

log = logging.getLogger(__name__)

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

_API = "https://api.openai.com/v1/chat/completions"
_PROMPT = (
    "You are identifying a Magic: The Gathering card from a flatbed scan. "
    "Read the card and return STRICT JSON with keys: "
    "name (the card's English name), "
    "set_code (your BEST GUESS of the card's official 3-5 char set code, e.g. "
    "TLA, SNC, USG, ARN, FEM. Prefer the code printed at the bottom; if none is "
    "printed, infer it from the set symbol, art, border, and copyright year), "
    "collector_number (the collector number printed at the bottom as an integer "
    "string, e.g. '12', '336', else null), "
    "printed_year (the copyright year at the bottom as an integer if visible, else null), "
    "is_promo (true if this looks like a promo / Secret Lair / The List / "
    "prerelease / foil-etched special printing). "
    "Only output the JSON object, nothing else."
)


def _api_key(db: Session) -> str | None:
    return get_setting(db, "openai_api_key") or os.environ.get("OPENAI_API_KEY")


def _b64_jpeg(image_path: str, max_edge: int = 1100) -> str:
    with Image.open(image_path) as img:
        img = img.convert("RGB")
        w, h = img.size
        scale = min(1.0, max_edge / max(w, h))
        if scale < 1.0:
            img = img.resize((int(w * scale), int(h * scale)))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode()


def _ask(model: str, key: str, image_path: str) -> dict:
    content = [
        {"type": "text", "text": _PROMPT},
        {"type": "image_url",
         "image_url": {"url": f"data:image/jpeg;base64,{_b64_jpeg(image_path)}"}},
    ]
    payload = {"model": model, "messages": [{"role": "user", "content": content}]}
    if model.startswith(("gpt-5", "gpt-6", "o3", "o4")):
        payload["max_completion_tokens"] = 1200        # renamed cap + fixed temp
    else:
        payload["max_tokens"] = 400
        payload["temperature"] = 0
    r = httpx.post(_API, headers={"Authorization": f"Bearer {key}"},
                   json=payload, timeout=90)
    r.raise_for_status()
    txt = r.json()["choices"][0]["message"]["content"].strip()
    lo, hi = txt.find("{"), txt.rfind("}")
    if lo == -1 or hi == -1:
        return {}
    try:
        return json.loads(txt[lo:hi + 1])
    except json.JSONDecodeError:
        return {}


def reidentify(db: Session, image_path: str, game: str = "mtg",
               model: str | None = None) -> dict:
    """Send one scan to the vision model and map the read to the catalog.

    Returns the same shape as scanning.recognize_image. Raises RuntimeError on
    a configuration/transport problem so the router can surface it clearly.
    """
    if httpx is None or Image is None:
        raise RuntimeError("cloud re-identify needs httpx and Pillow installed")
    key = _api_key(db)
    if not key:
        raise RuntimeError("no OpenAI API key (set openai_api_key or OPENAI_API_KEY)")
    model = model or get_setting(db, "openai_model") or "gpt-5.2"

    d = _ask(model, key, image_path)
    name = d.get("name") or None
    setc = d.get("set_code")
    setc = str(setc).upper() if setc else None
    num = d.get("collector_number")
    num = str(num) if num not in (None, "", "null") else None
    year = d.get("printed_year")
    try:
        year = int(str(year)[:4]) if year else None
    except (ValueError, TypeError):
        year = None

    # 1) set + number -> exact printing
    if setc and num:
        hits = scanning.lookup_by_ocr(db, game, setc, num)
        if hits:
            return {"candidates": [scanning._candidate_dict(hits[0], 0.95, "cloud_setnum")],
                    "method": "cloud_setnum", "confidence": 0.95, "language": "en"}

    # 1b) inferred set + name (vintage with no printed set code, e.g. USG Island)
    if setc and name:
        rows = db.execute(select(CatalogCard).where(
            CatalogCard.game == game, CatalogCard.set_code == setc,
            CatalogCard.name_norm == name_key(name),
            CatalogCard.is_sealed == False)).scalars().all()  # noqa: E712
        if rows:
            conf = 0.9 if len(rows) == 1 else 0.7
            cands = [scanning._candidate_dict(c, conf if i == 0 else 0.5, "cloud_setname")
                     for i, c in enumerate(rows[:10])]
            return {"candidates": cands, "method": "cloud_setname",
                    "confidence": conf, "language": "en"}

    # 2) name -> printings, disambiguate by copyright year, prefer standard
    names = scanning.lookup_by_name(db, game, name) if name else []
    if not names:
        return {"candidates": [], "method": "cloud_none", "confidence": 0.0, "language": "en"}
    if len(names) == 1:
        return {"candidates": [scanning._candidate_dict(names[0], 0.8, "cloud_name")],
                "method": "cloud_name", "confidence": 0.8, "language": "en"}
    pool = names
    if year:
        yr = [c for c in names if scanning._release_year(c) == year]
        if yr:
            pool = yr
    pool = scanning._prefer_standard(pool)
    conf = 0.75 if len(pool) == 1 else 0.5
    cands = [scanning._candidate_dict(c, conf if i == 0 else 0.4, "cloud_name_year")
             for i, c in enumerate(pool[:10])]
    return {"candidates": cands, "method": "cloud_name_year", "confidence": conf, "language": "en"}
