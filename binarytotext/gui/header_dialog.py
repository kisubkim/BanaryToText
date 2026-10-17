"""Dialog for the rule set's position-based header and footer fields."""

from __future__ import annotations

import copy

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QGroupBox, QLabel, QPlainTextEdit, QTabWidget,
                               QVBoxLayout)

from ..model import ERROR, Node
from ..render import RenderOptions, render_text
from ..rules import RuleEngine, RuleSet, fixed_size
from .field_editor import FieldListEditor
from .hexview import mono_font


class HeaderDialog(QDialog):
    def __init__(self, parent, ruleset: RuleSet, data: bytes):
        super().__init__(parent)
        self.setWindowTitle("헤더 / 꼬리 구조")
        self.resize(820, 640)
        self.ruleset = ruleset
        self.data = data
        self.header = copy.deepcopy(ruleset.header)
        self.footer = copy.deepcopy(ruleset.footer)

        help_ = QLabel("태그와 상관없이 <b>위치로</b> 정해지는 부분입니다. 헤더는 파일 맨 앞부터 순서대로, "
                       "꼬리는 파일 맨 끝의 고정 크기 영역으로 읽고, 태그 규칙은 그 사이(본문)에만 적용됩니다. "
                       "비워 두면 쓰지 않습니다.")
        help_.setWordWrap(True)

        self.head_editor = FieldListEditor(self.header, "파일 맨 앞부터 읽을 필드 (순서대로)")
        self.foot_editor = FieldListEditor(self.footer, "파일 맨 끝의 필드 (순서대로, 고정 크기만)")
        tabs = QTabWidget()
        tabs.addTab(self.head_editor, "헤더")
        tabs.addTab(self.foot_editor, "꼬리")
        self.msg = QLabel()
        self.msg.setWordWrap(True)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(mono_font())
        self.preview.setMinimumHeight(170)
        pg = QGroupBox("미리보기 (현재 파일의 헤더와 꼬리)")
        pl = QVBoxLayout(pg)
        pl.addWidget(self.preview)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(help_)
        lay.addWidget(tabs, 1)
        lay.addWidget(self.msg)
        lay.addWidget(pg)
        lay.addWidget(bb)

        self.head_editor.changed.connect(self._changed)
        self.foot_editor.changed.connect(self._changed)
        self._changed()

    def _changed(self) -> None:
        size = fixed_size(self.footer)
        if self.footer and size is None:
            self.msg.setText("✗ 꼬리에는 고정 크기 필드(정수/실수/불리언/고정 문자열/바이트/BCD/Unix 시각/건너뛰기)만 "
                             "쓸 수 있습니다.")
        elif self.footer:
            self.msg.setText(f"✓ 꼬리 = 파일의 마지막 {size} byte")
        else:
            self.msg.setText("")
        if not self.data:
            self.preview.setPlainText("파일을 열면 미리보기가 표시됩니다.")
            return
        rs = copy.deepcopy(self.ruleset)
        rs.header, rs.footer = self.header, self.footer
        root = RuleEngine(rs).decode(self.data)
        # Show only the header/footer groups (and their errors), not the whole body.
        shown = Node(0, len(self.data), root.kind, root.label)
        shown.children = [c for c in root.children
                          if c.label in ("헤더", "꼬리") or (c.kind == ERROR and ("헤더" in c.label or "꼬리" in c.label))]
        text = render_text(shown, self.data, RenderOptions(max_lines=40))
        self.preview.setPlainText(text or "헤더/꼬리가 정의되지 않았습니다.")
