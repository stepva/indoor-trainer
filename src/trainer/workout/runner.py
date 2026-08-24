"""Workout runner: drives the trainer's ERG target through the workout steps."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable

from .model import Workout

log = logging.getLogger(__name__)


class State(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    FINISHED = "finished"


@dataclass
class TickResult:
    """Outcome of a single 1 Hz tick."""
    target_w: int | None
    step_changed: bool
    finished: bool


BIAS_LIMIT_W = 100
BIAS_MIN_TARGET_W = 30


class WorkoutRunner:
    """Pure logic — UI calls tick() every second.

    The BLE write to set the trainer target is dispatched fire-and-forget on
    the asyncio loop so `tick()` itself stays synchronous (lets the caller
    detect step changes without waiting on a coroutine).
    """

    def __init__(
        self,
        workout: Workout,
        set_target_cb: Callable[[int], Awaitable[None]],
    ) -> None:
        self.workout = workout
        self._set_target = set_target_cb
        self.state = State.IDLE
        self.elapsed_s: int = 0
        self.step_idx: int = 0
        self.step_elapsed_s: int = 0
        self.last_target_w: int | None = None
        self.bias_w: int = 0

    @property
    def total_s(self) -> int:
        return self.workout.total_duration_s

    @property
    def current_step(self):
        if 0 <= self.step_idx < len(self.workout.steps):
            return self.workout.steps[self.step_idx]
        return None

    @property
    def step_remaining_s(self) -> int:
        step = self.current_step
        if step is None:
            return 0
        return max(0, step.duration_s - self.step_elapsed_s)

    def set_bias(self, bias_w: int) -> int:
        """Set the whole-workout watt bias (clamped). Returns the applied value.

        The next tick re-sends the trainer target, so a mid-step change takes
        effect within a second.
        """
        bias = max(-BIAS_LIMIT_W, min(BIAS_LIMIT_W, int(bias_w)))
        if bias != self.bias_w:
            self.bias_w = bias
            self.last_target_w = None
        return self.bias_w

    def biased_watts(self, step, t_in_step_s: float) -> int:
        """Step target at t with the ride-wide bias applied (ERG steps only)."""
        return max(BIAS_MIN_TARGET_W, step.watts_at(t_in_step_s) + self.bias_w)

    def start(self) -> None:
        self.state = State.RUNNING
        self.elapsed_s = 0
        self.step_idx = 0
        self.step_elapsed_s = 0
        self.last_target_w = None

    def pause(self) -> None:
        if self.state == State.RUNNING:
            self.state = State.PAUSED

    def resume(self) -> None:
        if self.state == State.PAUSED:
            self.state = State.RUNNING
            self.last_target_w = None  # force re-send of target on next tick

    def finish(self) -> None:
        self.state = State.FINISHED

    def skip_step(self) -> bool:
        """Jump to the next step. Returns True if a step boundary was crossed."""
        if self.step_idx < len(self.workout.steps) - 1:
            self.step_idx += 1
            self.step_elapsed_s = 0
            self.last_target_w = None
            return True
        self.finish()
        return False

    def skip_to_last_step(self) -> bool:
        """Jump straight to the final step (the cooldown on most workouts).

        Lets the rider bail out of an FTP ramp (or any workout) without
        clicking through every remaining step. Returns True if it jumped.
        """
        last = len(self.workout.steps) - 1
        if last < 0 or self.step_idx >= last:
            return False
        self.step_idx = last
        self.step_elapsed_s = 0
        self.last_target_w = None
        return True

    def tick(self) -> TickResult:
        """Advance one second. Returns target, whether the step changed, and finish flag."""
        if self.state != State.RUNNING:
            return TickResult(self.last_target_w, False, self.state == State.FINISHED)

        step = self.current_step
        if step is None:
            self.finish()
            return TickResult(None, False, True)

        step_changed = False
        while step is not None and self.step_elapsed_s >= step.duration_s:
            self.step_idx += 1
            self.step_elapsed_s = 0
            step_changed = True
            if self.step_idx >= len(self.workout.steps):
                self.finish()
                return TickResult(self.last_target_w, step_changed, True)
            step = self.current_step
            self.last_target_w = None

        target: int | None = None
        if step.is_erg():
            target = self.biased_watts(step, self.step_elapsed_s)
            if target != self.last_target_w:
                try:
                    asyncio.ensure_future(self._set_target_safe(int(target)))
                except RuntimeError:
                    pass
                self.last_target_w = target

        self.elapsed_s += 1
        self.step_elapsed_s += 1
        return TickResult(target, step_changed, False)

    async def _set_target_safe(self, watts: int) -> None:
        try:
            await self._set_target(watts)
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to set target power: %s", exc)
