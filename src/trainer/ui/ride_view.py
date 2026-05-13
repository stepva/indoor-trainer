"""Ride view: connect to devices, run a workout, record, export FIT.

Layout (top → bottom):
  • Header bar with title, status pills, connect buttons
  • Status line
  • 5 equal-size metric tiles in one row (POWER · HR · CADENCE · BLOCK LEFT · ELAPSED)
  • Workout profile bar with progress cursor
  • Step caption
  • Start / Pause / Skip / Finish controls
"""
from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Callable

from bleak.backends.device import BLEDevice
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..ble.ftms import BikeSample, FtmsClient
from ..ble.hrm import HrmClient
from ..recording.fit_writer import write_fit
from ..recording.recorder import Recorder
from ..workout.model import Workout
from ..workout.runner import State, WorkoutRunner
from . import theme
from .widgets import MetricTile, WorkoutBar, play_system_sound

log = logging.getLogger(__name__)


def _slug(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return s or "ride"


def _fmt_time(secs: float) -> str:
    s = int(secs)
    if s >= 3600:
        return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"
    return f"{s // 60:02d}:{s % 60:02d}"


def _fmt_block_left(secs: float) -> str:
    s = max(0, int(secs))
    return f"{s // 60}:{s % 60:02d}"


# ---------------------------------------------------------------------------
# Connection status pill
# ---------------------------------------------------------------------------


class _StatusPill(QFrame):
    def __init__(self, label: str) -> None:
        super().__init__()
        self.setObjectName("card")
        self.setFrameShape(QFrame.NoFrame)
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 6, 12, 6)
        h.setSpacing(8)
        self._dot = QLabel("●")
        self._dot.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 14px;")
        self._label = QLabel(label)
        self._label.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 13px;")
        h.addWidget(self._dot)
        h.addWidget(self._label)

    def set_connected(self, ok: bool) -> None:
        self._dot.setStyleSheet(
            f"color: {theme.ACCENT_CADENCE if ok else theme.TEXT_MUTED}; font-size: 14px;"
        )


# ---------------------------------------------------------------------------
# Ride view
# ---------------------------------------------------------------------------


