"""Dialog for editing one Rule and its fields, with a live preview."""

from __future__ import annotations

import copy


from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QPlainTextEdit, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget)

from ..render import RenderOptions, render_text
from ..rules import ACTIONS, ENCODINGS, FIELD_TYPES, FieldSpec, Rule, RuleEngine, RuleSet, parse_tag
from .hexview import mono_font

SIZED = {"uint", "int", "float", "bool", "fstring", "bytes", "bcd", "skip", "unixtime"}
TEXT = {"cstring", "fstring", "pstring", "rest"}
PREFIXED = {"pstring", "pbytes"}
INTS = {"uint", "int", "varint", "svarint", "bool"}


def _combo(items: list[tuple[str, str]], editable: bool = False) -> QComboBox:
    c = QComboBox()
    c.setEditable(editable)
    for key, label in items:
        c.addItem(label, key)
    return c


def _set_combo(c: QComboBox, key: str) -> None:
    i = c.findData(key)
    if i >= 0:
        c.setCurrentIndex(i)
    elif c.isEditable():
        c.setEditText(key)


def _combo_value(c: QComboBox) -> str:
    if c.isEditable():
        i = c.findText(c.currentText())
        return c.itemData(i) if i >= 0 else c.currentText().strip()
    return c.currentData()


class RuleDialog(QDialog):
    def __init__(self, parent, rule: Rule, ruleset: RuleSet, data: bytes):
        super().__init__(parent)
        self.setWindowTitle("규칙 편집")
        self.resize(820, 620)
        self.rule = copy.deepcopy(rule)
        self.ruleset = ruleset
        self.data = data
        self._loading = False

        self.tag = QLineEdit(self.rule.tag)
        self.tag.setPlaceholderText('예: 09   5F 20   0A??   "STR"')
        self.tag.setFont(mono_font())
        self.tag_msg = QLabel()
        self.name = QLineEdit(self.rule.name)
        self.action = _combo(list(ACTIONS.items()))
        _set_combo(self.action, self.rule.action)
        self.bracket = _combo([("[", "[ ]"), ("{", "{ }")])
        _set_combo(self.bracket, self.rule.bracket)
        self.comment = QLineEdit(self.rule.comment)
        self.action_help = QLabel()
        self.action_help.setWordWrap(True)

        top = QFormLayout()
        top.addRow("태그 (hex)", self.tag)
        top.addRow("", self.tag_msg)
        top.addRow("이름 (출력 라벨)", self.name)
        top.addRow("동작", self.action)
        top.addRow("", self.action_help)
        top.addRow("그룹 괄호", self.bracket)
        top.addRow("메모", self.comment)

        # Fields list + editor
        self.flist = QListWidget()
        btns = QVBoxLayout()
        for text, fn in (("추가", self._add_field), ("삭제", self._del_field),
                         ("위로", lambda: self._move(-1)), ("아래로", lambda: self._move(1))):
            b = QPushButton(text)
            b.clicked.connect(fn)
            btns.addWidget(b)
        btns.addStretch()

        self.f_type = _combo(list(FIELD_TYPES.items()))
        self.f_name = QLineEdit()
        self.f_size = QSpinBox()
        self.f_size.setRange(1, 1 << 24)
        self.f_endian = _combo([("", "규칙 세트 기본값"), ("little", "리틀 엔디언"), ("big", "빅 엔디언")])
        self.f_enc = _combo([("", "규칙 세트 기본값")] + [(e, e) for e in ENCODINGS], editable=True)
        self.f_term = QLineEdit()
        self.f_term.setFont(mono_font())
        self.f_len = _combo([("1", "1 byte"), ("2", "2 byte"), ("4", "4 byte"), ("varint", "varint")])
        self.f_fmt = _combo([("dec", "10진수"), ("hex", "16진수"), ("both", "10진 + 16진"), ("bin", "2진수"),
                             ("s", "초 (unixtime)"), ("ms", "밀리초 (unixtime)")])
        self.f_map = QPlainTextEdit()
        self.f_map.setPlaceholderText("값=이름 (한 줄에 하나)\n예:\n0=OFF\n1=ON\n0xFF=없음")
        self.f_map.setMaximumHeight(90)
        fform = QFormLayout()
        fform.addRow("타입", self.f_type)
        fform.addRow("이름 (선택)", self.f_name)
        fform.addRow("크기 (byte)", self.f_size)
        fform.addRow("엔디언", self.f_endian)
        fform.addRow("인코딩", self.f_enc)
        fform.addRow("종료 바이트 (hex)", self.f_term)
        fform.addRow("길이 접두 크기", self.f_len)
        fform.addRow("표시 형식", self.f_fmt)
        fform.addRow("값 이름표", self.f_map)
        self.fbox = QWidget()
        self.fbox.setLayout(fform)

        fl = QHBoxLayout()
        fl.addWidget(self.flist, 2)
        fl.addLayout(btns)
        fl.addWidget(self.fbox, 3)
        fgroup = QGroupBox("태그 다음에 읽을 필드 (순서대로)")
        fgroup.setLayout(fl)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(mono_font())
        self.preview.setMaximumHeight(110)
        pg = QGroupBox("미리보기 (파일에서 이 태그가 처음 나오는 위치에 이 규칙만 적용)")
        pl = QVBoxLayout()
        pl.addWidget(self.preview)
        pg.setLayout(pl)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        self.ok_button = bb.button(QDialogButtonBox.Ok)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(fgroup, 1)
        lay.addWidget(pg)
        lay.addWidget(bb)

        for w in (self.tag, self.name, self.comment, self.f_name, self.f_term):
            w.textChanged.connect(self._changed)
        for c in (self.action, self.bracket, self.f_type, self.f_endian, self.f_len, self.f_fmt):
            c.currentIndexChanged.connect(self._changed)
        self.f_enc.currentTextChanged.connect(self._changed)
        self.f_size.valueChanged.connect(self._changed)
        self.f_map.textChanged.connect(self._changed)
        self.flist.currentRowChanged.connect(self._load_field)

        self._refresh_list()
        if self.rule.fields:
            self.flist.setCurrentRow(0)
        self._load_field(self.flist.currentRow())
        self._changed()

    # ---- fields list ----
    def _refresh_list(self) -> None:
        row = self.flist.currentRow()
        self.flist.blockSignals(True)
        self.flist.clear()
        for i, f in enumerate(self.rule.fields):
            self.flist.addItem(f"{i + 1}. {f.summary()}")
        self.flist.blockSignals(False)
        if 0 <= row < self.flist.count():
            self.flist.setCurrentRow(row)

    def _add_field(self) -> None:
        self.rule.fields.append(FieldSpec("uint", size=1))
        self._refresh_list()
        self.flist.setCurrentRow(len(self.rule.fields) - 1)
        self._changed()

    def _del_field(self) -> None:
        r = self.flist.currentRow()
        if 0 <= r < len(self.rule.fields):
            del self.rule.fields[r]
            self._refresh_list()
            self.flist.setCurrentRow(min(r, len(self.rule.fields) - 1))
            self._load_field(self.flist.currentRow())
            self._changed()

    def _move(self, d: int) -> None:
        r = self.flist.currentRow()
        j = r + d
        if 0 <= r < len(self.rule.fields) and 0 <= j < len(self.rule.fields):
            f = self.rule.fields
            f[r], f[j] = f[j], f[r]
            self._refresh_list()
            self.flist.setCurrentRow(j)
            self._changed()

    def _load_field(self, row: int) -> None:
        self._loading = True
        has = 0 <= row < len(self.rule.fields)
        self.fbox.setEnabled(has)
        if has:
            f = self.rule.fields[row]
            _set_combo(self.f_type, f.type)
            self.f_name.setText(f.name)
            self.f_size.setValue(f.size)
            _set_combo(self.f_endian, f.endian)
            _set_combo(self.f_enc, f.encoding)
            self.f_term.setText(f.terminator)
            _set_combo(self.f_len, f.len_size)
            _set_combo(self.f_fmt, f.fmt)
            self.f_map.setPlainText("\n".join(f"{k}={v}" for k, v in f.map.items()))
        self._loading = False
        self._update_field_widgets()

    def _update_field_widgets(self) -> None:
        t = self.f_type.currentData()
        self.f_size.setEnabled(t in SIZED)
        self.f_endian.setEnabled(t in ("uint", "int", "float", "bool", "unixtime") or t in PREFIXED)
        self.f_enc.setEnabled(t in TEXT)
        self.f_term.setEnabled(t == "cstring")
        self.f_len.setEnabled(t in PREFIXED)
        self.f_fmt.setEnabled(t in INTS or t == "unixtime")
        self.f_map.setEnabled(t in INTS)

    def _store_field(self) -> None:
        row = self.flist.currentRow()
        if self._loading or not 0 <= row < len(self.rule.fields):
            return
        f = self.rule.fields[row]
        f.type = self.f_type.currentData()
        f.name = self.f_name.text().strip()
        f.size = self.f_size.value()
        f.endian = self.f_endian.currentData()
        f.encoding = _combo_value(self.f_enc) or ""
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

    # ---- whole rule ----
    def _changed(self, *_):
        if self._loading:
            return
        self._store_field()
        self._update_field_widgets()
        self.rule.tag = self.tag.text().strip()
        self.rule.name = self.name.text().strip()
        self.rule.action = self.action.currentData()
        self.rule.bracket = self.bracket.currentData()
        self.rule.comment = self.comment.text()
        self.action_help.setText(_ACTION_HELP.get(self.rule.action, ""))
        self.bracket.setEnabled(self.rule.action in ("open", "toggle", "len_block", "count_block"))
        try:
            pat = parse_tag(self.rule.tag)
            hits = _count_hits(self.data, pat)
            self.tag_msg.setText(f"✓ {len(pat)} byte 태그, 파일에서 {hits}회 등장" if self.data else
                                 f"✓ {len(pat)} byte 태그")
            self.ok_button.setEnabled(True)
        except ValueError as e:
            self.tag_msg.setText(f"✗ {e}")
            self.ok_button.setEnabled(False)
            self.preview.setPlainText("")
            return
        self._preview(pat)

    def _preview(self, pat) -> None:
        pos = _first_hit(self.data, pat)
        if pos < 0:
            self.preview.setPlainText("이 태그는 파일에 없습니다.")
            return
        rs = RuleSet(name="미리보기", endian=self.ruleset.endian, encoding=self.ruleset.encoding,
                     unknown="hex", rules=[self.rule])
        chunk = self.data[pos:pos + 512]
        root = RuleEngine(rs).decode(chunk)
        text = render_text(root, chunk, RenderOptions(offsets=False, max_lines=6))
        self.preview.setPlainText(f"위치 0x{pos:X}:\n{text}")

    def _accept(self) -> None:
        self._changed()
        self.accept()


