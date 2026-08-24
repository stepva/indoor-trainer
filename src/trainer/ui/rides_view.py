"""Past rides view: history of finished rides from the results log."""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
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
from . import theme


class _RideRow(QFrame):
    def __init__(self, result: RideResult, fit_path: Path | None) -> None:
        super().__init__()
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet("background: transparent;")
        h = QHBoxLayout(self)
        h.setContentsMargins(8, 8, 8, 8)
        h.setSpacing(14)

        # Name + date column
        info = QVBoxLayout()
        info.setSpacing(2)
        name = QLabel(result.workout_name)
        name.setStyleSheet(
            f"color: {theme.TEXT}; font-size: 15px; font-weight: 600; background: transparent;"
        )
        when = QLabel(time.strftime("%a %d %b %Y · %H:%M", time.localtime(result.started_at_unix)))
        when.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 12px; background: transparent;"
        )
        info.addWidget(name)
        info.addWidget(when)
        info.addStretch(1)
        h.addLayout(info, 1)

        # Stats line
        bits = [f"{result.duration_s // 60} min", f"{result.distance_m / 1000.0:.1f} km"]
        if result.avg_power_w is not None:
            bits.append(f"avg {result.avg_power_w} W")
        if result.avg_hr_bpm is not None:
            bits.append(f"HR {result.avg_hr_bpm}")
        if result.best_1min_w is not None:
            bits.append(f"best 1' {result.best_1min_w} W")
        stats = QLabel("   ·   ".join(bits))
        stats.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 13px; background: transparent;")
        h.addWidget(stats)

        # FTP badge (ramp/FTP tests only)
        if result.ftp_estimate_w is not None:
            badge = QLabel(f"FTP {result.ftp_estimate_w} W")
            badge.setStyleSheet(
                f"color: {theme.ACCENT_TARGET}; font-size: 13px; font-weight: 700;"
                f"border: 1px solid {theme.ACCENT_TARGET}; border-radius: 9px;"
                "padding: 4px 10px; background: transparent;"
            )
            h.addWidget(badge)

        if fit_path is not None and fit_path.exists():
            reveal = QPushButton("Reveal FIT")
            reveal.clicked.connect(lambda *_: self._reveal(fit_path))
            h.addWidget(reveal)

    @staticmethod
    def _reveal(path: Path) -> None:
        try:
            subprocess.Popen(["open", "-R", str(path)])
        except Exception:  # noqa: BLE001
            pass


class RidesView(QWidget):
    def __init__(
        self,
        results_log: ResultsLog,
        rides_dir: Path,
        on_back: Callable[[], None],
    ) -> None:
        super().__init__()
        self._log = results_log
        self._rides_dir = rides_dir

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(10)
        back = QPushButton("←")
        back.setFixedSize(40, 36)
        back.clicked.connect(on_back)
        header.addWidget(back)
        title = QLabel("Past rides")
        title.setStyleSheet(
            f"color: {theme.TEXT}; font-size: 24px; font-weight: 700; background: transparent;"
        )
        header.addWidget(title)
        self.count_label = QLabel("")
        self.count_label.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 13px; background: transparent;"
        )
        header.addWidget(self.count_label)
        header.addStretch(1)
        root.addLayout(header)

        self.list = QListWidget()
        self.list.setSpacing(6)
        self.list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.list.setSelectionMode(QListWidget.NoSelection)
        self.list.setFocusPolicy(Qt.NoFocus)
        self.list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root.addWidget(self.list, 1)

        self.empty_label = QLabel("No rides yet — finish a workout and it will show up here.")
        self.empty_label.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 14px;")
        self.empty_label.setAlignment(Qt.AlignCenter)
        root.addWidget(self.empty_label, 1)

        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        results = self._log.load()
        self.count_label.setText(f"{len(results)} ride{'s' if len(results) != 1 else ''}")
        self.empty_label.setVisible(not results)
        self.list.setVisible(bool(results))
        for r in reversed(results):  # newest first
            it = QListWidgetItem(self.list)
            fit_path = self._rides_dir / r.fit_file if r.fit_file else None
            row = _RideRow(r, fit_path)
            it.setSizeHint(QSize(0, 72))
            self.list.addItem(it)
            self.list.setItemWidget(it, row)
