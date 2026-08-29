"""Post-ride summary page + Strava-ready share image.

The whole card is painted with QPainter in a fixed logical coordinate space
(DESIGN_W x DESIGN_H), so the on-screen preview and the exported PNG are the
exact same drawing at different scales.

Layout (top -> bottom; no header — name/date live on the Strava activity):
  • Chart: planned structure in zone colors + actual power & HR traces
  • 6 stat tiles: time · distance · avg power · max power · cadence · avg HR
  • Footer strip: NP · IF · TSS · work kJ · best 1' · max HR
"""
from __future__ import annotations

import logging
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..recording.recorder import Record
from ..recording.results import best_rolling_power_w, is_ftp_test_name
from ..workout.model import Workout
from . import theme
from .widgets import build_profile_segments

log = logging.getLogger(__name__)

# Logical design space; exported PNG is EXPORT_SCALE x this.
DESIGN_W = 800.0
DESIGN_H = 500.0
EXPORT_SCALE = 2.0  # -> 1600 x 1000 PNG


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@dataclass
class FinishedRide:
    """Everything the summary page needs, handed from RideView to MainWindow."""

    workout: "Workout | None"
    records: "list[Record]"
    stats: "SummaryStats"
    fit_path: Path | None
    png_path: Path | None


@dataclass
class SummaryStats:
    workout_name: str
    started_at_unix: float
    duration_s: int
    distance_km: float
    avg_power_w: int | None
    max_power_w: int | None
    normalized_power_w: int | None
    intensity_factor: float | None
    tss: float | None
    work_kj: int | None
    avg_hr_bpm: int | None
    max_hr_bpm: int | None
    avg_cadence_rpm: int | None
    best_1min_w: int | None
    ftp_estimate_w: int | None
    ftp_w: int | None


def _normalized_power(powers: list[float]) -> int | None:
    """Coggan NP: 30 s rolling avg -> ^4 -> mean -> ^0.25. Records are 1 Hz."""
    if len(powers) < 30:
        return None
    window = 30
    rolling: list[float] = []
    cur = sum(powers[:window])
    rolling.append(cur / window)
    for i in range(window, len(powers)):
        cur += powers[i] - powers[i - window]
        rolling.append(cur / window)
    mean4 = sum(p**4 for p in rolling) / len(rolling)
    return int(round(mean4**0.25))


def compute_stats(
    workout_name: str,
    started_at_unix: float,
    records: list[Record],
    ftp_w: int | None,
) -> SummaryStats:
    powers = [float(r.power_w) for r in records if r.power_w is not None]
    hrs = [r.hr_bpm for r in records if r.hr_bpm is not None]
    cadences = [r.cadence_rpm for r in records if r.cadence_rpm]  # exclude 0 = coasting
    duration_s = int(records[-1].t_s) if records else 0
    np_w = _normalized_power(powers)
    intensity = tss = None
    if np_w is not None and ftp_w:
        intensity = np_w / ftp_w
        tss = duration_s * np_w * intensity / (ftp_w * 3600.0) * 100.0
    best1 = best_rolling_power_w(records)
    ftp_est = None
    if best1 is not None and is_ftp_test_name(workout_name):
        ftp_est = int(round(best1 * 0.75))
    return SummaryStats(
        workout_name=workout_name,
        started_at_unix=started_at_unix,
        duration_s=duration_s,
        distance_km=(records[-1].distance_m / 1000.0) if records else 0.0,
        avg_power_w=int(round(sum(powers) / len(powers))) if powers else None,
        max_power_w=int(max(powers)) if powers else None,
        normalized_power_w=np_w,
        intensity_factor=intensity,
        tss=tss,
        work_kj=int(round(sum(powers) / 1000.0)) if powers else None,  # 1 Hz: W·s -> kJ
        avg_hr_bpm=int(round(sum(hrs) / len(hrs))) if hrs else None,
        max_hr_bpm=max(hrs) if hrs else None,
        avg_cadence_rpm=int(round(sum(cadences) / len(cadences))) if cadences else None,
        best_1min_w=best1,
        ftp_estimate_w=ftp_est,
        ftp_w=ftp_w,
    )


def _fmt_hms(secs: int) -> str:
    if secs >= 3600:
        return f"{secs // 3600}:{(secs % 3600) // 60:02d}:{secs % 60:02d}"
    return f"{secs // 60}:{secs % 60:02d}"


