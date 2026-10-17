"""Color themes: Fusion style + palette + a small stylesheet built from one color table.

Panels (editors, tables, text) stand out from the window background; toolbar, tabs,
headers and buttons get their own tones so areas are easy to tell apart.
"system" restores the native Windows look.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPalette, QPen, QPolygonF
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Theme:
    title: str
    bg: str          # window background
    panel: str       # editors, tables, text
    alt: str         # alternate rows
    bar: str         # toolbar, status bar
    line: str        # borders
    text: str
    muted: str       # disabled text, placeholders
    btn: str
    btn_hover: str
    btn_down: str
    btn_line: str
    accent: str      # selected tab, default button
    select: str      # selection background
    select_text: str
    header: str      # table headers
    tab: str         # unselected tabs
    grid: str
    tip: str
    tip_line: str
    link: str
    shade: str       # QPalette.Dark (bevels, scroll bar grooves)
    radius: int = 4


DEFAULT = "pastel_blue"

THEMES: dict[str, Theme] = {
    "pastel_blue": Theme(
        "파스텔 블루", bg="#eef1f8", panel="#ffffff", alt="#f5f7fc", bar="#e1e8f6", line="#c9d3e6",
        text="#2b3240", muted="#9aa3b5", btn="#dbe6fb", btn_hover="#cddcf8", btn_down="#bccff3",
        btn_line="#b3c5ea", accent="#7fa6e8", select="#cfe0fc", select_text="#2b3240", header="#e7edf9",
        tab="#dfe5f1", grid="#e3e8f2", tip="#fffbe8", tip_line="#e6d9a8", link="#3d6fc4", shade="#a9b4c9"),
    "pastel_mint": Theme(
        "파스텔 민트", bg="#edf6f2", panel="#ffffff", alt="#f4faf7", bar="#dcefe6", line="#c3dccf",
        text="#27352f", muted="#93a69c", btn="#d3eee1", btn_hover="#c3e7d6", btn_down="#acdcc5",
        btn_line="#a5d3bd", accent="#6cc0a0", select="#cdeedf", select_text="#27352f", header="#e2f2ea",
        tab="#dbe9e2", grid="#e1eee7", tip="#fffbe8", tip_line="#e6d9a8", link="#2f8a68", shade="#a3bdb0"),
    "pastel_lavender": Theme(
        "파스텔 라벤더", bg="#f2eff8", panel="#ffffff", alt="#f8f6fc", bar="#e7e1f4", line="#d2c9e6",
        text="#322b40", muted="#a29ab5", btn="#e5dcf8", btn_hover="#dacef5", btn_down="#c9b9ef",
        btn_line="#c2b3e6", accent="#a48be0", select="#e3d8fb", select_text="#322b40", header="#ece6f8",
        tab="#e4dff0", grid="#e9e4f2", tip="#fffbe8", tip_line="#e6d9a8", link="#6a4fb8", shade="#b5abc9"),
    "pastel_peach": Theme(
        "파스텔 피치", bg="#faf1ec", panel="#ffffff", alt="#fdf7f3", bar="#f6e3d9", line="#e8d0c3",
        text="#3d2f29", muted="#b5a198", btn="#fadccd", btn_hover="#f7cfbb", btn_down="#f2bca2",
        btn_line="#ebbba3", accent="#ee9f7c", select="#fbdccd", select_text="#3d2f29", header="#f9e8de",
        tab="#f1e2da", grid="#f2e6df", tip="#fffbe8", tip_line="#e6d9a8", link="#b8603a", shade="#cbb2a6"),
    "soft_dark": Theme(
        "소프트 다크", bg="#2e333d", panel="#363c48", alt="#3b4250", bar="#272b33", line="#4a5263",
        text="#dfe4ee", muted="#8790a3", btn="#45506a", btn_hover="#505d7a", btn_down="#5c6b8c",
        btn_line="#5a6680", accent="#98b6ec", select="#4f6590", select_text="#f2f5fa", header="#313744",
        tab="#2a2f38", grid="#434a59", tip="#3f4656", tip_line="#5a6680", link="#9cc0ff", shade="#1f2329"),
    "terminal": Theme(
        "레트로 터미널", bg="#0b0f0b", panel="#050805", alt="#0a110a", bar="#0f170f", line="#1f5a2a",
        text="#7df08f", muted="#3f7a4a", btn="#0f1f12", btn_hover="#163019", btn_down="#1f4425",
        btn_line="#2f8a40", accent="#7df08f", select="#1c5a28", select_text="#c8ffd0", header="#0f1f12",
        tab="#0a140b", grid="#123118", tip="#0f1f12", tip_line="#2f8a40", link="#a8ffb5", shade="#020402",
        radius=0),
    "paper": Theme(
        "세피아 종이", bg="#efe6d2", panel="#fbf6ea", alt="#f6efdf", bar="#e4d6b8", line="#cdb991",
        text="#4a3a28", muted="#a8977a", btn="#eadcbc", btn_hover="#e2d0a8", btn_down="#d6c08e",
        btn_line="#bfa676", accent="#a0753f", select="#e8d3a6", select_text="#3a2c1c", header="#ece0c4",
        tab="#e2d6bb", grid="#e8dcc2", tip="#fbf6ea", tip_line="#bfa676", link="#8a5a22", shade="#b8a27a",
        radius=1),
}
SYSTEM = "system"
TITLES = {**{k: t.title for k, t in THEMES.items()}, SYSTEM: "Windows 기본"}

_native_style: str | None = None
_native_palette: QPalette | None = None


def _image(name: str, w: int, h: int, draw) -> str:
    """Small PNG cached in the temp folder; styled widgets lose their native arrows and check marks."""
    path = Path(tempfile.gettempdir()) / "binarytotext_theme" / f"{name}.png"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        img = QImage(w, h, QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        draw(p)
        p.end()
        img.save(str(path))
    return path.as_posix()


def _arrow(color: str, up: bool) -> str:
    def draw(p: QPainter) -> None:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        pts = [(0, 6), (10, 6), (5, 0)] if up else [(0, 0), (10, 0), (5, 6)]
        p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))
    return _image(f"arrow_{color.lstrip('#')}_{'up' if up else 'down'}", 10, 6, draw)


def _check(t: Theme, checked: bool, enabled: bool = True) -> str:
    border = t.accent if checked and enabled else (t.muted if enabled else t.line)
    mark = t.text if enabled else t.muted

    def draw(p: QPainter) -> None:
        p.setPen(QPen(QColor(border), 1.2))
        p.setBrush(QColor(t.panel if enabled else t.bg))
        p.drawRoundedRect(0.6, 0.6, 12.8, 12.8, min(t.radius, 3), min(t.radius, 3))
        if checked:
            pen = QPen(QColor(mark), 2)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            p.drawPolyline(QPolygonF([QPointF(3.2, 7.2), QPointF(5.8, 9.8), QPointF(10.8, 4.2)]))
    key = "_".join(c.lstrip("#") for c in (border, mark, t.panel, t.bg))
    return _image(f"check_{key}_{int(checked)}{int(enabled)}_{min(t.radius, 3)}", 14, 14, draw)


def stylesheet(t: Theme) -> str:
    r = t.radius
    down, up, off = _arrow(t.text, False), _arrow(t.text, True), _arrow(t.muted, False)
    box, box_on = _check(t, False), _check(t, True)
    box_off, box_on_off = _check(t, False, False), _check(t, True, False)
    return f"""
