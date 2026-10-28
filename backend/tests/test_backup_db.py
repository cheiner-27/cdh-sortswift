import os
import sqlite3
import zipfile

from backup_db import backup


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.execute("INSERT INTO t VALUES ('saved')")
    conn.commit()
    return conn


def _rows_in(zip_path, tmp_path):
    with zipfile.ZipFile(zip_path) as zf:
        assert zf.namelist() == ["sortswift.db"]
        zf.extract("sortswift.db", tmp_path / "out")
    conn = sqlite3.connect(tmp_path / "out" / "sortswift.db")
    try:
        return [r[0] for r in conn.execute("SELECT v FROM t")]
    finally:
        conn.close()


def test_backup_includes_writes_still_in_the_wal(tmp_path):
    db = tmp_path / "sortswift.db"
    conn = _make_db(db)  # left open: the committed row sits in sortswift.db-wal
    try:
        assert (tmp_path / "sortswift.db-wal").exists()
        made = backup(db, tmp_path / "drive")
    finally:
        conn.close()
    assert made is not None
    assert _rows_in(made, tmp_path) == ["saved"]


def test_unchanged_database_is_not_backed_up_again(tmp_path):
    db = tmp_path / "sortswift.db"
    _make_db(db).close()
    assert backup(db, tmp_path / "drive") is not None
    assert backup(db, tmp_path / "drive") is None
    assert len(list((tmp_path / "drive").glob("*.zip"))) == 1


def test_changed_database_is_backed_up_and_old_copies_pruned(tmp_path):
    db = tmp_path / "sortswift.db"
    _make_db(db).close()
    drive = tmp_path / "drive"
    drive.mkdir()
    for day in range(1, 13):
        old = drive / f"sortswift-202601{day:02d}-120000.zip"
        old.write_bytes(b"old")
        os.utime(old, (1, 1))  # older than the database

    made = backup(db, drive, keep=10)

    kept = sorted(p.name for p in drive.glob("*.zip"))
    assert len(kept) == 10
    assert kept[-1] == made.name
    assert "sortswift-20260101-120000.zip" not in kept
    assert _rows_in(made, tmp_path) == ["saved"]
