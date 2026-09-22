"""Usage: python audit_inventory.py --output ../audit-output/inventory.json

Opens SQLite in read-only mode. Does not import app.main or run migrations.
"""
import argparse
import json
from pathlib import Path
import sqlite3

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.services.reconciliation import audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path(__file__).parent / "data" / "sortswift.db")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    path = args.database.resolve()
    uri = path.as_uri() + "?mode=ro"
    # A real SQLite read transaction keeps every check on the same snapshot.
    def connect():
        connection = sqlite3.connect(uri, uri=True)
        connection.execute("BEGIN")
        return connection
    engine = create_engine("sqlite://", creator=connect)
    with Session(engine) as db:
        report = audit(db)
        report["database"] = str(path)
        report["sqlite_integrity"] = [r[0] for r in db.connection().exec_driver_sql("PRAGMA quick_check")]
        report["foreign_key_violations"] = [list(r) for r in db.connection().exec_driver_sql("PRAGMA foreign_key_check")]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    print(f"Read-only report: {args.output.resolve()}")


if __name__ == "__main__":
    main()
