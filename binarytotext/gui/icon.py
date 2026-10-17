"""Application icon drawn in code (no image assets needed)."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap


def icon_pixmap(size: int = 256) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(32, 72, 140))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(size * 0.04, size * 0.04, size * 0.92, size * 0.92), size * 0.18, size * 0.18)
    f = QFont("Consolas")
    f.setPixelSize(int(size * 0.30))
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor(255, 190, 70))
    p.drawText(QRectF(0, size * 0.10, size, size * 0.40), Qt.AlignCenter, "0A")
    p.setPen(QColor(255, 255, 255))
    p.drawText(QRectF(0, size * 0.50, size, size * 0.40), Qt.AlignCenter, "Aa")
    p.end()
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    for s in (16, 32, 48, 256):
        icon.addPixmap(icon_pixmap(s))
    return icon
