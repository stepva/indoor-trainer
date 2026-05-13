"""Workout builder: live preview, named blocks, and a step table."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..workout.model import Step, Workout
from . import theme
from .widgets import WorkoutBar


KINDS = ["steady", "ramp", "free"]


def _section_label(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setStyleSheet(
        f"color: {theme.TEXT_DIM}; font-size: 11px; font-weight: 600; "
        f"letter-spacing: 1.4px; background: transparent;"
    )
    return lbl


class WorkoutBuilderView(QWidget):
    """Edit a workout. Calls `on_save(new, old_name)` when Save is clicked."""

    def __init__(
        self,
        workout: Workout,
        on_save: Callable[[Workout, str], None],
        on_cancel: Callable[[], None],
    ) -> None:
        super().__init__()
        self._original_name = workout.name
        self._on_save = on_save
        self._on_cancel = on_cancel

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 18)
        root.setSpacing(14)

        # Header bar
        header = QHBoxLayout()
        back = QPushButton("←")
        back.setFixedSize(40, 36)
        back.clicked.connect(self._on_cancel)
        header.addWidget(back)
        title = QLabel("Edit workout")
        title.setStyleSheet(
            f"color: {theme.TEXT}; font-size: 22px; font-weight: 700; background: transparent;"
        )
        header.addWidget(title)
        header.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self._on_cancel)
        save_btn = QPushButton("Save")
        save_btn.setProperty("primary", True)
        save_btn.setMinimumHeight(36)
        save_btn.setMinimumWidth(110)
        save_btn.clicked.connect(self._save)
        header.addWidget(cancel_btn)
        header.addWidget(save_btn)
        root.addLayout(header)

        # Name + FTP card
        name_card = QFrame()
        name_card.setObjectName("card")
        nh = QHBoxLayout(name_card)
        nh.setContentsMargins(18, 14, 18, 14)
        nh.setSpacing(20)
        nbl = QVBoxLayout()
        nbl.setSpacing(4)
        nbl.addWidget(_section_label("Name"))
        self.name_edit = QLineEdit(workout.name)
        self.name_edit.setMinimumHeight(34)
        nbl.addWidget(self.name_edit)
        nh.addLayout(nbl, 3)
        ftpl = QVBoxLayout()
        ftpl.setSpacing(4)
        ftpl.addWidget(_section_label("FTP (W)"))
        self.ftp_spin = QSpinBox()
        self.ftp_spin.setRange(0, 600)
        self.ftp_spin.setValue(workout.ftp_w or 0)
        self.ftp_spin.setSpecialValueText("—")
        self.ftp_spin.setMinimumHeight(34)
        self.ftp_spin.valueChanged.connect(lambda *_: self._refresh_preview())
        ftpl.addWidget(self.ftp_spin)
        nh.addLayout(ftpl, 1)
        root.addWidget(name_card)

        # Live workout preview
        preview_label = _section_label("Profile")
        root.addWidget(preview_label)
        self.preview = WorkoutBar()
        root.addWidget(self.preview)
        self.total_label = QLabel("")
        self.total_label.setStyleSheet(
            f"color: {theme.TEXT_DIM}; font-size: 12px; background: transparent;"
        )
        root.addWidget(self.total_label)

        # Steps table
        root.addWidget(_section_label("Steps"))
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Kind", "Duration (s)", "Watts", "Ramp from", "Ramp to", "Label"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        root.addWidget(self.table, 1)

        # Step buttons
        sb = QHBoxLayout()
        for label, fn, primary in [
            ("+ Add step", self._add_step, True),
            ("Duplicate", self._dup_step, False),
            ("Delete", self._del_step, False),
            ("↑ Up", lambda: self._move(-1), False),
            ("↓ Down", lambda: self._move(1), False),
        ]:
            b = QPushButton(label)
            if primary:
                b.setProperty("primary", True)
            b.clicked.connect(fn)
            sb.addWidget(b)
        sb.addStretch(1)
        root.addLayout(sb)

        # Populate
        for s in workout.steps:
            self._insert_row(s, refresh=False)
        self._refresh_preview()
        self.table.itemChanged.connect(lambda *_: self._refresh_preview())

    # ---- table helpers --------------------------------------------------

    def _insert_row(self, s: Step, *, refresh: bool = True) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        kind = QComboBox()
        kind.addItems(KINDS)
        kind.setCurrentText(s.kind)
        kind.currentTextChanged.connect(lambda *_: self._refresh_preview())
        self.table.setCellWidget(r, 0, kind)
        for col, val in [
            (1, str(s.duration_s)),
            (2, "" if s.target_w is None else str(s.target_w)),
            (3, "" if s.ramp_from_w is None else str(s.ramp_from_w)),
            (4, "" if s.ramp_to_w is None else str(s.ramp_to_w)),
            (5, s.label),
        ]:
            it = QTableWidgetItem(val)
            it.setTextAlignment(Qt.AlignVCenter | (Qt.AlignLeft if col == 5 else Qt.AlignRight))
            self.table.setItem(r, col, it)
        if refresh:
            self._refresh_preview()

    def _row_to_step(self, r: int) -> Step:
        kind_w = self.table.cellWidget(r, 0)
        assert isinstance(kind_w, QComboBox)
        kind = kind_w.currentText()

        def _int(c: int) -> int | None:
            it = self.table.item(r, c)
            if not it or not it.text().strip():
                return None
            try:
                return int(it.text().strip())
            except ValueError:
                return None

        return Step(
            kind=kind,  # type: ignore[arg-type]
            duration_s=_int(1) or 60,
            target_w=_int(2),
            ramp_from_w=_int(3),
            ramp_to_w=_int(4),
            label=(self.table.item(r, 5).text() if self.table.item(r, 5) else ""),
        )

    def _add_step(self) -> None:
        self._insert_row(Step(kind="steady", duration_s=60, target_w=120, label=""))

    def _dup_step(self) -> None:
        r = self.table.currentRow()
        if r < 0:
            return
        self._insert_row(self._row_to_step(r))

    def _del_step(self) -> None:
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)
            self._refresh_preview()

    def _move(self, delta: int) -> None:
        r = self.table.currentRow()
        new_r = r + delta
        if r < 0 or not (0 <= new_r < self.table.rowCount()):
            return
        steps = [self._row_to_step(i) for i in range(self.table.rowCount())]
        steps[r], steps[new_r] = steps[new_r], steps[r]
        self.table.setRowCount(0)
        for s in steps:
            self._insert_row(s, refresh=False)
        self.table.selectRow(new_r)
        self._refresh_preview()

    def _current_workout(self) -> Workout:
        steps = [self._row_to_step(i) for i in range(self.table.rowCount())]
        return Workout(
            name=self.name_edit.text().strip() or "Untitled",
            ftp_w=self.ftp_spin.value() or None,
            steps=steps,
        )

    def _refresh_preview(self) -> None:
        wk = self._current_workout()
        self.preview.set_workout(wk, ftp=wk.ftp_w)
        secs = wk.total_duration_s
        n = len(wk.steps)
        self.total_label.setText(
            f"Total: {secs // 60} min {secs % 60:02d} s  ·  {n} steps"
            + (f"  ·  FTP {wk.ftp_w} W" if wk.ftp_w else "")
        )

    def _save(self) -> None:
        self._on_save(self._current_workout(), self._original_name)
