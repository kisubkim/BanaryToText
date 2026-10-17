"""Dialog for editing one Rule and its fields, with a live preview."""

from __future__ import annotations

import copy

from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QLabel, QLineEdit,
                               QPlainTextEdit, QVBoxLayout)

from ..render import RenderOptions, render_text
from ..rules import ACTIONS, Rule, RuleEngine, RuleSet, parse_tag
from .field_editor import FieldListEditor, combo, set_combo
from .hexview import mono_font


class RuleDialog(QDialog):
    def __init__(self, parent, rule: Rule, ruleset: RuleSet, data: bytes):
        super().__init__(parent)
        self.setWindowTitle("규칙 편집")
        self.resize(820, 620)
        self.original = rule  # replaced by the edited copy in the preview
        self.rule = copy.deepcopy(rule)
        self.ruleset = ruleset
        self.data = data
        # Preview with the main window's output options (labels, layout, commas).
        self.render_opts = parent._render_opts() if hasattr(parent, "_render_opts") else RenderOptions()

        self.tag = QLineEdit(self.rule.tag)
        self.tag.setPlaceholderText('예: 09   5F 20   0A??   "STR"')
        self.tag.setFont(mono_font())
        self.tag_msg = QLabel()
        self.name = QLineEdit(self.rule.name)
        self.action = combo(list(ACTIONS.items()))
        set_combo(self.action, self.rule.action)
        self.bracket = combo([("[", "[ ]"), ("{", "{ }")])
        set_combo(self.bracket, self.rule.bracket)
        self.layout = combo([("", "툴바 설정 따름"), ("lines", "줄마다 (항목마다 줄바꿈)"),
                             ("inline", "한 줄로 (쉼표로 이어서)")])
        set_combo(self.layout, self.rule.layout)
        self.layout.setToolTip("이 그룹의 항목을 줄마다 쓸지 한 줄로 이어 쓸지. 툴바의 '그룹' 설정보다 우선합니다.")
        self.comment = QLineEdit(self.rule.comment)
        self.end_tag = QLineEdit(self.rule.end_tag)
        self.end_tag.setPlaceholderText("예: 00   FF FF   (비우면 공통 '그룹 닫기' 규칙으로 닫음)")
        self.end_tag.setFont(mono_font())
        self.end_msg = QLabel()
        self.key = QCheckBox("읽은 값을 다음 값의 이름(키)으로 사용  →  key = 값")
        self.key.setToolTip("예: 09 \"volume\" 00 04 1E → volume = 30\n"
                            "키 바로 다음 항목이 값이 됩니다. 같은 규칙이 연달아 나오면 두 번째는 값으로 읽습니다\n"
                            "(09 \"mode\" 09 \"auto\" → mode = \"auto\"). 키와 값은 개수 블록에서 한 항목으로 셉니다.")
        self.key.setChecked(self.rule.key)
        self.action_help = QLabel()
        self.action_help.setWordWrap(True)

        top = QFormLayout()
        top.addRow("태그 (hex)", self.tag)
        top.addRow("", self.tag_msg)
        top.addRow("이름 (출력 라벨)", self.name)
        top.addRow("동작", self.action)
        top.addRow("", self.action_help)
        top.addRow("키", self.key)
        top.addRow("그룹 괄호", self.bracket)
        top.addRow("그룹 표시", self.layout)
        top.addRow("닫는 태그 (hex)", self.end_tag)
        top.addRow("", self.end_msg)
        top.addRow("메모", self.comment)

        self.fields_editor = FieldListEditor(self.rule.fields, "태그 다음에 읽을 필드 (순서대로)")

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(mono_font())
        self.preview.setMaximumHeight(110)
        pg = QGroupBox("미리보기 (이 태그가 처음 나오는 위치부터, 다른 규칙과 함께 적용)")
        pl = QVBoxLayout()
        pl.addWidget(self.preview)
        pg.setLayout(pl)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        self.ok_button = bb.button(QDialogButtonBox.Ok)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.fields_editor, 1)
        lay.addWidget(pg)
        lay.addWidget(bb)

        for w in (self.tag, self.name, self.comment, self.end_tag):
            w.textChanged.connect(self._changed)
        for c in (self.action, self.bracket, self.layout):
            c.currentIndexChanged.connect(self._changed)
        self.key.toggled.connect(self._changed)
        self.fields_editor.changed.connect(self._changed)
        self._changed()

    def _changed(self, *_):
        self.rule.tag = self.tag.text().strip()
        self.rule.name = self.name.text().strip()
        self.rule.action = self.action.currentData()
        self.rule.bracket = self.bracket.currentData()
        self.rule.comment = self.comment.text()
        self.rule.end_tag = self.end_tag.text().strip()
        self.key.setEnabled(self.rule.action == "value")
        self.rule.key = self.key.isChecked() and self.rule.action == "value"
        self.action_help.setText(_ACTION_HELP.get(self.rule.action, ""))
        is_group = self.rule.action in ("open", "toggle", "len_block", "count_block")
        self.bracket.setEnabled(is_group)
        self.layout.setEnabled(is_group)
        self.rule.layout = self.layout.currentData() if is_group else ""
        is_open = self.rule.action == "open"
        self.end_tag.setEnabled(is_open)
        end_ok = True
        if not is_open:
            self.end_msg.setText("'그룹 열기' 동작에서만 사용합니다.")
        elif not self.rule.end_tag:
            self.end_msg.setText("비어 있음: 공통 '그룹 닫기' 규칙이 이 그룹을 닫습니다.")
        else:
            try:
                ep = parse_tag(self.rule.end_tag)
                self.end_msg.setText(f"✓ 이 그룹이 가장 안쪽일 때만 {len(ep)} byte 닫는 태그로 인식합니다 "
                                     "(다른 규칙보다 우선).")
            except ValueError as e:
                self.end_msg.setText(f"✗ {e}")
                end_ok = False
        try:
            pat = parse_tag(self.rule.tag)
            hits = _count_hits(self.data, pat)
            self.tag_msg.setText(f"✓ {len(pat)} byte 태그, 파일에서 {hits}회 등장" if self.data else
                                 f"✓ {len(pat)} byte 태그")
            self.ok_button.setEnabled(end_ok)
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
                     unknown="hex",
                     rules=[r for r in self.ruleset.rules if r is not self.original] + [self.rule])
        chunk = self.data[pos:pos + 512]
        root = RuleEngine(rs).decode(chunk)
        opts = copy.copy(self.render_opts)
        opts.offsets, opts.raw_hex, opts.max_lines = False, False, 6
        text = render_text(root, chunk, opts)
        self.preview.setPlainText(f"위치 0x{pos:X}:\n{text}")

    def _accept(self) -> None:
        self._changed()
        self.accept()


_ACTION_HELP = {
    "value": "태그 다음의 필드들을 읽어 '이름: 값' 한 줄로 출력합니다. 필드가 없으면 이름만 출력(1 byte → 글자 표시 용도).",
    "open": "그룹(배열/구조체)을 엽니다. 이후 항목들은 닫는 태그(아래에서 지정) 또는 공통 '그룹 닫기' 규칙이 "
            "나올 때까지 안쪽에 들여쓰기 됩니다.",
    "close": "닫는 태그가 지정되지 않은 그룹 중 가장 안쪽 것을 닫습니다. 그룹마다 닫는 값이 다르면 "
             "'그룹 열기' 규칙의 '닫는 태그'를 쓰세요.",
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
