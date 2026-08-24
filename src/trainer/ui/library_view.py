"""Library view: list workouts with a preview thumbnail and a Start button."""
from __future__ import annotations

import time
from typing import Callable

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..recording.results import ResultsLog, RideResult
from ..workout.library import WorkoutLibrary
from ..workout.model import Workout
from . import theme
from .widgets import WorkoutBar


# ---------------------------------------------------------------------------
# Workout row (used as setItemWidget content)
# ---------------------------------------------------------------------------


class _WorkoutRow(QFrame):
    def __init__(
        self,
        workout: Workout,
        on_start: Callable[[Workout], None],
        on_edit: Callable[[Workout], None],
    ) -> None:
        super().__init__()
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet("background: transparent;")
        h = QHBoxLayout(self)
        h.setContentsMargins(8, 8, 8, 8)
        h.setSpacing(14)

        # Thumbnail preview of the workout shape
        thumb = WorkoutBar()
        thumb.setFixedSize(280, 84)
        thumb.set_workout(workout, ftp=workout.ftp_w)
        h.addWidget(thumb)

        # Text column
        info = QVBoxLayout()
        info.setSpacing(2)
        name = QLabel(workout.name)
        name.setStyleSheet(
            f"color: {theme.TEXT}; font-size: 16px; font-weight: 600; background: transparent;"
        )
        mins = workout.total_duration_s // 60
        secs = workout.total_duration_s % 60
        meta_bits = [f"{mins} min" + (f" {secs}s" if secs else ""), f"{len(workout.steps)} steps"]
        if workout.ftp_w:
            meta_bits.append(f"FTP {workout.ftp_w} W")
        meta = QLabel("  ·  ".join(meta_bits))
        meta.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 12px; background: transparent;"
        )
        info.addWidget(name)
        info.addWidget(meta)
        info.addStretch(1)
        h.addLayout(info, 1)

        # Buttons
        edit = QPushButton("Edit")
        edit.clicked.connect(lambda *_: on_edit(workout))
        start = QPushButton("Start  ▶")
        start.setProperty("primary", True)
        start.setMinimumWidth(110)
        start.setMinimumHeight(38)
        start.clicked.connect(lambda *_: on_start(workout))
        h.addWidget(edit)
        h.addWidget(start)


# ---------------------------------------------------------------------------
# Library view
# ---------------------------------------------------------------------------


class LibraryView(QWidget):
    def __init__(
        self,
        library: WorkoutLibrary,
        on_start: Callable[[Workout], None],
        on_edit: Callable[[Workout | None], None],
        results_log: ResultsLog | None = None,
    ) -> None:
        super().__init__()
        self._lib = library
        self._on_start = on_start
        self._on_edit = on_edit
        self._results_log = results_log

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        # Header
        header = QHBoxLayout()
        title = QLabel("Workouts")
        title.setStyleSheet(
            f"color: {theme.TEXT}; font-size: 24px; font-weight: 700; background: transparent;"
        )
        header.addWidget(title)
        header.addStretch(1)
        new_btn = QPushButton("+ New workout")
        new_btn.setProperty("primary", True)
        new_btn.setMinimumHeight(36)
        new_btn.clicked.connect(lambda *_: self._on_edit(None))
        header.addWidget(new_btn)
        root.addLayout(header)

        # List
        self.list = QListWidget()
        self.list.setSpacing(8)
        self.list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.list.setSelectionMode(QListWidget.SingleSelection)
        self.list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root.addWidget(self.list, 1)

        # Bottom action row (delete / duplicate)
        actions = QHBoxLayout()
        actions.addStretch(1)
        dup_btn = QPushButton("Duplicate")
        dup_btn.clicked.connect(self._duplicate_selected)
        del_btn = QPushButton("Delete")
        del_btn.setProperty("danger", True)
        del_btn.clicked.connect(self._delete_selected)
        actions.addWidget(dup_btn)
        actions.addWidget(del_btn)
        root.addLayout(actions)

        # Recent results (only shown once there is at least one finished ride)
        self.results_title = QLabel("Recent results")
        self.results_title.setStyleSheet(
            f"color: {theme.TEXT}; font-size: 16px; font-weight: 600; background: transparent;"
        )
        root.addWidget(self.results_title)
        self.results_list = QListWidget()
        self.results_list.setSpacing(2)
        self.results_list.setFixedHeight(170)
        self.results_list.setSelectionMode(QListWidget.NoSelection)
        self.results_list.setFocusPolicy(Qt.NoFocus)
        root.addWidget(self.results_list)

        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        for w in self._lib.list():
            it = QListWidgetItem(self.list)
            it.setData(Qt.UserRole, w)
            row = _WorkoutRow(w, on_start=self._on_start, on_edit=self._on_edit)
            it.setSizeHint(QSize(0, 110))
            self.list.addItem(it)
            self.list.setItemWidget(it, row)
        self._refresh_results()

    def _refresh_results(self) -> None:
        self.results_list.clear()
        results = self._results_log.load() if self._results_log else []
        self.results_title.setVisible(bool(results))
        self.results_list.setVisible(bool(results))
        for r in reversed(results[-20:]):  # newest first
            it = QListWidgetItem(self._format_result(r))
            if r.ftp_estimate_w is not None:
                it.setForeground(Qt.white)
            self.results_list.addItem(it)

    @staticmethod
    def _format_result(r: RideResult) -> str:
        when = time.strftime("%d %b %Y %H:%M", time.localtime(r.started_at_unix))
        bits = [when, r.workout_name, f"{r.duration_s // 60} min"]
        if r.avg_power_w is not None:
            bits.append(f"avg {r.avg_power_w} W")
        if r.best_1min_w is not None:
            bits.append(f"best 1' {r.best_1min_w} W")
        if r.ftp_estimate_w is not None:
            bits.append(f"★ FTP ≈ {r.ftp_estimate_w} W")
        return "   ·   ".join(bits)

    def _selected(self) -> Workout | None:
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _duplicate_selected(self) -> None:
        w = self._selected()
        if not w:
            return
        copy = Workout.from_dict(w.to_dict())
        copy.name = f"{w.name} (copy)"
        self._lib.save(copy)
        self.refresh()

    def _delete_selected(self) -> None:
        w = self._selected()
        if not w:
            return
        self._lib.delete(w)
        self.refresh()
