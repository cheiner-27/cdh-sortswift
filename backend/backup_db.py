"""Usage: python backup_db.py [--dest DIR] [--keep N]

Zips a copy of sortswift.db into Google Drive and keeps the newest N copies.
run.ps1 calls this on launch and on exit, while the app is stopped. A run with
nothing new since the newest copy does nothing, so sessions that only browse
don't push real backups out of the window.

The destination is --dest, else $SORTSWIFT_BACKUP_DIR, else a "SortSwift
Backups" folder in Google Drive for desktop's "My Drive".
"""
import argparse
import os
import shutil
import sqlite3
import string
import tempfile
import time
import zipfile
from pathlib import Path

from app.db import DB_PATH

KEEP = 10


def find_drive_folder():
    # Drive for desktop puts "My Drive" at the root of its own drive letter when
    # streaming. Mirroring uses a folder you pick; only the user folder is
    # checked, so set SORTSWIFT_BACKUP_DIR for anywhere else.
    roots = [Path.home()] + [Path(f"{letter}:/") for letter in string.ascii_uppercase]
    for root in roots:
        if (root / "My Drive").is_dir():
            return root / "My Drive" / "SortSwift Backups"
    return None


def backup(db_path, dest_dir, keep=KEEP):
    """Returns the new zip's path, or None when the newest copy is already current."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(dest_dir.glob("sortswift-*.zip"))
    # An unclean exit (window closed with the X) leaves recent writes in the
    # -wal file rather than the .db, so its time counts as a change too.
    changed = max(p.stat().st_mtime for p in (db_path, Path(f"{db_path}-wal")) if p.exists())
    if existing and existing[-1].stat().st_mtime >= changed:
        return None

    target = dest_dir / f"sortswift-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    with tempfile.TemporaryDirectory() as tmp:
        # SQLite's backup API rather than a file copy: it also reads the -wal.
        snapshot = Path(tmp) / db_path.name
        source, copy = sqlite3.connect(db_path), sqlite3.connect(snapshot)
        try:
            source.backup(copy)
        finally:
            copy.close()
            source.close()
        # Build the zip locally, then move it, so Drive never uploads half a file.
        staged = Path(tmp) / target.name
        with zipfile.ZipFile(staged, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(snapshot, db_path.name)
        shutil.move(staged, target)

    for old in sorted(dest_dir.glob("sortswift-*.zip"))[:-keep]:
        old.unlink()
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dest", type=Path, default=os.environ.get("SORTSWIFT_BACKUP_DIR"))
    parser.add_argument("--keep", type=int, default=KEEP)
    args = parser.parse_args()
    dest = args.dest or find_drive_folder()
    if dest is None:
        print("WARNING: Google Drive not found - database NOT backed up.")
        print("         Install Google Drive for desktop and sign in, or set SORTSWIFT_BACKUP_DIR.")
        return 1
    if not DB_PATH.exists():
        print("No database yet - nothing to back up.")
        return 0
    try:
        made = backup(DB_PATH, dest, args.keep)
    except (OSError, sqlite3.Error) as e:
        print(f"WARNING: database backup failed: {e}")
        return 1
    print(f"Backed up database to {made}" if made else "Database backup already up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
