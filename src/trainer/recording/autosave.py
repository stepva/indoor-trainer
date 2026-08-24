"""Crash-safe ride autosave.

While a ride is running the recorder's records are periodically dumped to a
hidden JSON file in the rides dir. A clean finish deletes it; if the app
crashes or freezes, the next startup finds the file, rebuilds the FIT and
results entry from it, and removes it. Losing a ride now costs at most one
autosave interval of data.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict
from pathlib import Path

from ..workout.model import Workout
from .fit_writer import write_fit
from .recorder import Record
from .results import ResultsLog, summarize

log = logging.getLogger(__name__)

AUTOSAVE_INTERVAL_S = 60


def _slug(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return s or "ride"


def autosave_path(rides_dir: Path, workout_name: str, started_at_unix: float) -> Path:
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime(started_at_unix))
    return rides_dir / f".autosave__{ts}__{_slug(workout_name)}.json"


def write_autosave(
    path: Path,
    *,
    workout_name: str,
    ftp_w: int | None,
    started_at_unix: float,
    records: list[Record],
) -> None:
    payload = {
        "workout_name": workout_name,
        "ftp_w": ftp_w,
        "started_at_unix": started_at_unix,
        "records": [asdict(r) for r in records],
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload))
    tmp.replace(path)  # atomic — a crash mid-write never corrupts the autosave


def clear_autosave(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
        path.with_suffix(".tmp").unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        log.warning("Could not remove autosave %s", path)


def recover_autosaves(rides_dir: Path, results_log: ResultsLog) -> list[Path]:
    """Turn leftover autosaves into FIT files + results entries.

    Returns the list of recovered FIT paths (empty if there was nothing).
    """
    recovered: list[Path] = []
    for p in sorted(rides_dir.glob(".autosave__*.json")):
        try:
            data = json.loads(p.read_text())
            records = [Record(**r) for r in data["records"]]
            name = data["workout_name"]
            started = float(data["started_at_unix"])
            if not records:
                clear_autosave(p)
                continue
            ts = time.strftime("%Y%m%d-%H%M%S", time.localtime(started))
            out = rides_dir / f"{ts}__{_slug(name)}.fit"
            if not out.exists():
                write_fit(
                    out_path=out,
                    workout=Workout(name=name, ftp_w=data.get("ftp_w")),
                    started_at_unix=started,
                    records=records,
                )
            result = summarize(name, started, records)
            result.fit_file = out.name
            known = {r.fit_file for r in results_log.load() if r.fit_file}
            if out.name not in known:
                results_log.append(result)
            recovered.append(out)
            clear_autosave(p)
            log.info("Recovered crashed ride %s -> %s", p.name, out.name)
        except Exception:  # noqa: BLE001
            log.exception("Failed to recover autosave %s", p.name)
    return recovered
