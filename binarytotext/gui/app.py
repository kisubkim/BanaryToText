"""GUI entry point."""

from __future__ import annotations

import sys


def run(path: str | None = None) -> int:
    from PySide6.QtWidgets import QApplication

    from .icon import app_icon
    from .main_window import MainWindow

    sys.setrecursionlimit(20000)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("BinaryToText")
    app.setWindowIcon(app_icon())
    win = MainWindow(path)
    win.show()
    return app.exec()
