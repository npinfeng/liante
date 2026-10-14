"""Archive old terminal tasks with a verified ZIP before removing live rows.

Dry run is the default. Run this during a maintenance window with --apply.
Qualification-linked tasks are retained so grading evidence remains auditable.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import closing

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import LOG_DIR, OUTPUT_DIR


TERMINAL = ("COMPLETED", "FAILED", "CANCELLED", "REVIEW_REQUIRED", "TIMEOUT")


def archive_terminal_tasks(output_dir: Path, log_dir: Path, days: int = 365,
                           apply: bool = False) -> dict[str, int]:
    if days < 1:
        raise ValueError("days must be positive")
    output_dir, log_dir = Path(output_dir), Path(log_dir)
    task_dir, task_logs = output_dir / "tasks", log_dir / "tasks"
    index_path = task_dir / "index.sqlite3"
    if not index_path.is_file():
        return {"eligible": 0, "archived": 0, "protected": 0}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    protected = set()
    qualification_path = output_dir / "qualifications.sqlite3"
    if qualification_path.is_file():
        with closing(sqlite3.connect(f'file:{qualification_path.resolve().as_posix()}?mode=ro',uri=True)) as db:
            for aoi_id, stage2_id in db.execute("SELECT aoi_task_id,stage2_task_id FROM qualifications"):
                protected.update((aoi_id, stage2_id))
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='reviews'").fetchone():
                protected.update(r[0] for r in db.execute('SELECT stage3_task_id FROM reviews WHERE stage3_task_id IS NOT NULL'))
    archived = eligible = protected_count = 0
    with sqlite3.connect(index_path, timeout=30) as db:
        if apply:
            db.execute("""CREATE TABLE IF NOT EXISTS archived_tasks (
                task_id TEXT PRIMARY KEY, archive_path TEXT NOT NULL, archived_at TEXT NOT NULL)""")
        rows = db.execute("""SELECT task_id,updated_at FROM tasks WHERE status IN (?,?,?,?,?)
                             AND updated_at < ? ORDER BY updated_at""", (*TERMINAL, cutoff)).fetchall()
        for task_id, updated_at in rows:
            if task_id in protected:
                protected_count += 1
                continue
            task_file = task_dir / f"{task_id}.json"
            payload=db.execute('SELECT task_json FROM tasks WHERE task_id=?',(task_id,)).fetchone()[0]
            if not payload:
                continue
            eligible += 1
            if not apply:
                continue
            task_log = task_logs / f"{task_id}.jsonl"
            year = updated_at[:4]
            target = output_dir / "archive" / year / f"{task_id}.zip"
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".tmp")
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("task.json",payload)
                if task_log.is_file():
                    archive.write(task_log, "task_logs.jsonl")
                prediction_lines = [row[0] for row in db.execute(
                    """SELECT prediction_json FROM prediction_rows WHERE task_id=?
                       ORDER BY file_index,channel,row_num""", (task_id,))]
                archive.writestr("predictions.jsonl", "\n".join(prediction_lines) + ("\n" if prediction_lines else ""))
                archive.writestr("manifest.json", json.dumps({
                    "task_id": task_id, "updated_at": updated_at,
                    "prediction_rows": len(prediction_lines)}, ensure_ascii=False))
            with zipfile.ZipFile(temporary) as archive:
                if archive.testzip() is not None:
                    temporary.unlink(missing_ok=True)
                    raise IOError(f"archive verification failed: {task_id}")
            os.replace(temporary, target)
            with db:
                db.execute("DELETE FROM prediction_rows WHERE task_id=?", (task_id,))
                db.execute("DELETE FROM prediction_usage WHERE task_id=?", (task_id,))
                db.execute("DELETE FROM events WHERE task_id=?", (task_id,))
                db.execute("DELETE FROM tasks WHERE task_id=?", (task_id,))
                db.execute("INSERT OR REPLACE INTO archived_tasks VALUES (?,?,?)",
                           (task_id, str(target), datetime.now(timezone.utc).isoformat()))
            task_file.unlink(missing_ok=True)
            task_log.unlink(missing_ok=True)
            archived += 1
    return {"eligible": eligible, "archived": archived, "protected": protected_count}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(archive_terminal_tasks(OUTPUT_DIR, LOG_DIR, args.days, args.apply)))
