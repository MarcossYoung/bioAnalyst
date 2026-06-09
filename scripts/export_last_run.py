"""Export a Nullifier run from the local SQLite run store.

By default this exports the latest run from ~/.nullifier/runs.db into the
same ~/.nullifier directory as a self-contained JSON file.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any


DEFAULT_DB_PATH = Path.home() / ".nullifier" / "runs.db"


def local_iso(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).astimezone().isoformat()


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def load_run(cursor: sqlite3.Cursor, run_id: str | None) -> sqlite3.Row:
    if run_id:
        run = cursor.execute(
            "select rowid, * from runs where id = ?",
            (run_id,),
        ).fetchone()
    else:
        run = cursor.execute(
            "select rowid, * from runs order by created_at desc limit 1",
        ).fetchone()

    if run is None:
        selector = f"run id {run_id!r}" if run_id else "latest run"
        raise SystemExit(f"No {selector} found in the database.")

    return run


def make_output_path(output_dir: Path, run: sqlite3.Row) -> Path:
    run_id = run["id"]
    created = local_iso(run["created_at"]) or run_id
    timestamp = created.replace(":", "").replace("-", "")[:15]
    return output_dir / f"run_{timestamp}_{run_id}_export.json"


def export_run(db_path: Path, output_dir: Path | None, run_id: str | None) -> Path:
    if not db_path.exists():
        raise SystemExit(f"Run database does not exist: {db_path}")

    output_dir = output_dir or db_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row

    try:
        cursor = connection.cursor()
        run = load_run(cursor, run_id)
        run_id = run["id"]

        events = [
            row_to_dict(row)
            for row in cursor.execute(
                """
                select seq, type, payload_json, ts
                from run_events
                where run_id = ?
                order by seq
                """,
                (run_id,),
            )
        ]
        event_counts = [
            row_to_dict(row)
            for row in cursor.execute(
                """
                select type, count(*) as count
                from run_events
                where run_id = ?
                group by type
                order by count desc, type
                """,
                (run_id,),
            )
        ]

        payload = {
            "exported_at_local": dt.datetime.now().astimezone().isoformat(),
            "database": str(db_path),
            "run": row_to_dict(run),
            "created_at_local": local_iso(run["created_at"]),
            "completed_at_local": local_iso(run["completed_at"]),
            "event_count": len(events),
            "event_counts": event_counts,
            "events": events,
        }

        output_path = make_output_path(output_dir, run)
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return output_path
    finally:
        connection.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export the latest Nullifier run, or a specific run, to JSON.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"Path to runs.db. Defaults to {DEFAULT_DB_PATH}.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for the export JSON. Defaults to the database directory.",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Specific run id to export. Defaults to the latest run by created_at.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_path = export_run(
        db_path=args.db.expanduser(),
        output_dir=args.output_dir.expanduser() if args.output_dir else None,
        run_id=args.run_id,
    )
    print(output_path)


if __name__ == "__main__":
    main()
