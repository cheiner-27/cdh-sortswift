#!/usr/bin/env python
"""One-time (idempotent) import of the Card Tracker Airtable 'Expenses' table
into the cdh-sortswift local database.

Source is the JSON snapshot in ``migration/airtable_expenses.json`` (pulled from
Airtable). Airtable has no category field, so a category is inferred from the
item name / retailer (see CATEGORY_RULES), and the capex/opex class is derived
from that category (Equipment → capex) — always review the dry-run output.

Re-running is safe: a row already in the DB (matched on date + name + subtotal +
quantity) is skipped, so this never double-imports.

    python migration/import_expenses.py                     # dry run — show the plan
    python migration/import_expenses.py --commit            # write to the live DB
    python migration/import_expenses.py --commit \
        --data-dir /tmp/dbcopy/data                         # target a DB copy (test)
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_JSON = Path(__file__).resolve().parent / "airtable_expenses.json"

# (category, name-substring keywords) — first match wins, so order matters.
# Retailer "USPS" always maps to Postage regardless of name.
CATEGORY_RULES = [
    ("Postage", ("stamp", "postage")),
    ("Software", ("subscription", "software", "saas")),
    ("Equipment", ("printer", "cutter", "loupe", "shelv", "zebra", "scanner")),
    ("Supplies", ("sleeve", "loader", "envelope", "mailer", "bag", "protector",
                  "label", "box", "storage", "binder", "penny")),
]


def categorize(name: str, retailer: str) -> str:
    if (retailer or "").strip().lower() == "usps":
        return "Postage"
    n = (name or "").lower()
    for category, keywords in CATEGORY_RULES:
        if any(k in n for k in keywords):
            return category
    return "Other"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", action="store_true",
                    help="write to the DB (default: dry run, nothing written)")
    ap.add_argument("--data-dir",
                    help="override SORTSWIFT_DATA_DIR (e.g. a DB copy to test against)")
    args = ap.parse_args()

    if args.data_dir:
        os.environ["SORTSWIFT_DATA_DIR"] = args.data_dir
    sys.path.insert(0, str(ROOT / "backend"))

    from app.db import DB_PATH, SessionLocal, engine, ensure_schema
    from app.domain import default_expense_class
    from app.models import Base, Expense
    from app.services import expenses as exp_svc

    # Make sure the target DB has the current schema (e.g. the expense_class
    # column) before we insert — the app does this on startup, but this script
    # talks to the DB directly. Both calls are idempotent.
    Base.metadata.create_all(engine)
    ensure_schema()

    payload = json.loads(DATA_JSON.read_text(encoding="utf-8"))
    records = payload["records"]

    db = SessionLocal()
    try:
        existing = {(e.date, e.name, round(e.subtotal or 0, 2), e.quantity or 1)
                    for e in db.query(Expense).all()}
        rate = exp_svc.default_rate(db)

        planned, skipped = [], []
        for r in records:
            qty = r.get("quantity") or 1
            key = (r["date"], r["name"], round(r["subtotal"], 2), qty)
            category = categorize(r["name"], r.get("retailer", ""))
            row = {
                "date": r["date"],
                "name": r["name"],
                "category": category,
                "expense_class": default_expense_class(category),  # Equipment → capex
                "retailer": r.get("retailer", ""),
                "payment_method": r.get("payment_method", ""),
                "quantity": qty,
                "subtotal": r["subtotal"],
                "tax_override": r.get("tax_override"),
                "notes": "",
            }
            (skipped if key in existing else planned).append(row)

        print(f"DB           : {DB_PATH}")
        print(f"tax rate     : {rate:.1%} (applied to rows without a tax override)")
        print(f"source rows  : {len(records)}   already present: {len(skipped)}   "
              f"to import: {len(planned)}\n")

        total_sub = total_tax = 0.0
        by_cat: dict[str, float] = {}
        by_class: dict[str, float] = {}
        for row in planned:
            tax = exp_svc.tax_for(db, row["subtotal"], row["tax_override"])
            total = row["subtotal"] + tax
            total_sub += row["subtotal"]
            total_tax += tax
            by_cat[row["category"]] = by_cat.get(row["category"], 0.0) + total
            by_class[row["expense_class"]] = by_class.get(row["expense_class"], 0.0) + total
            flag = " [tax override]" if row["tax_override"] is not None else ""
            print(f"  {row['date']}  {row['category']:<9} {row['expense_class']:<5} "
                  f"{row['name'][:30]:<30} qty {row['quantity']:>5}  "
                  f"sub {row['subtotal']:>8.2f}  tax {tax:>6.2f}  = {total:>8.2f}  "
                  f"{row['retailer']}{flag}")

        print(f"\n  TOTAL to import: subtotal ${total_sub:.2f} + tax ${total_tax:.2f} "
              f"= ${total_sub + total_tax:.2f}")
        print("  by category    : "
              + ("  ".join(f"{k} ${v:.2f}" for k, v in
                           sorted(by_cat.items(), key=lambda x: -x[1])) or "(none)"))
        print("  by class       : "
              + ("  ".join(f"{k} ${v:.2f}" for k, v in
                           sorted(by_class.items(), key=lambda x: -x[1])) or "(none)"))

        if not args.commit:
            print("\nDRY RUN -- nothing written. Re-run with --commit to import.")
            return 0

        if not planned:
            print("\nNothing to import (all rows already present).")
            return 0

        for row in planned:
            db.add(Expense(**row))
        db.commit()
        print(f"\nCOMMITTED {len(planned)} expense(s) -> {DB_PATH}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
