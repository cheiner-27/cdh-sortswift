"""Key/value settings with defaults."""
from sqlalchemy.orm import Session

from ..models import Setting

DEFAULTS = {
    "scan_folder": "",
    "min_scan_resolution": 400,          # min shorter-edge pixels before flagging
    "confidence_threshold": 0.75,        # below this -> needs_review
    "phash_max_distance": 12,            # hamming distance cutoff for candidates
    "tesseract_cmd": "",                 # blank = use PATH
    # Manual cloud re-identify (never called automatically): the hosted vision
    # model, an optional key override (blank = OPENAI_API_KEY env var), and the
    # confidence at/below which the review UI pre-selects a scan for re-eval.
    "openai_model": "gpt-5.2",
    "openai_api_key": "",
    "reeval_auto_select_below": 0.40,
    "shippo_api_token": "",
    "shippo_test_mode": True,
    "label_min_order_value": 25.0,       # orders <= this skip auto label
    "large_move_pct": 25.0,              # flag threshold in reprice preview
    "session_defaults": {"condition": "NM", "language": "en", "bin": ""},
    "ebay_poll_interval_minutes": 10,
    "ebay_marketplace_id": "EBAY_US",
    "ebay_merchant_location_key": "",    # must exist in Seller Hub (see Open Items)
    "import_undo_window_minutes": 15,
    "default_expense_tax_rate": 0.06,    # tax estimate for expenses lacking an override
    # Pick-list ordering: ordered list of fields (condition, name, set_code,
    # bin, collector_number, printing). Custom/unmatched lines always sort last.
    "pick_list_sort": ["condition", "name"],
}


def get_setting(db: Session, key: str):
    row = db.get(Setting, key)
    if row is not None and isinstance(row.value, dict) and "v" in row.value:
        return row.value["v"]
    return DEFAULTS.get(key)


def set_setting(db: Session, key: str, value) -> None:
    row = db.get(Setting, key)
    if row is None:
        row = Setting(key=key, value={"v": value})
        db.add(row)
    else:
        row.value = {"v": value}


def all_settings(db: Session) -> dict:
    out = dict(DEFAULTS)
    for row in db.query(Setting).all():
        if isinstance(row.value, dict) and "v" in row.value:
            out[row.key] = row.value["v"]
    return out