QToolBar {{ background: {t.bar}; border: none; border-bottom: 1px solid {t.line}; spacing: 4px; padding: 3px; }}
QToolBar QToolButton {{ border-radius: {r}px; padding: 3px 5px; }}
QToolBar QToolButton:hover {{ background: {t.btn_hover}; }}
QToolBar QToolButton:pressed {{ background: {t.btn_down}; }}
QStatusBar {{ background: {t.bar}; border-top: 1px solid {t.line}; }}
QStatusBar QToolButton {{ background: {t.btn}; border: 1px solid {t.btn_line}; border-radius: {r}px; padding: 1px 16px 1px 8px; }}
QStatusBar QToolButton:hover {{ background: {t.btn_hover}; }}
QStatusBar QToolButton::menu-indicator {{ image: url({down}); subcontrol-origin: padding; subcontrol-position: center right; right: 4px; }}
QPushButton {{ background: {t.btn}; border: 1px solid {t.btn_line}; border-radius: {r}px; padding: 4px 10px; }}
QPushButton:hover {{ background: {t.btn_hover}; }}
QPushButton:pressed {{ background: {t.btn_down}; }}
QPushButton:disabled {{ background: {t.bg}; color: {t.muted}; border-color: {t.line}; }}
QPushButton:default {{ border-color: {t.accent}; }}
QTabWidget::pane {{ border: 1px solid {t.line}; background: {t.panel}; top: -1px; }}
QTabBar::tab {{ background: {t.tab}; border: 1px solid {t.line}; border-bottom: none; padding: 5px 14px;
               margin-right: 2px; border-top-left-radius: {r}px; border-top-right-radius: {r}px; }}
