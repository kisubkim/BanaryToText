"""Write the app icon (drawn in code) to an .ico file for PyInstaller."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtGui import QGuiApplication  # noqa: E402

from binarytotext.gui.icon import icon_pixmap  # noqa: E402

if __name__ == "__main__":
    app = QGuiApplication([])
    out = sys.argv[1]
    if not icon_pixmap(256).save(out, "ICO"):
        sys.exit(f"아이콘 저장 실패: {out}")
