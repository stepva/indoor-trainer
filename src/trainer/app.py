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
    project_root = Path(__file__).resolve().parents[2]
    workouts_dir = project_root / "workouts"
    rides_dir = project_root / "rides"

    # Log to the terminal AND a rotating file, so crashes leave evidence
    # even after the terminal is gone.
    from logging.handlers import RotatingFileHandler

    file_handler = RotatingFileHandler(
        project_root / "trainer.log", maxBytes=1_000_000, backupCount=3
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), file_handler],
    )

    def _log_uncaught(exc_type, exc, tb):
        logging.getLogger("trainer").critical(
            "Uncaught exception", exc_info=(exc_type, exc, tb)
        )
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _log_uncaught

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
