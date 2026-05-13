"""Reusable visual widgets: large metric tiles + workout-profile bar."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import theme


# ---------------------------------------------------------------------------
# Metric tile — uniform big-number panel, painted with QPainter so the digit
# size auto-fits the available width (no clipping, no jitter as values change).
# ---------------------------------------------------------------------------


class MetricTile(QWidget):
    def __init__(
        self,
        label: str,
        unit: str,
        accent: str,
        *,
        max_chars: int = 4,
    ) -> None:
        super().__init__()
        self._label = label.upper()
        self._unit = unit
        self._accent = accent
        self._value = "—"
        self._max_chars = max_chars
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(180, 240)
        self.setAttribute(Qt.WA_StyledBackground, False)

    # -- public ---------------------------------------------------------

    def set_value(self, txt: str) -> None:
        if txt != self._value:
            self._value = txt
            self.update()

    def set_accent(self, color: str) -> None:
        if color != self._accent:
            self._accent = color
            self.update()

    def set_label(self, label: str) -> None:
        self._label = label.upper()
        self.update()

    def set_unit(self, unit: str) -> None:
        self._unit = unit
        self.update()

    # -- paint ----------------------------------------------------------

    def paintEvent(self, ev: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(2, 2, -2, -2)

        # Card
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), 16, 16)
        p.fillPath(path, QBrush(QColor(theme.BG_CARD)))
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.drawPath(path)

        # Accent glow at the top
        glow = QLinearGradient(0, rect.top(), 0, rect.top() + rect.height() * 0.5)
        a1 = QColor(self._accent); a1.setAlpha(80)
        a2 = QColor(self._accent); a2.setAlpha(0)
        glow.setColorAt(0.0, a1)
        glow.setColorAt(1.0, a2)
        p.fillPath(path, QBrush(glow))

        # Layout zones
        inner = rect.adjusted(18, 16, -18, -16)
        label_rect = QRectF(inner.left(), inner.top(), inner.width(), 22)
        unit_rect = QRectF(inner.left(), inner.bottom() - 22, inner.width(), 22)
        value_rect = QRectF(
            inner.left(),
            label_rect.bottom() + 4,
            inner.width(),
            unit_rect.top() - label_rect.bottom() - 8,
        )

        # Label (small uppercase, accent color)
        label_font = QFont()
        label_font.setPointSizeF(11.0)
        label_font.setWeight(QFont.DemiBold)
        label_font.setLetterSpacing(QFont.AbsoluteSpacing, 1.6)
        p.setFont(label_font)
        p.setPen(QColor(self._accent))
        p.drawText(label_rect, Qt.AlignLeft | Qt.AlignVCenter, self._label)

        # Unit (small, dim)
        unit_font = QFont()
        unit_font.setPointSizeF(13.0)
        unit_font.setWeight(QFont.Medium)
        p.setFont(unit_font)
        p.setPen(QColor(theme.TEXT_MUTED))
        p.drawText(unit_rect, Qt.AlignLeft | Qt.AlignVCenter, self._unit)

        # Value — auto-fit to fill value_rect
        self._paint_autofit_value(p, value_rect)

    def _paint_autofit_value(self, p: QPainter, rect: QRectF) -> None:
        text = self._value if self._value else "—"
        target_w = rect.width()
        target_h = rect.height()
        if target_w <= 0 or target_h <= 0:
            return

        # We size by character count (so layout stays stable as digits change).
        chars = max(len(text), self._max_chars)
        # Heuristic starting point.
        size = max(20.0, min(target_h * 0.95, target_w * 1.7 / chars))

        font = QFont()
        font.setWeight(QFont.Black)
        font.setStyleStrategy(QFont.PreferAntialias)
        # Tighten the digits visually (negative spacing is allowed and looks
        # great on huge numerals).
        font.setLetterSpacing(QFont.PercentageSpacing, 96.0)

        # Binary-ish refinement
        for _ in range(6):
            font.setPointSizeF(size)
            fm = QFontMetrics(font)
            w = fm.horizontalAdvance("0" * chars)
            h = fm.ascent() + fm.descent()
            if w > target_w * 0.98:
                size *= target_w * 0.98 / max(w, 1)
            elif h > target_h * 0.95:
                size *= target_h * 0.95 / max(h, 1)
            else:
                break
        font.setPointSizeF(size)
        p.setFont(font)
        p.setPen(QColor(theme.TEXT))
        p.drawText(rect, Qt.AlignCenter, text)


# ---------------------------------------------------------------------------
# Workout profile bar (unchanged behavior; same nice paint)
# ---------------------------------------------------------------------------


@dataclass
class _Seg:
    start: float
    end: float
    color: QColor
    label: str
    avg_w: int


class WorkoutBar(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(96)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._segments: list[_Seg] = []
        self._max_w: int = 1
        self._total_s: int = 0
        self._progress_s: float = 0.0
        self._current_step: int = -1
        self._show_cursor: bool = False
        self._ftp: int | None = None

    def set_workout(self, workout, ftp: int | None = None) -> None:
        self._segments.clear()
        self._ftp = ftp or workout.ftp_w
        t = 0.0
        peak = 1
        for step in workout.steps:
            if step.kind == "steady":
                w = int(step.target_w or 0)
                color = theme.zone_color_for_watts(w, self._ftp)
                self._segments.append(_Seg(t, t + step.duration_s, color, step.label or "steady", w))
                peak = max(peak, w)
            elif step.kind == "ramp":
                a = int(step.ramp_from_w or 0)
                b = int(step.ramp_to_w or 0)
                slices = 6
                for i in range(slices):
                    f0 = i / slices
                    f1 = (i + 1) / slices
                    avg_w = int(round(a + (b - a) * (f0 + f1) / 2))
                    color = theme.zone_color_for_watts(avg_w, self._ftp)
                    self._segments.append(
                        _Seg(
                            t + step.duration_s * f0,
                            t + step.duration_s * f1,
                            color,
                            step.label or "ramp",
                            avg_w,
                        )
                    )
                    peak = max(peak, avg_w)
            else:
                color = QColor(theme.BORDER)
                self._segments.append(_Seg(t, t + step.duration_s, color, step.label or "free", 0))
            t += step.duration_s
        self._total_s = max(int(t), 1)
        self._max_w = max(peak, 1)
        self._progress_s = 0.0
        self._current_step = -1
        self._show_cursor = False
        self.update()

    def set_progress(self, elapsed_s: float, current_step_idx: int) -> None:
        self._progress_s = max(0.0, min(float(elapsed_s), float(self._total_s)))
        self._current_step = current_step_idx
        self._show_cursor = True
        self.update()

    def clear_progress(self) -> None:
        self._show_cursor = False
        self._current_step = -1
        self._progress_s = 0.0
        self.update()

    def paintEvent(self, ev: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(2, 2, -2, -2)

        p.setBrush(QBrush(QColor(theme.BG_CARD)))
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.drawRoundedRect(QRectF(rect), 12, 12)

        if not self._segments or self._total_s <= 0:
            p.setPen(QColor(theme.TEXT_MUTED))
            p.drawText(rect, Qt.AlignCenter, "No steps yet")
            return

        inner = rect.adjusted(8, 10, -8, -22)
        baseline_y = inner.bottom()
        full_w = inner.width()

        grid_pen = QPen(QColor(255, 255, 255, 18))
        grid_pen.setWidth(1)
        p.setPen(grid_pen)
        for frac in (0.25, 0.5, 0.75):
            y = baseline_y - inner.height() * frac
            p.drawLine(QPointF(inner.left(), y), QPointF(inner.right(), y))

        for seg in self._segments:
            x0 = inner.left() + full_w * (seg.start / self._total_s)
            x1 = inner.left() + full_w * (seg.end / self._total_s)
            h = inner.height() * (seg.avg_w / self._max_w) if seg.avg_w > 0 else 6
            h = max(h, 4)
            r = QRectF(x0, baseline_y - h, max(1.0, x1 - x0 - 1.0), h)
            top_color = QColor(seg.color)
            bot_color = QColor(seg.color)
            bot_color.setAlpha(170)
            grad = QLinearGradient(QPointF(0, r.top()), QPointF(0, r.bottom()))
            grad.setColorAt(0.0, top_color)
            grad.setColorAt(1.0, bot_color)
            p.setBrush(QBrush(grad))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(r, 3, 3)

        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.drawLine(QPointF(inner.left(), baseline_y + 0.5), QPointF(inner.right(), baseline_y + 0.5))

        if self._show_cursor:
            cx = inner.left() + full_w * (self._progress_s / self._total_s)
            shade = QColor(255, 255, 255, 18)
            p.fillRect(QRectF(inner.left(), inner.top(), cx - inner.left(), inner.height()), shade)
            cursor_pen = QPen(QColor("#ffffff"), 2)
            p.setPen(cursor_pen)
            p.drawLine(QPointF(cx, inner.top()), QPointF(cx, baseline_y))

        p.setPen(QColor(theme.TEXT_MUTED))
        f = p.font()
        f.setPointSize(9)
        p.setFont(f)
        fm = QFontMetrics(f)
        total_min = self._total_s // 60
        mid_min = self._total_s // 120
        labels = [
            (inner.left(), "0:00"),
            (inner.left() + full_w / 2, f"{mid_min}:{(self._total_s // 2) % 60:02d}"),
            (inner.right(), f"{total_min}:{self._total_s % 60:02d}"),
        ]
        for x, txt in labels:
            tw = fm.horizontalAdvance(txt)
            tx = x - tw / 2
            tx = max(rect.left() + 6, min(tx, rect.right() - tw - 6))
            p.drawText(QPointF(tx, baseline_y + 14), txt)


# ---------------------------------------------------------------------------
# Sound helper — non-blocking macOS system sounds
# ---------------------------------------------------------------------------


import subprocess
from pathlib import Path

_SOUND_DIR = Path("/System/Library/Sounds")


def play_system_sound(name: str) -> None:
    """Play a macOS system sound (e.g. 'Tink', 'Glass', 'Hero') asynchronously."""
    path = _SOUND_DIR / f"{name}.aiff"
    if not path.exists():
        return
    try:
        subprocess.Popen(
            ["afplay", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Back-compat aliases — older imports still reference HeroTile / Tile names.
# ---------------------------------------------------------------------------

Tile = MetricTile
HeroTile = MetricTile
