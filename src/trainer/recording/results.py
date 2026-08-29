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
    fit_file: str | None = None  # basename of the FIT in the rides dir


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
        results.sort(key=lambda r: r.started_at_unix)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps([asdict(r) for r in results], indent=2))
        except Exception:  # noqa: BLE001
            log.exception("Failed to write results log")


# ---------------------------------------------------------------------------
# Backfill: import FIT files recorded before the results log existed
# ---------------------------------------------------------------------------


def _records_from_fit(path: Path) -> tuple[list[Record], float]:
    """Reconstruct 1 Hz records (power/HR/distance) from one of our FIT files.

    We write one lap per workout step, so step_idx is set to the lap ORDINAL
    (0, 1, 2, … in ride order — not the plan index, since skips jump around);
    records outside any lap get step_idx = -1.
    """
    from fit_tool.fit_file import FitFile
    from fit_tool.profile.messages.lap_message import LapMessage
    from fit_tool.profile.messages.record_message import RecordMessage

    fit = FitFile.from_file(str(path))
    records: list[Record] = []
    ts_ms: list[float] = []
    laps: list[tuple[float, float]] = []  # (start_ms, end_ms) in ride order
    start_ms: float | None = None
    for frame in fit.records:
        msg = frame.message
        if isinstance(msg, LapMessage):
            if msg.start_time is not None and msg.timestamp is not None:
                laps.append((float(msg.start_time), float(msg.timestamp)))
            continue
        if not isinstance(msg, RecordMessage) or msg.timestamp is None:
            continue
        if start_ms is None:
            start_ms = float(msg.timestamp)
        ts_ms.append(float(msg.timestamp))
        records.append(
            Record(
                t_s=(float(msg.timestamp) - start_ms) / 1000.0,
                power_w=int(msg.power) if msg.power is not None else None,
                cadence_rpm=int(msg.cadence) if msg.cadence is not None else None,
                speed_kph=float(msg.speed) * 3.6 if msg.speed is not None else None,
                hr_bpm=int(msg.heart_rate) if msg.heart_rate is not None else None,
                distance_m=float(msg.distance or 0.0),
                target_w=None,
                step_idx=-1,
            )
        )
    if start_ms is None:
        raise ValueError(f"no record messages in {path.name}")
    for rec, t in zip(records, ts_ms):
        for ordinal, (lap_start, lap_end) in enumerate(laps):
            if lap_start <= t <= lap_end:
                rec.step_idx = ordinal
                break
    return records, start_ms / 1000.0


def _name_from_fit_filename(filename: str) -> str:
    """'20260517-162843__sst-4x15.fit' -> 'sst-4x15' (slug; original name is gone)."""
    stem = Path(filename).stem
    return stem.split("__", 1)[1] if "__" in stem else stem


def backfill_from_fit_dir(results_log: ResultsLog, rides_dir: Path) -> int:
    """Add any FIT file in `rides_dir` that the log doesn't know yet. Returns count."""
    known = {r.fit_file for r in results_log.load() if r.fit_file}
    imported = 0
    for p in sorted(rides_dir.glob("*.fit")):
        if p.name in known:
            continue
        try:
            records, start_unix = _records_from_fit(p)
        except Exception:  # noqa: BLE001
            log.warning("Could not import %s into the results log", p.name)
            continue
        result = summarize(_name_from_fit_filename(p.name), start_unix, records)
        result.fit_file = p.name
        results_log.append(result)
        imported += 1
    return imported