class RideView(QWidget):
    _bike_sample = Signal(object)
    _hr_value = Signal(int)
    _state_changed = Signal(str)
    _finished_with = Signal(object)

    def __init__(
        self,
        rides_dir: Path,
        on_back: Callable[[], None],
    ) -> None:
        super().__init__()
        self.rides_dir = rides_dir
        self.rides_dir.mkdir(parents=True, exist_ok=True)
        self._on_back = on_back

        self.workout: Workout | None = None
        self.ftms = FtmsClient()
        self.hrm = HrmClient()
        self.recorder = Recorder()
        self.runner: WorkoutRunner | None = None

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

        # Pre-workout countdown
        self._countdown_left: int = 0
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._countdown_tick)

        self._build_ui()

        self._bike_sample.connect(self._on_bike_sample)
        self._hr_value.connect(self._on_hr)
        self._state_changed.connect(self.status.setText)
        self._finished_with.connect(self._show_finished_dialog)

    # ---- UI -------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 18)
        root.setSpacing(14)

        # Header
        top = QHBoxLayout()
        top.setSpacing(10)
        back = QPushButton("←")
        back.setFixedSize(40, 36)
        back.clicked.connect(self._handle_back)
        top.addWidget(back)
        self.title = QLabel("")
        self.title.setStyleSheet(
            f"color: {theme.TEXT}; font-size: 18px; font-weight: 600; background: transparent;"
        )
        top.addWidget(self.title)
        self.subtitle = QLabel("")
        self.subtitle.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 12px; background: transparent;"
        )
        top.addWidget(self.subtitle)
        top.addStretch(1)
        self.trainer_pill = _StatusPill("Trainer")
        self.hr_pill = _StatusPill("HR")
        top.addWidget(self.trainer_pill)
        top.addWidget(self.hr_pill)
        self.connect_trainer_btn = QPushButton("Connect trainer")
        self.connect_trainer_btn.clicked.connect(self._connect_trainer_clicked)
        top.addWidget(self.connect_trainer_btn)
        self.connect_hr_btn = QPushButton("Connect HR")
        self.connect_hr_btn.clicked.connect(self._connect_hr_clicked)
        top.addWidget(self.connect_hr_btn)
        root.addLayout(top)

        # Status line
        self.status = QLabel("Connect your trainer and (optional) HR sensor to begin.")
        self.status.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 13px;")
        root.addWidget(self.status)

        # Tiles page — 5 equal-size metric tiles in a row
        tiles_page = QWidget()
        tiles_row = QHBoxLayout(tiles_page)
        tiles_row.setContentsMargins(0, 0, 0, 0)
        tiles_row.setSpacing(12)
        self.t_power = MetricTile("Power", "watts", theme.ACCENT_POWER)
        self.t_hr = MetricTile("Heart rate", "bpm", theme.ACCENT_HR)
        self.t_cadence = MetricTile("Cadence", "rpm", theme.ACCENT_CADENCE)
        self.t_block = MetricTile("Block left", "m:ss", theme.ACCENT_TARGET, max_chars=5)
        self.t_elapsed = MetricTile("Elapsed", "h:mm:ss", theme.ACCENT_TIME, max_chars=5)
        for t in (self.t_power, self.t_hr, self.t_cadence, self.t_block, self.t_elapsed):
            t.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            tiles_row.addWidget(t, 1)

        # Countdown page — single massive tile filling the metric row's space
        countdown_page = QWidget()
        cd_layout = QHBoxLayout(countdown_page)
        cd_layout.setContentsMargins(0, 0, 0, 0)
        self.countdown_tile = MetricTile(
            "Get on the bike", "starting in", theme.ACCENT_TARGET, max_chars=2
        )
        self.countdown_tile.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        cd_layout.addWidget(self.countdown_tile)

        # Stack — defaults to tiles page; we flip to countdown on Start.
        self.metrics_stack = QStackedWidget()
        self.metrics_stack.addWidget(tiles_page)       # index 0
        self.metrics_stack.addWidget(countdown_page)   # index 1
        root.addWidget(self.metrics_stack, 1)

        # Workout profile bar (THE progress visualization)
        self.workout_bar = WorkoutBar()
        root.addWidget(self.workout_bar)

        # Step caption
        self.step_caption = QLabel("")
        self.step_caption.setStyleSheet(
            f"color: {theme.TEXT_DIM}; font-size: 13px; background: transparent;"
        )
        root.addWidget(self.step_caption)

        # Controls
        ctrl = QHBoxLayout()
        ctrl.addStretch(1)
        self.start_btn = QPushButton("Start  ▶")
        self.start_btn.setProperty("primary", True)
        self.start_btn.setFixedHeight(44)
        self.start_btn.setMinimumWidth(140)
        self.start_btn.clicked.connect(self._start)
        self.start_btn.setEnabled(False)
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.setFixedHeight(44)
        self.pause_btn.setMinimumWidth(110)
        self.pause_btn.clicked.connect(self._pause_resume)
        self.pause_btn.setEnabled(False)
        self.lap_btn = QPushButton("Skip step")
        self.lap_btn.setFixedHeight(44)
        self.lap_btn.setMinimumWidth(110)
        self.lap_btn.clicked.connect(self._skip_step)
        self.lap_btn.setEnabled(False)
        self.finish_btn = QPushButton("Finish  ⏹")
        self.finish_btn.setProperty("danger", True)
        self.finish_btn.setFixedHeight(44)
        self.finish_btn.setMinimumWidth(120)
        self.finish_btn.clicked.connect(self._finish)
        self.finish_btn.setEnabled(False)
        for b in (self.start_btn, self.pause_btn, self.lap_btn, self.finish_btn):
            ctrl.addWidget(b)
        root.addLayout(ctrl)

    # ---- workout lifecycle ---------------------------------------------

    def load_workout(self, w: Workout) -> None:
        self.workout = w
        mins = w.total_duration_s // 60
        self.title.setText(w.name)
        self.subtitle.setText(f"{mins} min · {len(w.steps)} steps")
        self.runner = WorkoutRunner(w, self._set_target_async)
        self.workout_bar.set_workout(w, ftp=w.ftp_w)
        self._refresh_buttons()
        self._update_step_caption()
        self._reset_tile_values()

    def _reset_tile_values(self) -> None:
        for t in (self.t_power, self.t_hr, self.t_cadence, self.t_block, self.t_elapsed):
            t.set_value("—")
        self.t_block.set_value("—")
        self.t_elapsed.set_value("0:00")

    def _refresh_buttons(self) -> None:
        connected = self.ftms.connected
        running = self.runner is not None and self.runner.state == State.RUNNING
        paused = self.runner is not None and self.runner.state == State.PAUSED
        idle = self.runner is not None and self.runner.state == State.IDLE
        counting = self._in_countdown
        self.start_btn.setEnabled(connected and idle and not counting)
        if counting:
            self.pause_btn.setEnabled(True)
            self.pause_btn.setText("Cancel")
        else:
            self.pause_btn.setEnabled(running or paused)
            self.pause_btn.setText("Resume" if paused else "Pause")
        self.lap_btn.setEnabled(running or paused)
        self.finish_btn.setEnabled(running or paused or counting)

    # ---- BLE: trainer ---------------------------------------------------

    def _connect_trainer_clicked(self) -> None:
        asyncio.ensure_future(self._connect_trainer())

    async def _connect_trainer(self) -> None:
        self.status.setText("Scanning for FTMS trainers…")
        try:
            devices = await FtmsClient.scan(timeout=6.0)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Scan failed", str(exc))
            return
        if not devices:
            QMessageBox.information(self, "No trainers", "No FTMS devices were found.")
            self.status.setText("No trainers found.")
            return
        device = await self._pick_device("Pick trainer", devices)
        if device is None:
            self.status.setText("Cancelled.")
            return
        self.status.setText(f"Connecting to {device.name or device.address}…")
        try:
            await self.ftms.connect(device, on_sample=self._bike_sample.emit)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Connect failed", str(exc))
            self.status.setText("Connection failed.")
            return
        self.status.setText(f"Trainer ready · power range {self.ftms.min_w}–{self.ftms.max_w} W.")
        self.connect_trainer_btn.setText("Trainer ✓")
        self.connect_trainer_btn.setEnabled(False)
        self.trainer_pill.set_connected(True)
        self._refresh_buttons()

    # ---- BLE: HR --------------------------------------------------------

    def _connect_hr_clicked(self) -> None:
        asyncio.ensure_future(self._connect_hr())

    async def _connect_hr(self) -> None:
        self.status.setText("Scanning for HR devices (put your watch into Broadcast HR)…")
        try:
            devices = await HrmClient.scan(timeout=8.0)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Scan failed", str(exc))
            return
        if not devices:
            QMessageBox.information(self, "No HR devices", "Couldn't find an HR device. Make sure your watch is broadcasting.")
            return
        device = await self._pick_device("Pick HR sensor", devices)
        if device is None:
            return
        try:
            await self.hrm.connect(device, on_hr=self._hr_value.emit)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "HR connect failed", str(exc))
            return
        self.connect_hr_btn.setText("HR ✓")
        self.connect_hr_btn.setEnabled(False)
        self.hr_pill.set_connected(True)
        self.status.setText("HR connected.")

    async def _pick_device(self, title: str, devices: list[BLEDevice]) -> BLEDevice | None:
        labels = [f"{d.name or '(unnamed)'}  ·  {d.address}" for d in devices]
        choice, ok = QInputDialog.getItem(self, title, "Device:", labels, 0, False)
        if not ok:
            return None
        return devices[labels.index(choice)]

    # ---- runner glue ----------------------------------------------------

    async def _set_target_async(self, watts: int) -> None:
        if self.ftms.connected:
            try:
                await self.ftms.set_target_power(watts)
            except Exception as exc:  # noqa: BLE001
                log.warning("Failed to send target: %s", exc)
        self.recorder.current_target_w = watts

    def _start(self) -> None:
        if not self.runner or not self.workout:
            return
        if not self.ftms.connected:
            QMessageBox.warning(self, "Not connected", "Connect the trainer first.")
            return
        # Begin a 15-second countdown so the rider can get on the bike.
        self._countdown_left = 15
        self.countdown_tile.set_value(str(self._countdown_left))
        self.metrics_stack.setCurrentIndex(1)
        self.status.setText("Get on the bike · starting in 15 s")
        self._countdown_timer.start()
        self._refresh_buttons()
        play_system_sound("Pop")

    def _countdown_tick(self) -> None:
        self._countdown_left -= 1
        if self._countdown_left > 0:
            self.countdown_tile.set_value(str(self._countdown_left))
            self.status.setText(f"Get on the bike · starting in {self._countdown_left} s")
            if self._countdown_left <= 3:
                play_system_sound("Tink")
            return
        # Countdown finished — start the workout for real.
        self._countdown_timer.stop()
        self.metrics_stack.setCurrentIndex(0)
        self.recorder.start()
        self.runner.start()
        self.recorder.current_step_idx = self.runner.step_idx
        self._timer.start()
        self._refresh_buttons()
        self.status.setText("Workout started · pedal!")
        play_system_sound("Glass")

    def _cancel_countdown(self) -> None:
        """Abort a pre-workout countdown back to the idle state."""
        self._countdown_timer.stop()
        self._countdown_left = 0
        self.metrics_stack.setCurrentIndex(0)
        self.status.setText("Countdown cancelled.")
        self._refresh_buttons()

    @property
    def _in_countdown(self) -> bool:
        return self._countdown_timer.isActive()

    def _pause_resume(self) -> None:
        if self._in_countdown:
            self._cancel_countdown()
            return
        if not self.runner:
            return
        if self.runner.state == State.RUNNING:
            self.runner.pause()
            self.status.setText("Paused.")
        elif self.runner.state == State.PAUSED:
            self.runner.resume()
            self.status.setText("Resumed.")
        self._refresh_buttons()

    def _skip_step(self) -> None:
        if self.runner:
            crossed = self.runner.skip_step()
            self.recorder.current_step_idx = self.runner.step_idx
            self._update_step_caption()
            if crossed:
                play_system_sound("Tink")

    def _finish(self) -> None:
        if not self.runner:
            return
        if self._in_countdown:
            self._cancel_countdown()
            return
        self._timer.stop()
        self.runner.finish()
        out: Path | None = None
        if self.recorder.records and self.workout:
            ts = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.recorder.started_at_unix))
            out = self.rides_dir / f"{ts}__{_slug(self.workout.name)}.fit"
            try:
                write_fit(
                    out_path=out,
                    workout=self.workout,
                    started_at_unix=self.recorder.started_at_unix,
                    records=self.recorder.records,
                )
            except Exception as exc:  # noqa: BLE001
                log.exception("FIT write failed")
                QMessageBox.warning(self, "FIT export failed", str(exc))
                out = None
        asyncio.ensure_future(self._teardown_ble())
        play_system_sound("Hero")
        self._refresh_buttons()
        self._finished_with.emit(out)

    async def _teardown_ble(self) -> None:
        try:
            await self.ftms.disconnect()
        except Exception:  # noqa: BLE001
            pass
        try:
            await self.hrm.disconnect()
        except Exception:  # noqa: BLE001
            pass

    def _show_finished_dialog(self, out_path: Path | None) -> None:
        if out_path is None:
            QMessageBox.information(self, "Finished", "Workout finished. No data was recorded.")
            return
        msg = QMessageBox(self)
        msg.setWindowTitle("Finished")
        secs = int(self.recorder.elapsed_s)
        km = self.recorder.distance_m / 1000.0
        msg.setText(
            f"Workout saved.\n\n"
            f"Duration: {secs // 60} min {secs % 60:02d} s\n"
            f"Distance: {km:.2f} km\n"
            f"FIT: {out_path}"
        )
        reveal = msg.addButton("Reveal in Finder", QMessageBox.ActionRole)
        msg.addButton(QMessageBox.Ok)
        msg.exec()
        if msg.clickedButton() is reveal:
            try:
                subprocess.Popen(["open", "-R", str(out_path)])
            except Exception:  # noqa: BLE001
                pass

    # ---- 1 Hz tick ------------------------------------------------------

    def _tick(self) -> None:
        if not self.runner:
            return
        if self.runner.state == State.RUNNING:
            result = self.runner.tick()
            self.recorder.current_step_idx = self.runner.step_idx
            self.recorder.tick(1.0)
            self.workout_bar.set_progress(self.runner.elapsed_s, self.runner.step_idx)
            if result.finished:
                play_system_sound("Hero")
            elif result.step_changed:
                # Distinct tone for the start of a new block.
                play_system_sound("Glass")
        self._update_displays()
        self._update_step_caption()
        if self.runner.state == State.FINISHED:
            self._timer.stop()

    def _update_displays(self) -> None:
        r = self.recorder
        if r.last_power is not None:
            self.t_power.set_value(str(r.last_power))
            self.t_power.set_accent(
                theme.zone_color_for_watts(
                    r.last_power, self.workout.ftp_w if self.workout else None
                ).name()
            )
        if r.last_cadence is not None:
            self.t_cadence.set_value(f"{int(r.last_cadence)}")
        if r.last_hr is not None:
            self.t_hr.set_value(str(r.last_hr))
        self.t_elapsed.set_value(_fmt_time(r.elapsed_s))
        if self.runner:
            self.t_block.set_value(_fmt_block_left(self.runner.step_remaining_s))

    def _update_step_caption(self) -> None:
        if not self.runner or not self.workout:
            return
        step = self.runner.current_step
        if step is None:
            self.step_caption.setText("Done.")
            return
        n = len(self.workout.steps)
        if step.is_erg():
            target_txt = f"target {step.watts_at(self.runner.step_elapsed_s)} W"
        else:
            target_txt = "free ride"
        self.step_caption.setText(
            f"Step {self.runner.step_idx + 1}/{n} · {step.label or step.kind} · "
            f"{target_txt} · {_fmt_time(self.runner.step_elapsed_s)} / {_fmt_time(step.duration_s)}"
        )

    # ---- BLE callbacks (signals) ---------------------------------------

    def _on_bike_sample(self, sample: BikeSample) -> None:
        if sample.power_w is not None:
            self.recorder.last_power = sample.power_w
        if sample.cadence_rpm is not None:
            self.recorder.last_cadence = sample.cadence_rpm
        # Trainer-reported speed is ignored — we compute virtual speed from power
        # in the Recorder for proper response to effort.

    def _on_hr(self, bpm: int) -> None:
        self.recorder.last_hr = bpm

    # ---- back -----------------------------------------------------------

    def _handle_back(self) -> None:
        if self._in_countdown:
            self._cancel_countdown()
        if self.runner and self.runner.state in (State.RUNNING, State.PAUSED):
            r = QMessageBox.question(
                self,
                "Leave workout?",
                "A workout is in progress. Finish and save the FIT first?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            )
            if r == QMessageBox.Cancel:
                return
            if r == QMessageBox.Yes:
                self._finish()
        self._timer.stop()
        asyncio.ensure_future(self._teardown_ble())
        self._on_back()
