"""Editable list of FieldSpecs (used by the rule dialog and the header/footer dialog)."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLineEdit, QListWidget,
                               QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ..rules import ENCODINGS, FIELD_TYPES, FieldSpec
from .hexview import mono_font

SIZED = {"uint", "int", "float", "bool", "fstring", "bytes", "bcd", "skip", "unixtime"}
TEXT = {"cstring", "fstring", "pstring", "rest"}
PREFIXED = {"pstring", "pbytes"}
INTS = {"uint", "int", "varint", "svarint", "bool"}


def combo(items: list[tuple[str, str]], editable: bool = False) -> QComboBox:
    c = QComboBox()
    c.setEditable(editable)
    for key, label in items:
        c.addItem(label, key)
    return c


def set_combo(c: QComboBox, key: str) -> None:
    i = c.findData(key)
    if i >= 0:
        c.setCurrentIndex(i)
    elif c.isEditable():
        c.setEditText(key)


def combo_value(c: QComboBox) -> str:
    if c.isEditable():
        i = c.findText(c.currentText())
        return c.itemData(i) if i >= 0 else c.currentText().strip()
    return c.currentData()


class FieldListEditor(QGroupBox):
    """Edits `fields` in place and emits `changed` after every edit."""

    changed = Signal()

    def __init__(self, fields: list[FieldSpec], title: str):
        super().__init__(title)
        self.fields = fields
        self._loading = False

        self.flist = QListWidget()
        btns = QVBoxLayout()
        for text, fn in (("추가", self._add), ("삭제", self._delete),
                         ("위로", lambda: self._move(-1)), ("아래로", lambda: self._move(1))):
            b = QPushButton(text)
            b.clicked.connect(fn)
            btns.addWidget(b)
        btns.addStretch()

        self.f_type = combo(list(FIELD_TYPES.items()))
        self.f_name = QLineEdit()
        self.f_size = QSpinBox()
        self.f_size.setRange(1, 1 << 24)
        self.f_endian = combo([("", "규칙 세트 기본값"), ("little", "리틀 엔디언"), ("big", "빅 엔디언")])
        self.f_enc = combo([("", "규칙 세트 기본값")] + [(e, e) for e in ENCODINGS], editable=True)
        self.f_term = QLineEdit()
        self.f_term.setFont(mono_font())
        self.f_len = combo([("1", "1 byte"), ("2", "2 byte"), ("4", "4 byte"), ("varint", "varint")])
        self.f_fmt = combo([("dec", "10진수"), ("hex", "16진수"), ("both", "10진 + 16진"), ("bin", "2진수"),
                            ("s", "초 (unixtime)"), ("ms", "밀리초 (unixtime)")])
        self.f_map = QPlainTextEdit()
        self.f_map.setPlaceholderText("값=이름 (한 줄에 하나)\n예:\n0=OFF\n1=ON\n0xFF=없음")
        self.f_map.setMaximumHeight(90)
        form = QFormLayout()
        form.addRow("타입", self.f_type)
        form.addRow("이름 (선택)", self.f_name)
        form.addRow("크기 (byte)", self.f_size)
        form.addRow("엔디언", self.f_endian)
        form.addRow("인코딩", self.f_enc)
        form.addRow("종료 바이트 (hex)", self.f_term)
        form.addRow("길이 접두 크기", self.f_len)
        form.addRow("표시 형식", self.f_fmt)
        form.addRow("값 이름표", self.f_map)
        self.fbox = QWidget()
        self.fbox.setLayout(form)

        lay = QHBoxLayout(self)
        lay.addWidget(self.flist, 2)
        lay.addLayout(btns)
        lay.addWidget(self.fbox, 3)

        for w in (self.f_name, self.f_term):
            w.textChanged.connect(self._edited)
        for c in (self.f_type, self.f_endian, self.f_len, self.f_fmt):
            c.currentIndexChanged.connect(self._edited)
        self.f_enc.currentTextChanged.connect(self._edited)
        self.f_size.valueChanged.connect(self._edited)
        self.f_map.textChanged.connect(self._edited)
        self.flist.currentRowChanged.connect(self._load)

        self._refresh()
        if self.fields:
            self.flist.setCurrentRow(0)
        self._load(self.flist.currentRow())

    def _refresh(self) -> None:
        row = self.flist.currentRow()
        self.flist.blockSignals(True)
        self.flist.clear()
        for i, f in enumerate(self.fields):
            self.flist.addItem(f"{i + 1}. {f.summary()}")
        self.flist.blockSignals(False)
        if 0 <= row < self.flist.count():
            self.flist.setCurrentRow(row)

    def _add(self) -> None:
        self.fields.append(FieldSpec("uint", size=1))
        self._refresh()
        self.flist.setCurrentRow(len(self.fields) - 1)
        self.changed.emit()

    def _delete(self) -> None:
        r = self.flist.currentRow()
        if 0 <= r < len(self.fields):
            del self.fields[r]
            self._refresh()
            self.flist.setCurrentRow(min(r, len(self.fields) - 1))
            self._load(self.flist.currentRow())
            self.changed.emit()

    def _move(self, d: int) -> None:
        r = self.flist.currentRow()
        j = r + d
        if 0 <= r < len(self.fields) and 0 <= j < len(self.fields):
            self.fields[r], self.fields[j] = self.fields[j], self.fields[r]
            self._refresh()
            self.flist.setCurrentRow(j)
            self.changed.emit()

    def _load(self, row: int) -> None:
        self._loading = True
        has = 0 <= row < len(self.fields)
        self.fbox.setEnabled(has)
        if has:
            f = self.fields[row]
            set_combo(self.f_type, f.type)
            self.f_name.setText(f.name)
            self.f_size.setValue(f.size)
            set_combo(self.f_endian, f.endian)
            set_combo(self.f_enc, f.encoding)
            self.f_term.setText(f.terminator)
            set_combo(self.f_len, f.len_size)
            set_combo(self.f_fmt, f.fmt)
            self.f_map.setPlainText("\n".join(f"{k}={v}" for k, v in f.map.items()))
        self._loading = False
        self._update_widgets()

    def _update_widgets(self) -> None:
        t = self.f_type.currentData()
        self.f_size.setEnabled(t in SIZED)
        self.f_endian.setEnabled(t in ("uint", "int", "float", "bool", "unixtime") or t in PREFIXED)
        self.f_enc.setEnabled(t in TEXT)
        self.f_term.setEnabled(t == "cstring")
        self.f_len.setEnabled(t in PREFIXED)
        self.f_fmt.setEnabled(t in INTS or t == "unixtime")
        self.f_map.setEnabled(t in INTS)

    def _edited(self, *_) -> None:
        if self._loading:
            return
        self._update_widgets()
        row = self.flist.currentRow()
        if not 0 <= row < len(self.fields):
            return
        f = self.fields[row]
        f.type = self.f_type.currentData()
        f.name = self.f_name.text().strip()
        f.size = self.f_size.value()
        f.endian = self.f_endian.currentData()
        f.encoding = combo_value(self.f_enc) or ""
        f.terminator = self.f_term.text().strip() or "00"
        f.len_size = self.f_len.currentData()
        f.fmt = self.f_fmt.currentData()
        f.map = {}
        for line in self.f_map.toPlainText().splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip():
                k = k.strip()
                if k.lower().startswith("0x"):
                    try:
                        k = str(int(k, 16))
                    except ValueError:
                        pass
                f.map[k] = v.strip()
        self.flist.item(row).setText(f"{row + 1}. {f.summary()}")
        self.changed.emit()
