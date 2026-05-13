"""Workout data model."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Literal


StepKind = Literal["steady", "ramp", "free"]


@dataclass
class Step:
    kind: StepKind = "steady"
    duration_s: int = 60
    target_w: int | None = 100      # for steady
    ramp_from_w: int | None = None  # for ramp
    ramp_to_w: int | None = None
    label: str = ""

    def watts_at(self, t_in_step_s: float) -> int:
        """Target watts at offset t (seconds) within this step."""
        if self.kind == "steady":
            return int(self.target_w or 0)
        if self.kind == "ramp":
            a = self.ramp_from_w or 0
            b = self.ramp_to_w or 0
            d = max(self.duration_s, 1)
            frac = max(0.0, min(1.0, t_in_step_s / d))
            return int(round(a + (b - a) * frac))
        # free ride: no ERG target. Caller should treat None as "do not change".
        return 0

    def is_erg(self) -> bool:
        return self.kind in ("steady", "ramp")


@dataclass
class Workout:
    name: str = "New Workout"
    ftp_w: int | None = None
    steps: list[Step] = field(default_factory=list)

    @property
    def total_duration_s(self) -> int:
        return sum(s.duration_s for s in self.steps)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Workout":
        return cls(
            name=d.get("name", "Untitled"),
            ftp_w=d.get("ftp_w"),
            steps=[Step(**s) for s in d.get("steps", [])],
        )