_ACTION_HELP = {
    "value": "태그 다음의 필드들을 읽어 '이름: 값' 한 줄로 출력합니다. 필드가 없으면 이름만 출력(1 byte → 글자 표시 용도).",
    "open": "그룹(배열/구조체)을 엽니다. 이후 항목들은 '그룹 닫기' 규칙이 나올 때까지 안쪽에 들여쓰기 됩니다.",
    "close": "가장 안쪽의 열린 그룹을 닫습니다.",
    "toggle": "같은 태그가 처음 나오면 그룹을 열고, 다음에 나오면 닫습니다 (예: 0A ... 0A).",
    "len_block": "첫 필드(정수)가 길이입니다. 그만큼의 byte를 그룹 안쪽 내용으로 해석합니다.",
    "count_block": "첫 필드(정수)가 항목 개수입니다. 그만큼의 항목을 그룹 안쪽으로 해석합니다.",
    "end": "이 태그에서 해석을 멈춥니다. 나머지는 미해석으로 표시됩니다.",
}


def _first_hit(data: bytes, pat, start: int = 0) -> int:
    if not data:
        return -1
    anchor = next(i for i, b in enumerate(pat) if b is not None)
    while True:
        p = data.find(bytes([pat[anchor]]), start + anchor)
        if p < 0:
            return -1
        s = p - anchor
        if s >= 0 and s + len(pat) <= len(data) and all(
                b is None or data[s + i] == b for i, b in enumerate(pat)):
            return s
        start = s + 1


def _count_hits(data: bytes, pat, cap: int = 100000) -> int:
    if None not in pat:
        return data.count(bytes(pat))
    n, s = 0, 0
    while n < cap:
        p = _first_hit(data, pat, s)
        if p < 0:
            break
        n += 1
        s = p + 1
    return n
