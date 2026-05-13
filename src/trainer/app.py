"""Application entry point: wire Qt + asyncio (qasync) and open the main window."""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import qasync
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from .ui.main_window import MainWindow
from .ui import theme


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    project_root = Path(__file__).resolve().parents[2]
    workouts_dir = project_root / "workouts"
    rides_dir = project_root / "rides"

    app = QApplication(sys.argv)
    app.setApplicationName("Indoor Trainer")
    app.setStyle("Fusion")

    f = QFont()
    f.setPointSize(13)
    app.setFont(f)
    app.setStyleSheet(theme.QSS)

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    window = MainWindow(workouts_dir=workouts_dir, rides_dir=rides_dir)
    window.show()

    with loop:
        loop.run_forever()


if __name__ == "__main__":
    main()
