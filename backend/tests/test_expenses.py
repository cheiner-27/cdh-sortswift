"""Expense ledger: tax calc (default rate + override), totals, summary."""
from app.services import expenses as exp
from app.services.settings import set_setting


def test_tax_default_rate_and_total(db):
    e = exp.create_expense(db, {"date": "2026-06-01", "name": "Penny Sleeves",
                                "category": "Supplies", "retailer": "Amazon",
                                "quantity": 1000, "subtotal": 9.99})
    d = exp.to_dict(db, e)
    assert d["tax"] == round(9.99 * 0.06, 2)      # 6% default
    assert d["total"] == round(9.99 + d["tax"], 2)


def test_tax_override_wins(db):
    e = exp.create_expense(db, {"name": "Stamps", "subtotal": 65.15, "tax_override": 0.0})
    d = exp.to_dict(db, e)
    assert d["tax"] == 0.0 and d["total"] == 65.15


def test_configurable_rate(db):
    set_setting(db, "default_expense_tax_rate", 0.08)
    db.commit()
    e = exp.create_expense(db, {"name": "Top Loaders", "subtotal": 100.0})
    assert exp.to_dict(db, e)["tax"] == 8.0


def test_summary_groups_and_date_filter(db):
    exp.create_expense(db, {"date": "2026-01-05", "name": "A", "category": "Supplies",
                            "retailer": "Amazon", "subtotal": 10.0, "tax_override": 0})
    exp.create_expense(db, {"date": "2026-06-05", "name": "B", "category": "Software",
                            "retailer": "Airtable", "subtotal": 24.0, "tax_override": 0})
    s = exp.summary(db)
    assert s["count"] == 2 and s["total"] == 34.0
    cats = {c["key"]: c["total"] for c in s["by_category"]}
    assert cats["Supplies"] == 10.0 and cats["Software"] == 24.0
    # date range excludes the January row
    s2 = exp.summary(db, date_from="2026-03-01")
    assert s2["count"] == 1 and s2["total"] == 24.0
