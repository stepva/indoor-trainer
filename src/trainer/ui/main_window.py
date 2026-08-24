"""Main window: a stacked container that swaps between Library / Builder / Ride."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QMainWindow, QStackedWidget

from ..recording.results import ResultsLog, backfill_from_fit_dir
from ..workout.library import WorkoutLibrary
from ..workout.model import Workout
from .builder_view import WorkoutBuilderView
from .library_view import LibraryView
from .ride_view import RideView
from .rides_view import RidesView


class MainWindow(QMainWindow):
    def __init__(self, *, workouts_dir: Path, rides_dir: Path) -> None:
        super().__init__()
        self.setWindowTitle("Indoor Trainer")
        self.resize(1280, 860)
        self.setMinimumSize(1100, 760)

        self.library = WorkoutLibrary(workouts_dir)
        self.library.seed_if_empty()
        self.results_log = ResultsLog(rides_dir / "results.json")
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

        self.ride_view = RideView(rides_dir, on_back=self._show_library, results_log=self.results_log)
        self.stack.addWidget(self.ride_view)

        self.rides_view = RidesView(self.results_log, rides_dir, on_back=self._show_library)
        self.stack.addWidget(self.rides_view)

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
