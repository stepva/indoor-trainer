"""Main window: a stacked container that swaps between Library / Builder / Ride."""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtWidgets import QMainWindow, QMessageBox, QStackedWidget

from ..recording.autosave import recover_autosaves
from ..recording.results import (
    ResultsLog,
    RideResult,
    _records_from_fit,
    backfill_from_fit_dir,
)
from ..workout.library import WorkoutLibrary
from ..workout.model import Workout
from .builder_view import WorkoutBuilderView
from .library_view import LibraryView
from .ride_view import RideView
from .rides_view import RidesView
from .summary_view import FinishedRide, SummaryView, apply_plan_targets, compute_stats

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self, *, workouts_dir: Path, rides_dir: Path) -> None:
        super().__init__()
        self.setWindowTitle("Indoor Trainer")
        self.resize(1280, 860)
        self.setMinimumSize(1100, 760)

        self.library = WorkoutLibrary(workouts_dir)
        self.library.seed_if_empty()
        self.results_log = ResultsLog(rides_dir / "results.json")
        recover_autosaves(rides_dir, self.results_log)  # rides lost to a crash -> FIT
        backfill_from_fit_dir(self.results_log, rides_dir)  # import pre-log FIT rides

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.library_view = LibraryView(
            self.library,
            on_start=self._open_ride,
            on_edit=self._open_builder,
            on_rides=self._show_rides,
        )
        self.stack.addWidget(self.library_view)

        self.ride_view = RideView(
            rides_dir,
            on_back=self._show_library,
            results_log=self.results_log,
            on_finished=self._show_ride_summary,
        )
        self.stack.addWidget(self.ride_view)

        self.rides_view = RidesView(
            self.results_log,
            rides_dir,
            on_back=self._show_library,
            on_summary=self._open_past_summary,
        )
        self.stack.addWidget(self.rides_view)

        self.summary_view = SummaryView(on_done=self._show_library)
        self.stack.addWidget(self.summary_view)
        self._rides_dir = rides_dir

        self._builder_view: WorkoutBuilderView | None = None
        self._show_library()

    def _show_library(self) -> None:
        self.library_view.refresh()
        self.stack.setCurrentWidget(self.library_view)

    def _show_rides(self) -> None:
        self.rides_view.refresh()
        self.stack.setCurrentWidget(self.rides_view)

    def _open_builder(self, workout: Workout | None) -> None:
        wk = workout if workout is not None else Workout(name="New Workout")
        view = WorkoutBuilderView(
            wk,
            on_save=self._save_from_builder,
            on_cancel=self._show_library,
        )
        if self._builder_view is not None:
            self.stack.removeWidget(self._builder_view)
            self._builder_view.deleteLater()
        self._builder_view = view
        self.stack.addWidget(view)
        self.stack.setCurrentWidget(view)

    def _save_from_builder(self, w: Workout, old_name: str) -> None:
        self.library.save(w, old_name=old_name)
        self._show_library()

    def _open_ride(self, w: Workout) -> None:
        self.ride_view.load_workout(w)
        self.stack.setCurrentWidget(self.ride_view)

    # ---- ride summary ----------------------------------------------------

    def _show_ride_summary(self, ride: FinishedRide) -> None:
        """A workout just finished: show the summary page + save the share PNG."""
        self.summary_view.show_ride(
            ride.workout,
            ride.records,
            ride.stats,
            ride.fit_path,
            ride.png_path,
            write_png=True,
        )
        self.stack.setCurrentWidget(self.summary_view)

    def _open_past_summary(self, result: RideResult) -> None:
        """Rebuild the summary page (and share PNG) for a past ride's FIT file."""
        if not result.fit_file:
            return
        fit_path = self._rides_dir / result.fit_file
        try:
            records, _ = _records_from_fit(fit_path)
        except Exception as exc:  # noqa: BLE001
            log.exception("Could not read %s", fit_path)
            QMessageBox.warning(self, "Summary unavailable", f"Could not read {fit_path.name}:\n{exc}")
            return
        # Find the workout for the structure chart; names may have drifted, so
        # match loosely and fall back to traces-only if nothing fits.
        wanted = result.workout_name.strip().lower()
        workout = next(
            (w for w in self.library.list() if w.name.strip().lower() == wanted), None
        )
        apply_plan_targets(records, workout)
        stats = compute_stats(
            result.workout_name,
            result.started_at_unix,
            records,
            workout.ftp_w if workout else None,
        )
        self.summary_view.show_ride(
            workout,
            records,
            stats,
            fit_path,
            fit_path.with_suffix(".png"),
            write_png=True,
        )
        self.stack.setCurrentWidget(self.summary_view)
