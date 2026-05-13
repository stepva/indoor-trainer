"""1 Hz session recorder.

Holds the latest values from the trainer and HRM, and produces one record
per second with elapsed time, distance, and the runner's current target.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..physics import virtual_speed_kph


@dataclass
class Record:
    t_s: float            # elapsed seconds since start
    power_w: int | None
    cadence_rpm: int | None
    speed_kph: float | None
    hr_bpm: int | None
    distance_m: float
    target_w: int | None
    step_idx: int


@dataclass
class Recorder:
    """Single-session recorder. Call tick() once per second while RUNNING."""

    started_at_unix: float = 0.0
    elapsed_s: float = 0.0
    distance_m: float = 0.0
    records: list[Record] = field(default_factory=list)

    # latest sensor values
    last_power: int | None = None
    last_cadence: float | None = None
    last_speed_kph: float | None = None
    last_hr: int | None = None

    # runner state
    current_target_w: int | None = None
    current_step_idx: int = 0

    def start(self) -> None:
        self.started_at_unix = time.time()
        self.elapsed_s = 0.0
        self.distance_m = 0.0
        self.records = []

    def tick(self, dt_s: float = 1.0) -> Record:
        # Compute virtual "flat road" speed from current power. The trainer's
        # own speed reading depends on its gear ratio (constant with a Zwift
        # cog), so we override it with physics-based speed here.
        speed_kph = virtual_speed_kph(self.last_power)
        self.last_speed_kph = speed_kph
        self.distance_m += (speed_kph * 1000.0 / 3600.0) * dt_s
        self.elapsed_s += dt_s
        rec = Record(
            t_s=self.elapsed_s,
            power_w=self.last_power,
            cadence_rpm=int(self.last_cadence) if self.last_cadence is not None else None,
            speed_kph=speed_kph,
            hr_bpm=self.last_hr,
            distance_m=self.distance_m,
            target_w=self.current_target_w,
            step_idx=self.current_step_idx,
        )
        self.records.append(rec)
        return rec
