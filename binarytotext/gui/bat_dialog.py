"""Dialog that writes a .bat running `btt batch` with the main window's current settings."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QPlainTextEdit, QPushButton, QRadioButton, QVBoxLayout)

from .. import batch, batfile
from .hexview import mono_font


def default_runner() -> tuple[str, str, str]:
    """(btt.exe path, python, source dir): btt.exe beside the frozen app, else run from source."""
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).with_name("btt.exe")), "", ""
    return "", sys.executable, str(Path(__file__).resolve().parents[2])


class BatDialog(QDialog):
    """`base` holds the method and output options of the main window; the dialog adds inputs and output."""

    def __init__(self, parent, base: batfile.BatSpec, settings: QSettings, start_dir: str = ""):
        super().__init__(parent)
        self.setWindowTitle("bat 파일 만들기")
        self.resize(820, 760)
        self.base, self.settings = base, settings
        self.saved_path: Path | None = None

        # --- inputs
        inp = QGroupBox("변환할 파일")
        il = QVBoxLayout(inp)
        self.drop = QRadioButton("bat 파일 아이콘에 끌어다 놓은 파일·폴더 (여러 개 가능)")
        self.fixed = QRadioButton("항상 이 파일·폴더")
        grp = QButtonGroup(self)
        grp.addButton(self.drop)
        grp.addButton(self.fixed)
        (self.fixed if settings.value("bat_fixed", False, type=bool) else self.drop).setChecked(True)
        il.addWidget(self.drop)
        row = QHBoxLayout()
        row.addWidget(self.fixed)
        self.input = QLineEdit(settings.value("bat_input", "") or start_dir)
        self.input.setPlaceholderText("폴더 또는 파일 경로")
        row.addWidget(self.input, 1)
        for text, slot in (("폴더…", self._choose_folder), ("파일…", self._choose_file)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        il.addLayout(row)
        row = QHBoxLayout()
        self.recursive = QCheckBox("하위 폴더 포함")
        self.recursive.setChecked(settings.value("bat_recursive", False, type=bool))
        self.include = QLineEdit(settings.value("bat_include", "*"))
        self.include.setPlaceholderText("*.bin;*.dat")
        self.include.setToolTip("폴더에서 고를 파일 패턴. ; 로 여러 개")
        row.addWidget(self.recursive)
        row.addWidget(QLabel("폴더에서 고를 파일"))
        row.addWidget(self.include, 1)
        il.addLayout(row)

        # --- method (from the main window)
        meth = QGroupBox("해석 방식 (현재 화면 설정)")
        ml = QVBoxLayout(meth)
        desc = QLabel(base.description)
        desc.setWordWrap(True)
        ml.addWidget(desc)
        self.use_lib = QCheckBox(f"규칙 폴더에서 파일마다 자동 선택 ({base.library})")
        self.use_lib.setVisible(bool(base.library))
        self.use_lib.setChecked(bool(base.library) and settings.value("bat_use_lib", True, type=bool))
        self.fallback = QCheckBox("맞는 규칙 세트가 없으면 위 해석 방식으로 변환 (끄면 건너뜀)")
        self.fallback.setVisible(bool(base.library))
        self.fallback.setChecked(settings.value("bat_fallback", True, type=bool))
        ml.addWidget(self.use_lib)
        ml.addWidget(self.fallback)
        out_opts = QLabel("출력 모양(오프셋, 이름, 따옴표, 쉼표, 그룹, 실수 유효 숫자)도 현재 화면 설정을 씁니다.")
        out_opts.setWordWrap(True)
        ml.addWidget(out_opts)

        # --- output
        outg = QGroupBox("저장")
        of = QFormLayout(outg)
        orow = QHBoxLayout()
        self.out_dir = QLineEdit(settings.value("bat_out_dir", ""))
        self.out_dir.setPlaceholderText("비우면 각 원본 파일 옆에 저장")
        ob = QPushButton("찾기…")
        ob.clicked.connect(self._choose_out)
        orow.addWidget(self.out_dir, 1)
        orow.addWidget(ob)
        of.addRow("출력 폴더", orow)
        krow = QHBoxLayout()
        self.as_text = QRadioButton("텍스트")
        self.as_json = QRadioButton("JSON")
        (self.as_json if settings.value("bat_json", False, type=bool) else self.as_text).setChecked(True)
        krow.addWidget(self.as_text)
        krow.addWidget(self.as_json)
        krow.addStretch(1)
        of.addRow("형식", krow)
        self.pattern = QLineEdit(settings.value("bat_pattern", ""))
        self.pattern.setPlaceholderText("비우면 {name}.txt (JSON 은 {name}.json)")
        self.pattern.setToolTip("{name} = 원본 파일 이름(확장자 포함), {stem} = 확장자 뺀 이름, {ext} = 확장자")
        of.addRow("파일 이름", self.pattern)
        self.overwrite = QCheckBox("같은 이름이 있으면 덮어쓰기 (끄면 '이름 (2)' 로 저장)")
        self.overwrite.setChecked(settings.value("bat_overwrite", False, type=bool))
        of.addRow("", self.overwrite)

        # --- runner
        rung = QGroupBox("실행")
        rf = QFormLayout(rung)
        btt, self.python, self.source_dir = default_runner()
        brow = QHBoxLayout()
        self.btt = QLineEdit(settings.value("bat_btt", "") or btt)
        self.btt.setPlaceholderText("비우면 소스로 실행 (python -m binarytotext)" if self.python else "btt.exe 경로")
        bb = QPushButton("찾기…")
        bb.clicked.connect(self._choose_btt)
        brow.addWidget(self.btt, 1)
        brow.addWidget(bb)
        rf.addRow("btt.exe", brow)
        self.beside = QCheckBox("bat 파일과 같은 폴더의 btt.exe 사용 (프로그램 폴더째 옮겨도 동작)")
        self.beside.setChecked(settings.value("bat_beside", False, type=bool))
        rf.addRow("", self.beside)
        self.pause = QCheckBox("끝나면 창을 닫지 않고 결과 보여 주기 (pause)")
        self.pause.setChecked(settings.value("bat_pause", True, type=bool))
        rf.addRow("", self.pause)

        # --- preview + buttons
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(mono_font())
        self.msg = QLabel()
        self.msg.setWordWrap(True)
        brow = QHBoxLayout()
        self.save_btn = QPushButton("bat 저장…")
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self.save)
        close = QPushButton("닫기")
        close.clicked.connect(self.close)
        brow.addWidget(self.msg, 1)
        brow.addWidget(self.save_btn)
        brow.addWidget(close)

        lay = QVBoxLayout(self)
        for w in (inp, meth, outg, rung):
            lay.addWidget(w)
        lay.addWidget(QLabel("미리보기"))
        lay.addWidget(self.preview, 1)
        lay.addLayout(brow)

        for w in (self.input, self.include, self.out_dir, self.pattern, self.btt):
            w.textChanged.connect(self._update)
        for c in (self.drop, self.fixed, self.recursive, self.use_lib, self.fallback, self.as_json, self.overwrite,
                  self.beside, self.pause):
            c.toggled.connect(self._update)
        self._update()

    # ------------------------------------------------------------------ spec
    def spec(self) -> batfile.BatSpec:
        s = copy.deepcopy(self.base)
        s.inputs = [self.input.text().strip()] if self.fixed.isChecked() and self.input.text().strip() else []
        s.recursive = self.recursive.isChecked()
        s.include = self.include.text().strip() or "*"
        if not (s.library and self.use_lib.isChecked()):
            s.library = ""
        elif not self.fallback.isChecked():
            s.rules, s.fmt, s.fmt_opts = "", "", {}  # library only: files without a match are skipped
        s.as_json = self.as_json.isChecked()
        s.out_dir = self.out_dir.text().strip()
        s.pattern = self.pattern.text().strip()
        s.overwrite = self.overwrite.isChecked()
        s.btt_beside = self.beside.isChecked()
        s.btt = "" if s.btt_beside else self.btt.text().strip()
        s.python, s.source_dir = (self.python, self.source_dir) if not (s.btt or s.btt_beside) else ("", "")
        s.pause = self.pause.isChecked()
        return s

    def _problem(self, s: batfile.BatSpec) -> str:
        if self.fixed.isChecked() and not s.inputs:
            return "변환할 파일·폴더 경로를 넣으세요"
        if s.pattern and (err := batch.check_pattern(s.pattern)):
            return f"파일 이름: {err}"
        if s.btt and not Path(s.btt).is_file():
            return f"btt.exe 가 없습니다: {s.btt} (bat 은 만들 수 있지만 실행 전에 경로를 확인하세요)"
        return batfile.check(s)

    def _update(self, *_) -> None:
        self.input.setEnabled(self.fixed.isChecked())
        self.fallback.setEnabled(self.use_lib.isChecked())
        self.btt.setEnabled(not self.beside.isChecked())
        s = self.spec()
        problem = self._problem(s)
        self.msg.setText(f"⚠ {problem}" if problem else "")
        blocking = problem and not problem.startswith("btt.exe 가 없습니다")
        self.save_btn.setEnabled(not blocking)
        self.preview.setPlainText(batfile.bat_text(s).replace("\r\n", "\n"))

    # ------------------------------------------------------------------ actions
    def save(self) -> Path | None:
        s = self.spec()
        start = self.settings.value("bat_dir", "") or str(Path.home())
        p, _ = QFileDialog.getSaveFileName(self, "bat 파일 저장", str(Path(start) / "변환.bat"), "배치 파일 (*.bat)")
        if not p:
            return None
        path = Path(p)
        enc = batfile.write_bat(path, batfile.bat_text(s))
        self.saved_path = path
        self._remember(path)
        self.msg.setText(f"저장했습니다: {path}  ({enc})")
        return path

    def _remember(self, path: Path) -> None:
        st = self.settings
        st.setValue("bat_dir", str(path.parent))
        st.setValue("bat_fixed", self.fixed.isChecked())
        st.setValue("bat_input", self.input.text().strip())
        st.setValue("bat_recursive", self.recursive.isChecked())
        st.setValue("bat_include", self.include.text().strip())
        st.setValue("bat_use_lib", self.use_lib.isChecked())
        st.setValue("bat_fallback", self.fallback.isChecked())
        st.setValue("bat_out_dir", self.out_dir.text().strip())
        st.setValue("bat_json", self.as_json.isChecked())
        st.setValue("bat_pattern", self.pattern.text().strip())
        st.setValue("bat_overwrite", self.overwrite.isChecked())
        st.setValue("bat_btt", self.btt.text().strip())
        st.setValue("bat_beside", self.beside.isChecked())
        st.setValue("bat_pause", self.pause.isChecked())

    def _choose_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "변환할 폴더", self.input.text())
        if d:
            self.fixed.setChecked(True)
            self.input.setText(d)

    def _choose_file(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "변환할 파일", self.input.text())
        if f:
            self.fixed.setChecked(True)
            self.input.setText(f)

    def _choose_out(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "출력 폴더", self.out_dir.text())
        if d:
            self.out_dir.setText(d)

    def _choose_btt(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "btt.exe 찾기", self.btt.text(), "btt.exe (btt.exe);;실행 파일 (*.exe)")
        if f:
            self.btt.setText(f)