def _match_laps_to_steps(lap_durs: list[float], step_durs: list[int], tol: float = 3.0) -> list[int | None]:
    """Map ride laps (in order) to plan step indices by duration.

    Laps match steps 1:1 in order until a lap is cut short (Skip step /
    → Cooldown). The cut lap still belongs to the step it was riding; the laps
    after it are anchored to the TAIL of the plan — right for both a single
    skip (the rest of the plan was ridden) and a jump to the final cooldown.
    """
    mapping: list[int | None] = [None] * len(lap_durs)
    si = 0
    for li, ld in enumerate(lap_durs):
        if si >= len(step_durs):
            break
        mapping[li] = si
        if abs(ld - step_durs[si]) <= tol:
            si += 1
            continue
        # Cut short: anchor the remaining laps to the end of the plan.
        remaining = len(lap_durs) - li - 1
        tail_start = len(step_durs) - remaining
        for k in range(remaining):
            s = tail_start + k
            mapping[li + 1 + k] = s if 0 <= s < len(step_durs) else None
        break
    return mapping


def apply_plan_targets(records: list[Record], workout: Workout | None) -> None:
    """Backfill per-second ERG targets from the plan for FIT-loaded rides.

    Our FIT files don't carry targets, but they have one lap per ridden step
    (step_idx holds the lap ordinal after _records_from_fit). Match those laps
    to plan steps and replay what each step would have targeted.
    No-op for records that already have targets or lack lap info.
    """
    if workout is None or not records or any(r.target_w for r in records):
        return
    # Contiguous runs of the same lap ordinal.
    runs: list[tuple[int, int, int]] = []  # (ordinal, start_rec, end_rec_exclusive)
    i = 0
    while i < len(records):
        j = i
        while j < len(records) and records[j].step_idx == records[i].step_idx:
            j += 1
        # Ignore boundary artifacts (e.g. the zero-length final lap in our
        # FIT files) — one stray "lap" would shift the tail anchoring.
        if records[i].step_idx >= 0 and records[j - 1].t_s - records[i].t_s >= 2.0:
            runs.append((records[i].step_idx, i, j))
        i = j
    if not runs:
        return
    lap_durs = [records[b - 1].t_s - records[a].t_s + 1.0 for _, a, b in runs]
    mapping = _match_laps_to_steps(lap_durs, [s.duration_s for s in workout.steps])
    for (ordinal, a, b), step_i in zip(runs, mapping):
        if step_i is None:
            continue
        step = workout.steps[step_i]
        if not step.is_erg():
            continue
        t0 = records[a].t_s
        for r in records[a:b]:
            r.target_w = step.watts_at(r.t_s - t0)


def _segments_from_records(records: list[Record], ftp: int | None):
    """Zone-colored target bars from the per-second ERG targets actually sent.

    Unlike the stored plan, this stays aligned with the power trace when steps
    were skipped or a bias was applied. Returns [] when no targets were
    recorded (e.g. rides backfilled from FIT files).
    """
    from .widgets import ProfileSeg  # local import to avoid cycle at module load

    if not any(r.target_w for r in records):
        return []
    segs: list[ProfileSeg] = []
    prev_end = 0.0
    for rec in records:
        tgt = int(rec.target_w or 0)
        if segs and segs[-1].avg_w == tgt:
            segs[-1].end = rec.t_s
        else:
            segs.append(
                ProfileSeg(
                    prev_end,
                    rec.t_s,
                    theme.zone_color_for_watts(tgt, ftp),
                    "",
                    tgt,
                )
            )
        prev_end = rec.t_s
    return segs


def _smooth(values: list[float], window: int = 5) -> list[float]:
    """Centered rolling mean, tolerant of short inputs."""
    if len(values) <= window:
        return values[:]
    half = window // 2
    out: list[float] = []
    for i in range(len(values)):
        lo = max(0, i - half)
        hi = min(len(values), i + half + 1)
        out.append(sum(values[lo:hi]) / (hi - lo))
    return out


# ---------------------------------------------------------------------------
# The card (preview widget + PNG renderer)
# ---------------------------------------------------------------------------


