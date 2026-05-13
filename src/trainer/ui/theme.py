"""Visual theme: palette, training-zone colors, and the global Qt stylesheet."""
from __future__ import annotations

from PySide6.QtGui import QColor


# ---------- Palette ----------------------------------------------------------

BG          = "#0e1014"   # window background
BG_ELEVATED = "#161922"   # slightly lighter band
BG_CARD     = "#1c2030"   # card / tile
BORDER      = "#272c3a"   # subtle separators
TEXT        = "#e6e8ee"
TEXT_DIM    = "#9aa1ad"
TEXT_MUTED  = "#6a7180"

# Metric accents
ACCENT_POWER    = "#4fc3f7"   # cyan/blue
ACCENT_TARGET   = "#ffb74d"   # amber
ACCENT_HR       = "#ff5e6c"   # red
ACCENT_CADENCE  = "#67e8a3"   # green
ACCENT_SPEED    = "#b794f4"   # purple
ACCENT_TIME     = "#a3b1c6"   # slate
ACCENT_DIST     = "#facc15"   # gold

PRIMARY = "#5b8def"
PRIMARY_HOVER = "#6e9cff"
DANGER  = "#ef4444"


# ---------- Training-zone palette (Coggan 7-zone, thresholds in % of FTP) ----

ZONE_COLORS = [
    ("#5a6470", 55),   # Z1 Active recovery
    ("#3b82f6", 75),   # Z2 Endurance
    ("#10b981", 90),   # Z3 Tempo
    ("#f59e0b", 105),  # Z4 Threshold
    ("#f97316", 120),  # Z5 VO2 max
    ("#ef4444", 150),  # Z6 Anaerobic
    ("#a855f7", 9999), # Z7 Neuromuscular
]


def zone_color_for_watts(watts: int | None, ftp: int | None) -> QColor:
    """Pick a zone color from watts. Falls back to absolute thresholds if no FTP."""
    if watts is None or watts <= 0:
        return QColor(BORDER)
    if ftp and ftp > 0:
        pct = (watts / ftp) * 100.0
    else:
        # Reasonable absolute fallback (assumes ~200 W FTP).
        pct = (watts / 200.0) * 100.0
    for hex_color, ceiling_pct in ZONE_COLORS:
        if pct < ceiling_pct:
            return QColor(hex_color)
    return QColor(ZONE_COLORS[-1][0])


# ---------- Global stylesheet ------------------------------------------------

QSS = f"""
/* Default text color only — font size lives on each widget so the
   universal selector doesn't override per-tile metric sizes. */
QWidget {{
    color: {TEXT};
}}

QMainWindow, QWidget {{
    background-color: {BG};
}}

QStatusBar, QToolBar {{
    background: transparent;
    border: none;
}}

QLabel {{
    background: transparent;
}}

/* Cards (objectName="card") */
QFrame#card, QWidget#card {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 14px;
}}

/* Buttons */
QPushButton {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 8px 14px;
    color: {TEXT};
    font-weight: 500;
}}
QPushButton:hover {{
    border-color: {PRIMARY};
    color: {TEXT};
}}
QPushButton:pressed {{
    background-color: {BG_ELEVATED};
}}
QPushButton:disabled {{
    color: {TEXT_MUTED};
    border-color: {BORDER};
}}

/* Primary button */
QPushButton[primary="true"] {{
    background-color: {PRIMARY};
    border: 1px solid {PRIMARY};
    color: white;
    font-weight: 600;
}}
QPushButton[primary="true"]:hover {{
    background-color: {PRIMARY_HOVER};
    border-color: {PRIMARY_HOVER};
}}
QPushButton[primary="true"]:disabled {{
    background-color: #2c3346;
    border-color: #2c3346;
    color: {TEXT_MUTED};
}}

/* Danger button */
QPushButton[danger="true"] {{
    background-color: transparent;
    border: 1px solid {DANGER};
    color: #ff8b8b;
}}
QPushButton[danger="true"]:hover {{
    background-color: rgba(239, 68, 68, 0.12);
}}

/* Inputs */
QLineEdit, QSpinBox, QComboBox, QAbstractSpinBox {{
    background-color: {BG_ELEVATED};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 10px;
    selection-background-color: {PRIMARY};
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border-color: {PRIMARY};
}}

/* Lists & tables */
QListWidget, QTableWidget {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 12px;
    padding: 4px;
    alternate-background-color: {BG_ELEVATED};
    gridline-color: {BORDER};
}}
QListWidget::item {{
    padding: 12px 14px;
    border-radius: 8px;
}}
QListWidget::item:selected, QListWidget::item:hover {{
    background-color: {BG_ELEVATED};
    color: {TEXT};
}}
QTableWidget {{
    gridline-color: {BORDER};
}}
QHeaderView::section {{
    background-color: {BG_ELEVATED};
    color: {TEXT_DIM};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 8px 10px;
    font-weight: 500;
    font-size: 11px;
    letter-spacing: 1px;
    text-transform: uppercase;
}}
QTableWidget::item {{
    padding: 6px;
}}
QTableWidget::item:selected {{
    background-color: rgba(91, 141, 239, 0.18);
    color: {TEXT};
}}

/* Scrollbars */
QScrollBar:vertical, QScrollBar:horizontal {{
    background: transparent;
    border: none;
    width: 10px; height: 10px;
}}
QScrollBar::handle {{
    background: {BORDER};
    border-radius: 5px;
}}
QScrollBar::handle:hover {{
    background: #3a3f4d;
}}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent; border: none; height: 0; width: 0;
}}

/* Progress bars (kept for any leftover use, our workout bar is custom) */
QProgressBar {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 6px;
    height: 8px;
    text-align: center;
}}
QProgressBar::chunk {{
    background-color: {PRIMARY};
    border-radius: 6px;
}}

/* Dialogs / message boxes */
QDialog, QMessageBox {{
    background-color: {BG};
}}

/* Tooltips */
QToolTip {{
    background-color: {BG_ELEVATED};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 4px 8px;
}}
"""
