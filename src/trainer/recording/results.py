"""Ride results: post-ride summary stats and a small on-disk history log.

The log is one JSON file (`results.json` in the rides dir) holding a list of
RideResult dicts, newest last. FTP is estimated only for rides whose workout
name looks like an FTP/ramp test, using the ramp-test convention:
FTP = 0.75 x best 1-minute power.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .recorder import Record

log = logging.getLogger(__name__)

_FTP_TEST_NAME_RE = re.compile(r"ramp|ftp", re.IGNORECASE)


def is_ftp_test_name(name: str) -> bool:
    return bool(_FTP_TEST_NAME_RE.search(name))


def best_rolling_power_w(records: list[Record], window_s: int = 60) -> int | None:
    """Best rolling average power over `window_s`. Records are 1 Hz."""
    powers = [float(r.power_w or 0) for r in records]
    if len(powers) < window_s:
        return None
    best = cur = sum(powers[:window_s])
    for i in range(window_s, len(powers)):
        cur += powers[i] - powers[i - window_s]
        best = max(best, cur)
    return int(round(best / window_s))


@dataclass
class RideResult:
    started_at_unix: float
    workout_name: str
    duration_s: int
    distance_m: float
    avg_power_w: int | None
    avg_hr_bpm: int | None
    best_1min_w: int | None
    ftp_estimate_w: int | None  # FTP/ramp tests only: 0.75 x best 1 min


def summarize(workout_name: str, started_at_unix: float, records: list[Record]) -> RideResult:
    powers = [r.power_w for r in records if r.power_w is not None]
    hrs = [r.hr_bpm for r in records if r.hr_bpm is not None]
    best1 = best_rolling_power_w(records)
    ftp = None
    if best1 is not None and is_ftp_test_name(workout_name):
        ftp = int(round(best1 * 0.75))
    return RideResult(
        started_at_unix=started_at_unix,
        workout_name=workout_name,
        duration_s=int(records[-1].t_s) if records else 0,
        distance_m=records[-1].distance_m if records else 0.0,
        avg_power_w=int(round(sum(powers) / len(powers))) if powers else None,
        avg_hr_bpm=int(round(sum(hrs) / len(hrs))) if hrs else None,
        best_1min_w=best1,
        ftp_estimate_w=ftp,
    )


class ResultsLog:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> list[RideResult]:
        try:
            raw = json.loads(self.path.read_text())
        except FileNotFoundError:
            return []
        except Exception:  # noqa: BLE001
            log.warning("Could not read results log at %s", self.path)
            return []
        out: list[RideResult] = []
        for d in raw if isinstance(raw, list) else []:
            try:
                out.append(RideResult(**d))
            except TypeError:
                continue
        return out

    def append(self, result: RideResult) -> None:
        results = self.load()
        results.append(result)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps([asdict(r) for r in results], indent=2))
        except Exception:  # noqa: BLE001
            log.exception("Failed to write results log")
