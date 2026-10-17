"""Hex view (table model, scales to large files) and the data inspector."""

from __future__ import annotations

import struct

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QMenu, QTableView, QTableWidget, QTableWidgetItem

from ..reader import float_text, unix_time

WIDTH = 16
ASCII_COL = WIDTH


def mono_font() -> QFont:
    f = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    f.setPointSize(10)
    return f


class HexModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.data_bytes = b""
        self.marks = bytearray()  # 0 = covered, 1 = unknown, 2 = not covered
        self.hl_start = self.hl_end = -1
        self.c_unknown = QColor(255, 140, 0, 70)
        self.c_uncovered = QColor(128, 128, 128, 40)
        self.c_highlight = QColor(40, 120, 255, 110)

    def set_data(self, data: bytes) -> None:
        self.beginResetModel()
        self.data_bytes = data
        self.marks = bytearray()
        self.hl_start = self.hl_end = -1
        self.endResetModel()

    def set_marks(self, marks: bytearray) -> None:
        self.marks = marks
        self._refresh_all()

    def set_highlight(self, start: int, end: int) -> None:
        old = (self.hl_start, self.hl_end)
        self.hl_start, self.hl_end = start, end
        for s, e in (old, (start, end)):
            if s >= 0:
                self.dataChanged.emit(self.index(s // WIDTH, 0),
                                      self.index(max(s, e - 1) // WIDTH, ASCII_COL))

    def _refresh_all(self) -> None:
        if self.rowCount():
            self.dataChanged.emit(self.index(0, 0), self.index(self.rowCount() - 1, ASCII_COL))

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else (len(self.data_bytes) + WIDTH - 1) // WIDTH

    def columnCount(self, parent=QModelIndex()) -> int:
        return WIDTH + 1

    def offset_of(self, index: QModelIndex) -> int:
        col = min(index.column(), WIDTH - 1)
        return index.row() * WIDTH + col

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        base = row * WIDTH
        if col == ASCII_COL:
            if role == Qt.DisplayRole:
                chunk = self.data_bytes[base:base + WIDTH]
                return "".join(chr(c) if 0x20 <= c < 0x7F else "." for c in chunk)
            if role == Qt.BackgroundRole and self.hl_start >= 0:
                if self.hl_start < base + WIDTH and self.hl_end > base:
                    return self.c_highlight
            return None
        off = base + col
        if off >= len(self.data_bytes):
            return None
        if role == Qt.DisplayRole:
            return f"{self.data_bytes[off]:02X}"
        if role == Qt.BackgroundRole:
            if self.hl_start <= off < self.hl_end:
                return self.c_highlight
            if off < len(self.marks):
                m = self.marks[off]
                if m == 1:
                    return self.c_unknown
                if m == 2:
                    return self.c_uncovered
        if role == Qt.ToolTipRole:
            b = self.data_bytes[off]
            return f"0x{off:X} ({off}) = 0x{b:02X} / {b} / '{chr(b) if 0x20 <= b < 0x7F else '.'}'"
        if role == Qt.TextAlignmentRole:
            return Qt.AlignCenter
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return "ASCII" if section == ASCII_COL else f"{section:X}"
        return f"{section * WIDTH:08X}"


class HexView(QTableView):
    cursor_moved = Signal(int)  # byte offset
    add_rule_requested = Signal(bytes)  # selected bytes as tag

    def __init__(self):
        super().__init__()
        self.model_ = HexModel()
        self.setModel(self.model_)
        self.setFont(mono_font())
        self.setShowGrid(False)
        self.setSelectionMode(QAbstractItemView.ContiguousSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        h = self.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.Fixed)
        fm = self.fontMetrics()
        cw = fm.horizontalAdvance("00") + 8
        for c in range(WIDTH):
            self.setColumnWidth(c, cw)
        self.setColumnWidth(ASCII_COL, fm.horizontalAdvance("W" * WIDTH) + 16)
        self._width = (fm.horizontalAdvance("00000000") + 16 + cw * WIDTH
                       + fm.horizontalAdvance("W" * WIDTH) + 16 + 48)
        v = self.verticalHeader()
        v.setSectionResizeMode(QHeaderView.Fixed)
        v.setDefaultSectionSize(fm.height() + 4)
        v.setFont(mono_font())
        self.selectionModel().currentChanged.connect(self._current_changed)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

    def preferred_width(self) -> int:
        return self._width

    def set_data(self, data: bytes) -> None:
        self.model_.set_data(data)

    def _current_changed(self, cur: QModelIndex, _prev) -> None:
        if cur.isValid():
            off = self.model_.offset_of(cur)
            if off < len(self.model_.data_bytes):
                self.cursor_moved.emit(off)

    def selected_range(self) -> tuple[int, int] | None:
        idx = [i for i in self.selectionModel().selectedIndexes() if i.column() < WIDTH]
        if not idx:
            return None
        offs = [self.model_.offset_of(i) for i in idx]
        n = len(self.model_.data_bytes)
        return min(offs), min(max(offs) + 1, n)

    def highlight(self, start: int, end: int, scroll: bool = True) -> None:
        self.model_.set_highlight(start, max(end, start + 1))
        if scroll and 0 <= start < len(self.model_.data_bytes):
            self._center_row(start // WIDTH)

    def _center_row(self, row: int) -> None:
        # Column 0 keeps the view from scrolling sideways.
        self.scrollTo(self.model_.index(row, 0), QAbstractItemView.PositionAtCenter)

    def go_to(self, offset: int) -> None:
        if 0 <= offset < len(self.model_.data_bytes):
            idx = self.model_.index(offset // WIDTH, offset % WIDTH)
            self.setCurrentIndex(idx)
            self._center_row(offset // WIDTH)

    def _menu(self, pos) -> None:
        rng = self.selected_range()
        menu = QMenu(self)
        if rng:
            s, e = rng
            b = self.model_.data_bytes[s:e]
            label = b[:8].hex(" ").upper() + (" …" if len(b) > 8 else "")
            a = menu.addAction(f"선택한 바이트 [{label}] 를 태그로 규칙 추가…")
            a.setEnabled(1 <= len(b) <= 16)
            a.triggered.connect(lambda: self.add_rule_requested.emit(b))
            menu.addAction("선택 영역 hex 복사").triggered.connect(lambda: _copy(b.hex(" ").upper()))
            menu.addAction("선택 영역 ASCII 복사").triggered.connect(
                lambda: _copy("".join(chr(c) if 0x20 <= c < 0x7F else "." for c in b)))
        menu.exec(self.viewport().mapToGlobal(pos))


def _copy(text: str) -> None:
    from PySide6.QtWidgets import QApplication
    QApplication.clipboard().setText(text)


def inspect_values(data: bytes, off: int) -> list[tuple[str, str]]:
    """All common interpretations of the bytes at `off`."""
    b = data[off:off + 64]
    rows: list[tuple[str, str]] = [("오프셋", f"0x{off:X} ({off})")]

    def num(label, n, fmt):
        if len(b) >= n:
            for e, en in (("<", "LE"), (">", "BE")):
                if n == 1 and en == "BE":
                    continue
                if fmt in "fd":
                    text = float_text(b[:n], "little" if e == "<" else "big")
                else:
                    text = str(struct.unpack(e + fmt, b[:n])[0])
                rows.append((f"{label}{'' if n == 1 else ' ' + en}", text))

    num("uint8", 1, "B")
    num("int8", 1, "b")
    num("uint16", 2, "H")
    num("int16", 2, "h")
    num("uint32", 4, "I")
    num("int32", 4, "i")
    num("uint64", 8, "Q")
    num("int64", 8, "q")
    num("float32", 4, "f")
    num("float64", 8, "d")
    if b:
        rows.append(("2진수", f"{b[0]:08b}"))
        v = shift = 0
        for i, c in enumerate(b[:10]):
            v |= (c & 0x7F) << shift
            shift += 7
            if not c & 0x80:
                rows.append(("varint", f"{v} ({i + 1} byte)"))
                break
    if len(b) >= 4:
        rows.append(("Unix 시각 32 LE", unix_time(int.from_bytes(b[:4], "little"))))
        rows.append(("Unix 시각 32 BE", unix_time(int.from_bytes(b[:4], "big"))))
        rows.append(("BCD 4 byte", b[:4].hex().upper()))
    z = b.find(b"\x00")
    s = b[: z if z >= 0 else 32]
    rows.append(("ASCII (00까지)", repr(s.decode("latin-1"))[1:-1]))
    for enc in ("utf-8", "cp949", "utf-16-le", "utf-16-be"):
        chunk = b[:32]
        if enc.startswith("utf-16"):
            chunk = chunk[: len(chunk) // 2 * 2]
            zz = next((i for i in range(0, len(chunk) - 1, 2) if chunk[i:i + 2] == b"\x00\x00"), None)
            if zz is not None:
                chunk = chunk[:zz]
        elif z >= 0:
            chunk = b[:z][:32]
        rows.append((enc.upper(), chunk.decode(enc, errors="replace")))
    return rows


class Inspector(QTableWidget):
    def __init__(self):
        super().__init__(0, 2)
        self.setHorizontalHeaderLabels(["해석", "값"])
        self.horizontalHeader().setStretchLastSection(True)
        self.verticalHeader().hide()
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setFont(mono_font())
        self.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 4)

    def show_at(self, data: bytes, off: int) -> None:
        rows = inspect_values(data, off)
        self.setRowCount(len(rows))
        for i, (k, v) in enumerate(rows):
            self.setItem(i, 0, QTableWidgetItem(k))
            self.setItem(i, 1, QTableWidgetItem(v))
        self.resizeColumnToContents(0)