class SummaryCard(QWidget):
    """Paints the share card, aspect-fit inside its widget rect."""

    def __init__(self) -> None:
        super().__init__()
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(480, 300)
        self._workout: Workout | None = None
        self._records: list[Record] = []
        self._stats: SummaryStats | None = None

    def set_data(
        self,
        workout: Workout | None,
        records: list[Record],
        stats: SummaryStats,
    ) -> None:
        self._workout = workout
        self._records = records
        self._stats = stats
        self.update()

    # -- export ----------------------------------------------------------

    def render_image(self, scale: float = EXPORT_SCALE) -> QImage:
        img = QImage(
            int(DESIGN_W * scale), int(DESIGN_H * scale), QImage.Format_ARGB32_Premultiplied
        )
        img.fill(QColor(theme.BG))
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.scale(scale, scale)
        self._paint_card(p)
        p.end()
        return img

    def save_png(self, path: Path) -> bool:
        ok = self.render_image().save(str(path), "PNG")
        if ok:
            log.info("Saved summary image to %s", path)
        else:
            log.warning("Failed to save summary image to %s", path)
        return ok

    # -- painting ----------------------------------------------------------

    def paintEvent(self, ev: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        # Aspect-fit the design space into the widget, letterboxed on BG.
        scale = min(self.width() / DESIGN_W, self.height() / DESIGN_H)
        ox = (self.width() - DESIGN_W * scale) / 2
        oy = (self.height() - DESIGN_H * scale) / 2
        p.translate(ox, oy)
        p.scale(scale, scale)
        p.setClipRect(QRectF(0, 0, DESIGN_W, DESIGN_H))
        self._paint_card(p)

    def _paint_card(self, p: QPainter) -> None:
        full = QRectF(0, 0, DESIGN_W, DESIGN_H)
        # Background with a subtle top-down brand tint.
        p.fillRect(full, QColor(theme.BG))
        tint = QLinearGradient(0, 0, 0, DESIGN_H)
        c = QColor(theme.PRIMARY)
        c.setAlpha(14)
        tint.setColorAt(0.0, c)
        tint.setColorAt(0.45, QColor(0, 0, 0, 0))
        p.fillRect(full, QBrush(tint))

        if self._stats is None:
            p.setPen(QColor(theme.TEXT_MUTED))
            p.drawText(full, Qt.AlignCenter, "No ride data")
            return

        # No header — the name/date live on the Strava activity itself.
        margin = 26.0
        self._paint_chart(p, QRectF(margin, margin, DESIGN_W - 2 * margin, 286))
        self._paint_tiles(p, QRectF(margin, 326, DESIGN_W - 2 * margin, 118))
        self._paint_footer(p, QRectF(margin, 452, DESIGN_W - 2 * margin, 22))

    # chart ------------------------------------------------------------------

    def _paint_chart(self, p: QPainter, r: QRectF) -> None:
        s = self._stats
        assert s is not None
        # Card behind the chart.
        path = QPainterPath()
        path.addRoundedRect(r, 14, 14)
        p.fillPath(path, QBrush(QColor(theme.BG_CARD)))
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.drawPath(path)

        inner = r.adjusted(14, 12, -14, -24)
        baseline = inner.bottom()

        powers = [float(rec.power_w or 0) for rec in self._records]
        ride_s = int(self._records[-1].t_s) if self._records else 0

        # Prefer the targets that were actually sent (aligned with the trace
        # even after skips); fall back to the stored plan for old FIT rides.
        segments = _segments_from_records(self._records, s.ftp_w)
        if segments:
            total_s = max(ride_s, 1)
        else:
            total_s = max(
                self._workout.total_duration_s if self._workout else 0, ride_s, 1
            )
            if self._workout is not None:
                segments, _, _ = build_profile_segments(self._workout, s.ftp_w)
        peak_plan = max((seg.avg_w for seg in segments), default=0)
        max_y = max(peak_plan, max(powers) if powers else 0, 1) * 1.06

        # Faint horizontal gridlines every 100 W (labels drawn later, on top).
        watt_step = 100 if max_y <= 650 else 200
        watt_lines: list[tuple[int, float]] = []
        for w in range(watt_step, int(max_y), watt_step):
            y = baseline - inner.height() * (w / max_y)
            if y < inner.top() + 12:
                continue  # too close to the card edge for a label
            watt_lines.append((w, y))
            p.setPen(QPen(QColor(255, 255, 255, 16)))
            p.drawLine(QPointF(inner.left(), y), QPointF(inner.right(), y))

        # Planned structure: zone-colored bars (dimmed so the traces pop).
        for seg in segments:
            x0 = inner.left() + inner.width() * (seg.start / total_s)
            x1 = inner.left() + inner.width() * (seg.end / total_s)
            h = inner.height() * (seg.avg_w / max_y) if seg.avg_w > 0 else 5
            h = max(h, 3.0)
            # Only gap bars wide enough to read as discrete steps — per-second
            # ramp slivers would otherwise render as stripes.
            gap = 0.8 if (x1 - x0) > 6 else 0.0
            rr = QRectF(x0, baseline - h, max(1.0, x1 - x0 - gap), h)
            top = QColor(seg.color)
            top.setAlpha(150)
            bot = QColor(seg.color)
            bot.setAlpha(70)
            grad = QLinearGradient(QPointF(0, rr.top()), QPointF(0, rr.bottom()))
            grad.setColorAt(0.0, top)
            grad.setColorAt(1.0, bot)
            p.setBrush(QBrush(grad))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(rr, 2, 2)

        # HR trace (own 0..max scale, drawn first so power sits on top).
        hr_pts = [
            (rec.t_s, float(rec.hr_bpm)) for rec in self._records if rec.hr_bpm is not None
        ]
        if len(hr_pts) > 2:
            hr_max = max(v for _, v in hr_pts) * 1.15
            smoothed = _smooth([v for _, v in hr_pts])
            poly = QPainterPath()
            for i, ((t, _), v) in enumerate(zip(hr_pts, smoothed)):
                x = inner.left() + inner.width() * (t / total_s)
                y = baseline - inner.height() * (v / hr_max)
                poly.moveTo(x, y) if i == 0 else poly.lineTo(x, y)
            hr_color = QColor(theme.ACCENT_HR)
            hr_color.setAlpha(150)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(hr_color, 1.3))
            p.drawPath(poly)

        # Actual power trace (5 s smoothed, white).
        if len(powers) > 2:
            smoothed = _smooth(powers)
            poly = QPainterPath()
            for i, (rec, v) in enumerate(zip(self._records, smoothed)):
                x = inner.left() + inner.width() * (rec.t_s / total_s)
                y = baseline - inner.height() * (min(v, max_y) / max_y)
                poly.moveTo(x, y) if i == 0 else poly.lineTo(x, y)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 235), 1.6))
            p.drawPath(poly)

        # Watt labels on top of the bars, with a soft backing so they stay legible.
        grid_font = QFont()
        grid_font.setPointSizeF(7.5)
        p.setFont(grid_font)
        gfm = QFontMetricsF(grid_font)
        for w, y in watt_lines:
            txt = f"{w} W"
            tw = gfm.horizontalAdvance(txt)
            back = QRectF(inner.left(), y - gfm.height() + 1, tw + 8, gfm.height())
            backing = QColor(theme.BG_CARD)
            backing.setAlpha(190)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(backing))
            p.drawRoundedRect(back, 3, 3)
            p.setPen(QColor(theme.TEXT_MUTED))
            p.drawText(QPointF(inner.left() + 4, y - 3), txt)

        # Baseline + time labels.
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.drawLine(QPointF(inner.left(), baseline + 0.5), QPointF(inner.right(), baseline + 0.5))
        label_font = QFont()
        label_font.setPointSizeF(8.5)
        p.setFont(label_font)
        p.setPen(QColor(theme.TEXT_MUTED))
        fm = QFontMetricsF(label_font)
        for frac in (0.0, 0.5, 1.0):
            txt = _fmt_hms(int(total_s * frac))
            x = inner.left() + inner.width() * frac
            tw = fm.horizontalAdvance(txt)
            tx = max(inner.left(), min(x - tw / 2, inner.right() - tw))
            p.drawText(QPointF(tx, baseline + 16), txt)

        # Legend, top-right inside the chart.
        lx = inner.right() - 148
        ly = inner.top() + 4
        p.setPen(QPen(QColor(255, 255, 255, 235), 2))
        p.drawLine(QPointF(lx, ly + 5), QPointF(lx + 16, ly + 5))
        p.setPen(QColor(theme.TEXT_DIM))
        p.drawText(QPointF(lx + 21, ly + 9), "power")
        if len(hr_pts) > 2:
            hr_color = QColor(theme.ACCENT_HR)
            p.setPen(QPen(hr_color, 2))
            p.drawLine(QPointF(lx + 66, ly + 5), QPointF(lx + 82, ly + 5))
            p.setPen(QColor(theme.TEXT_DIM))
            p.drawText(QPointF(lx + 87, ly + 9), "heart rate")

    # tiles ------------------------------------------------------------------

    def _paint_tiles(self, p: QPainter, r: QRectF) -> None:
        s = self._stats
        assert s is not None
        tiles: list[tuple[str, str, str, str]] = [  # label, value, unit, accent
            ("Time", _fmt_hms(s.duration_s), "h:mm:ss" if s.duration_s >= 3600 else "m:ss",
             theme.ACCENT_TIME),
            ("Distance", f"{s.distance_km:.1f}", "km", theme.ACCENT_DIST),
            ("Avg power", str(s.avg_power_w) if s.avg_power_w is not None else "—", "watts",
             theme.ACCENT_POWER),
            ("Max power", str(s.max_power_w) if s.max_power_w is not None else "—", "watts",
             theme.ACCENT_TARGET),
            ("Cadence", str(s.avg_cadence_rpm) if s.avg_cadence_rpm is not None else "—", "rpm",
             theme.ACCENT_CADENCE),
            ("Avg HR", str(s.avg_hr_bpm) if s.avg_hr_bpm is not None else "—", "bpm",
             theme.ACCENT_HR),
        ]
        gap = 10.0
        tw = (r.width() - gap * (len(tiles) - 1)) / len(tiles)
        for i, (label, value, unit, accent) in enumerate(tiles):
            tile = QRectF(r.left() + i * (tw + gap), r.top(), tw, r.height())
            self._paint_tile(p, tile, label, value, unit, accent)

    def _paint_tile(
        self, p: QPainter, r: QRectF, label: str, value: str, unit: str, accent: str
    ) -> None:
        path = QPainterPath()
        path.addRoundedRect(r, 12, 12)
        p.fillPath(path, QBrush(QColor(theme.BG_CARD)))
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.drawPath(path)
        # Accent glow at the top, like the ride-view tiles.
        glow = QLinearGradient(0, r.top(), 0, r.top() + r.height() * 0.6)
        a1 = QColor(accent)
        a1.setAlpha(70)
        a2 = QColor(accent)
        a2.setAlpha(0)
        glow.setColorAt(0.0, a1)
        glow.setColorAt(1.0, a2)
        p.fillPath(path, QBrush(glow))

        inner = r.adjusted(12, 10, -12, -9)
        label_font = QFont()
        label_font.setPointSizeF(8.5)
        label_font.setWeight(QFont.DemiBold)
        label_font.setLetterSpacing(QFont.AbsoluteSpacing, 1.1)
        p.setFont(label_font)
        p.setPen(QColor(accent))
        p.drawText(
            QRectF(inner.left(), inner.top(), inner.width(), 14),
            Qt.AlignLeft | Qt.AlignVCenter,
            label.upper(),
        )

        unit_font = QFont()
        unit_font.setPointSizeF(8.5)
        unit_font.setWeight(QFont.Medium)
        p.setFont(unit_font)
        p.setPen(QColor(theme.TEXT_MUTED))
        p.drawText(
            QRectF(inner.left(), inner.bottom() - 13, inner.width(), 13),
            Qt.AlignLeft | Qt.AlignVCenter,
            unit,
        )

        value_rect = QRectF(
            inner.left(), inner.top() + 16, inner.width(), inner.height() - 32
        )
        # Auto-fit the value.
        size = value_rect.height() * 0.9
        value_font = QFont()
        value_font.setWeight(QFont.Black)
        value_font.setLetterSpacing(QFont.PercentageSpacing, 96.0)
        for _ in range(6):
            value_font.setPointSizeF(size)
            fm = QFontMetricsF(value_font)
            if fm.horizontalAdvance(value) > value_rect.width() * 0.98:
                size *= value_rect.width() * 0.98 / max(fm.horizontalAdvance(value), 1)
            elif fm.ascent() + fm.descent() > value_rect.height():
                size *= value_rect.height() / (fm.ascent() + fm.descent())
            else:
                break
        value_font.setPointSizeF(size)
        p.setFont(value_font)
        p.setPen(QColor(theme.TEXT))
        p.drawText(value_rect, Qt.AlignCenter, value)

    # footer -----------------------------------------------------------------

    def _paint_footer(self, p: QPainter, r: QRectF) -> None:
        s = self._stats
        assert s is not None
        bits: list[str] = []
        if s.normalized_power_w is not None:
            bits.append(f"NP {s.normalized_power_w} W")
        if s.intensity_factor is not None:
            bits.append(f"IF {s.intensity_factor:.2f}")
        if s.tss is not None and math.isfinite(s.tss):
            bits.append(f"TSS {s.tss:.0f}")
        if s.work_kj is not None:
            bits.append(f"{s.work_kj} kJ")
        if s.best_1min_w is not None:
            bits.append(f"best 1' {s.best_1min_w} W")
        if s.max_hr_bpm is not None:
            bits.append(f"max HR {s.max_hr_bpm}")
        if s.ftp_w:
            bits.append(f"FTP {s.ftp_w} W")
        if not bits:
            return
        f = QFont()
        f.setPointSizeF(9.5)
        f.setWeight(QFont.Medium)
        p.setFont(f)
        p.setPen(QColor(theme.TEXT_DIM))
        p.drawText(r, Qt.AlignCenter, "    ·    ".join(bits))


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