QTabBar::tab:selected {{ background: {t.panel}; border-top: 2px solid {t.accent}; }}
QTabBar::tab:!selected:hover {{ background: {t.btn_hover}; }}
QHeaderView::section {{ background: {t.header}; border: none; border-right: 1px solid {t.line};
                       border-bottom: 1px solid {t.line}; padding: 3px 5px; }}
QTableView, QTreeView, QListView, QPlainTextEdit, QTextEdit {{ border: 1px solid {t.line}; gridline-color: {t.grid}; }}
QLineEdit, QComboBox, QAbstractSpinBox {{ border: 1px solid {t.line}; border-radius: {r}px; padding: 1px 3px; }}
QLineEdit {{ background: {t.panel}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox::down-arrow {{ image: url({down}); }}
QComboBox::down-arrow:disabled {{ image: url({off}); }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ border: none; width: 16px; }}
QCheckBox {{ spacing: 5px; }}
QToolBar QCheckBox {{ margin-left: 3px; }}
QCheckBox::indicator, QTableView::indicator, QTreeView::indicator {{ width: 14px; height: 14px; }}
QCheckBox::indicator:unchecked, QTableView::indicator:unchecked, QTreeView::indicator:unchecked {{ image: url({box}); }}
QCheckBox::indicator:checked, QTableView::indicator:checked, QTreeView::indicator:checked {{ image: url({box_on}); }}
QCheckBox::indicator:unchecked:disabled {{ image: url({box_off}); }}
QCheckBox::indicator:checked:disabled {{ image: url({box_on_off}); }}
QAbstractSpinBox::up-arrow {{ image: url({up}); }}
QAbstractSpinBox::down-arrow {{ image: url({down}); }}
QLineEdit:focus, QComboBox:focus, QAbstractSpinBox:focus {{ border-color: {t.accent}; }}
QLineEdit:disabled {{ background: {t.bg}; }}
QComboBox QLineEdit, QAbstractSpinBox QLineEdit {{ border: none; padding: 0; background: transparent; }}
QTableView QTableCornerButton::section {{ background: {t.header}; border: none; }}
QSplitter::handle {{ background: {t.bg}; }}
QSplitter::handle:horizontal {{ width: 6px; }}
QSplitter::handle:vertical {{ height: 6px; }}
QGroupBox {{ border: 1px solid {t.line}; border-radius: {r + 1}px; margin-top: 10px; padding-top: 6px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 3px; }}
QToolTip {{ background: {t.tip}; color: {t.text}; border: 1px solid {t.tip_line}; padding: 4px; }}
"""


def palette(t: Theme) -> QPalette:
    p = QPalette()
    for role, color in ((QPalette.Window, t.bg), (QPalette.WindowText, t.text), (QPalette.Base, t.panel),
                        (QPalette.AlternateBase, t.alt), (QPalette.Text, t.text), (QPalette.Button, t.btn),
                        (QPalette.ButtonText, t.text), (QPalette.BrightText, t.accent),
                        (QPalette.Highlight, t.select), (QPalette.HighlightedText, t.select_text),
                        (QPalette.ToolTipBase, t.tip), (QPalette.ToolTipText, t.text),
                        (QPalette.PlaceholderText, t.muted), (QPalette.Light, t.panel),
                        (QPalette.Midlight, t.header), (QPalette.Mid, t.line), (QPalette.Dark, t.shade),
                        (QPalette.Shadow, t.shade), (QPalette.Link, t.link), (QPalette.LinkVisited, t.link)):
        p.setColor(role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(t.muted))
    p.setColor(QPalette.Disabled, QPalette.Base, QColor(t.bg))
    return p


def apply_theme(app: QApplication, name: str = DEFAULT) -> str:
    """Apply a theme by key and return the key actually used (unknown keys fall back to DEFAULT)."""
    global _native_style, _native_palette
    if _native_style is None:
        _native_style = app.style().name() or "windowsvista"  # name() is empty once a stylesheet is set
        _native_palette = QPalette(app.palette())
    if name == SYSTEM:
        app.setStyleSheet("")
        app.setStyle(_native_style)
        app.setPalette(_native_palette)
        return name
    if name not in THEMES:
        name = DEFAULT
    t = THEMES[name]
    app.setStyle("Fusion")
    app.setPalette(palette(t))
    app.setStyleSheet(stylesheet(t))
    return name
