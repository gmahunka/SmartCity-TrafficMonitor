"""Create or restore a consistent SQLite snapshot at a new path."""

import argparse
import sqlite3
from contextlib import closing
from pathlib import Path

from .db import connect_readonly


def snapshot(source: Path, destination: Path) -> dict[str, int]:
    with closing(connect_readonly(source)) as src:
        # Reserve the path so a backup or restore cannot overwrite an existing database.
        with destination.open("xb"):
            pass
        try:
            with closing(sqlite3.connect(destination)) as dst:
                src.backup(dst, pages=256)
                result = dst.execute("PRAGMA quick_check").fetchall()
                if result != [("ok",)]:
                    raise sqlite3.DatabaseError(f"Snapshot integrity check failed: {result}")
                tables = {name for name, in dst.execute("SELECT name FROM sqlite_schema WHERE type = 'table'")}
                if "traffic_counts" not in tables:
                    raise sqlite3.DatabaseError("Source does not contain a traffic_counts table")
                return {
                    table: dst.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in ("traffic_events", "traffic_counts", "traffic_forecasts")
                    if table in tables
                }
        except BaseException:
            destination.unlink()
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        counts = snapshot(args.source, args.destination)
    except (OSError, sqlite3.Error) as error:
        parser.exit(1, f"Snapshot failed: {error}\n")
    print(f"Saved {args.destination}")
    for table, count in counts.items():
        print(f"{table}: {count}")


if __name__ == "__main__":
    main()
