"""On-disk workout library: one JSON file per workout in `workouts/`."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .model import Workout


def _slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()
    return s or "workout"


class WorkoutLibrary:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[Workout]:
        out: list[Workout] = []
        for p in sorted(self.root.glob("*.json")):
            try:
                out.append(Workout.from_dict(json.loads(p.read_text())))
            except Exception:  # noqa: BLE001
                continue
        return out

    def path_for(self, w: Workout) -> Path:
        return self.root / f"{_slug(w.name)}.json"

    def save(self, w: Workout, *, old_name: str | None = None) -> Path:
        if old_name and old_name != w.name:
            old_path = self.root / f"{_slug(old_name)}.json"
            if old_path.exists():
                old_path.unlink()
        p = self.path_for(w)
        p.write_text(json.dumps(w.to_dict(), indent=2))
        return p

    def delete(self, w: Workout) -> None:
        p = self.path_for(w)
        if p.exists():
            p.unlink()

    def seed_if_empty(self) -> None:
        if any(self.root.glob("*.json")):
            return
        from .model import Step

        def steady(label: str, dur: int, w: int) -> Step:
            return Step(kind="steady", duration_s=dur, target_w=w, label=label)

        examples = [
            Workout(
                name="20 min Sweet Spot",
                ftp_w=200,
                steps=[
                    steady("Warmup", 5 * 60, 110),
                    steady("Sweet Spot", 20 * 60, 180),
                    steady("Cooldown", 5 * 60, 100),
                ],
            ),
            Workout(
                name="4x4 VO2",
                ftp_w=200,
                steps=[
                    steady("Warmup", 10 * 60, 110),
                    *[
                        s
                        for _ in range(4)
                        for s in (
                            steady("VO2", 4 * 60, 240),
                            steady("Recovery", 3 * 60, 110),
                        )
                    ],
                    steady("Cooldown", 5 * 60, 100),
                ],
            ),
            Workout(
                name="Endurance 45",
                ftp_w=200,
                steps=[
                    steady("Warmup", 5 * 60, 110),
                    steady("Endurance", 35 * 60, 140),
                    steady("Cooldown", 5 * 60, 100),
                ],
            ),
        ]
        for w in examples:
            self.save(w)
