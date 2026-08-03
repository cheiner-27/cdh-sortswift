"""Business-expense ledger (supplies, postage, software, equipment).

Modeled on the Card Tracker Airtable 'Expenses' table: date, name, qty, pre-tax
subtotal, retailer, payment method, and tax (a configurable % of subtotal unless
an override is given). Total = subtotal + tax. These are overhead not tied to any
one card, and are subtracted from sales profit to get net profit.
"""
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import (
    CAPEX_CATEGORIES, EXPENSE_CATEGORIES, EXPENSE_CLASSES, EXPENSE_RETAILERS,
    default_expense_class,
)
from ..models import Expense
from ..validate import choice, money, whole
from .settings import get_setting

FIELDS = ("date", "name", "category", "expense_class", "retailer",
          "payment_method", "quantity", "subtotal", "tax_override", "notes")


def _clean(field: str, value):
    """Validate one expense field by name; pass free text through.

    Subtotal and tax feed the net-profit roll-up, so a string here used to
    reach the ORM and blow up at commit with a raw StatementError.
    """
    if field == "quantity":
        return whole(value, field, default=1, min_value=1)
    if field == "subtotal":
        return money(value, field, default=0.0)
    if field == "tax_override":  # null = fall back to the configured rate
        return money(value, field, default=None)
    if field == "expense_class":
        return choice(value, field, EXPENSE_CLASSES, default=None)
    return value


def default_rate(db: Session) -> float:
    try:
        return float(get_setting(db, "default_expense_tax_rate"))
    except (TypeError, ValueError):
        return 0.06


def tax_for(db: Session, subtotal: float | None, tax_override: float | None) -> float:
    if tax_override is not None:
        return round(float(tax_override), 2)
    return round(float(subtotal or 0) * default_rate(db), 2)


def to_dict(db: Session, e: Expense) -> dict:
    tax = tax_for(db, e.subtotal, e.tax_override)
    return {
        "id": e.id, "date": e.date, "name": e.name, "category": e.category,
        "expense_class": e.expense_class or "opex",
        "retailer": e.retailer, "payment_method": e.payment_method,
        "quantity": e.quantity, "subtotal": e.subtotal,
        "tax_override": e.tax_override, "tax": tax,
        "total": round((e.subtotal or 0) + tax, 2), "notes": e.notes,
    }


def _in_range(e: Expense, date_from: str | None, date_to: str | None) -> bool:
    # ISO YYYY-MM-DD strings compare lexically, so plain string comparison works.
    if date_from and (e.date or "") < date_from:
        return False
    if date_to and (e.date or "") > date_to:
        return False
    return True


def list_expenses(db: Session, date_from: str | None = None,
                  date_to: str | None = None) -> list[Expense]:
    rows = db.execute(select(Expense)).scalars().all()
    rows = [e for e in rows if _in_range(e, date_from, date_to)]
    rows.sort(key=lambda e: (e.date or "", e.id), reverse=True)
    return rows


def create_expense(db: Session, payload: dict) -> Expense:
    e = Expense(**{f: _clean(f, payload.get(f)) for f in FIELDS if f in payload})
    if e.quantity is None:
        e.quantity = 1
    if not e.expense_class:  # not supplied — infer from the category
        e.expense_class = default_expense_class(e.category)
    db.add(e)
    db.commit()
    return e


def update_expense(db: Session, e: Expense, payload: dict) -> Expense:
    for f in FIELDS:
        if f in payload:
            setattr(e, f, _clean(f, payload[f]))
    db.commit()
    return e


def summary(db: Session, date_from: str | None = None,
            date_to: str | None = None) -> dict:
    rows = list_expenses(db, date_from, date_to)
    by_category: dict[str, float] = defaultdict(float)
    by_retailer: dict[str, float] = defaultdict(float)
    by_class: dict[str, float] = defaultdict(float)
    total_subtotal = total_tax = 0.0
    for e in rows:
        tax = tax_for(db, e.subtotal, e.tax_override)
        total = (e.subtotal or 0) + tax
        total_subtotal += e.subtotal or 0
        total_tax += tax
        by_category[e.category or "(uncategorized)"] += total
        by_retailer[e.retailer or "(none)"] += total
        by_class[e.expense_class or "opex"] += total
    return {
        "count": len(rows),
        "total_subtotal": round(total_subtotal, 2),
        "total_tax": round(total_tax, 2),
        "total": round(total_subtotal + total_tax, 2),
        # Capex vs opex split. Both are deducted from net profit in-period
        # (de minimis), but capital spend is reported on its own line.
        "total_opex": round(by_class.get("opex", 0.0), 2),
        "total_capex": round(by_class.get("capex", 0.0), 2),
        "by_class": sorted(({"key": k, "total": round(v, 2)}
                            for k, v in by_class.items()),
                           key=lambda x: -x["total"]),
        "by_category": sorted(({"key": k, "total": round(v, 2)}
                               for k, v in by_category.items()),
                              key=lambda x: -x["total"]),
        "by_retailer": sorted(({"key": k, "total": round(v, 2)}
                               for k, v in by_retailer.items()),
                              key=lambda x: -x["total"]),
    }


def suggestions(db: Session) -> dict:
    """Dropdown values: the fixed defaults plus any distinct values already used
    (so anything added via "Add new" persists as an option next time)."""
    rows = db.execute(select(Expense)).scalars().all()
    retailers = sorted(set(EXPENSE_RETAILERS) | {e.retailer for e in rows if e.retailer})
    categories = sorted(set(EXPENSE_CATEGORIES) | {e.category for e in rows if e.category})
    return {
        "retailers": retailers,
        "categories": categories,
        "classes": list(EXPENSE_CLASSES),
        # category -> suggested class, so the UI can auto-fill on category change
        "capex_categories": sorted(CAPEX_CATEGORIES),
        "payment_methods": sorted({e.payment_method for e in rows if e.payment_method}),
    }