class SummaryView(QWidget):
    """Full-page workout summary shown after Finish (and from Past rides)."""

    def __init__(self, on_done: Callable[[], None]) -> None:
        super().__init__()
        self._on_done = on_done
        self._fit_path: Path | None = None
        self._png_path: Path | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 18)
        root.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(10)
        back = QPushButton("←")
        back.setFixedSize(40, 36)
        back.setFocusPolicy(Qt.NoFocus)
        back.clicked.connect(self._on_done)
        header.addWidget(back)
        title = QLabel("Workout complete")
        title.setStyleSheet(
            f"color: {theme.TEXT}; font-size: 20px; font-weight: 700; background: transparent;"
        )
        header.addWidget(title)
        self.status = QLabel("")
        self.status.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 12px; background: transparent;"
        )
        header.addWidget(self.status)
        header.addStretch(1)
        root.addLayout(header)

        self.card = SummaryCard()
        root.addWidget(self.card, 1)

        ctrl = QHBoxLayout()
        ctrl.addStretch(1)
        self.reveal_img_btn = QPushButton("Reveal share image")
        self.reveal_img_btn.setFixedHeight(40)
        self.reveal_img_btn.clicked.connect(self._reveal_image)
        self.reveal_fit_btn = QPushButton("Reveal FIT")
        self.reveal_fit_btn.setFixedHeight(40)
        self.reveal_fit_btn.clicked.connect(self._reveal_fit)
        done = QPushButton("Done")
        done.setProperty("primary", True)
        done.setFixedHeight(40)
        done.setMinimumWidth(120)
        done.clicked.connect(self._on_done)
        for b in (self.reveal_img_btn, self.reveal_fit_btn, done):
            b.setFocusPolicy(Qt.NoFocus)
            ctrl.addWidget(b)
        root.addLayout(ctrl)

    def show_ride(
        self,
        workout: Workout | None,
        records: list[Record],
        stats: SummaryStats,
        fit_path: Path | None,
        png_path: Path | None,
        *,
        write_png: bool = False,
    ) -> None:
        """Populate the page. With write_png=True, (re)renders the share PNG."""
        self.card.set_data(workout, records, stats)
        self._fit_path = fit_path
        self._png_path = png_path
        if write_png and png_path is not None:
            if not self.card.save_png(png_path):
                self._png_path = None
        self.reveal_fit_btn.setVisible(fit_path is not None and fit_path.exists())
        self.reveal_img_btn.setVisible(self._png_path is not None and self._png_path.exists())
        status_bits = []
        if fit_path is not None and fit_path.exists():
            status_bits.append(f"FIT: {fit_path.name}")
        if self._png_path is not None and self._png_path.exists():
            status_bits.append(f"image: {self._png_path.name}")
        self.status.setText("   ·   ".join(status_bits))

    @staticmethod
    def _reveal(path: Path | None) -> None:
        if path is None:
            return
        try:
            subprocess.Popen(["open", "-R", str(path)])
        except Exception:  # noqa: BLE001
            pass

    def _reveal_image(self) -> None:
        self._reveal(self._png_path)

    def _reveal_fit(self) -> None:
        self._reveal(self._fit_path)
