"""Batch conversion dialog: a file list, output naming and a background run with a result table."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QMessageBox,
                               QProgressBar, QPushButton, QRadioButton, QSplitter, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from .. import batch

STATUS_COLOR = {batch.OK: None, batch.SKIPPED: Qt.darkYellow, batch.FAILED: Qt.red}


class BatchJob(QThread):
    one_done = Signal(int, object)
    all_done = Signal(object, object)

    def __init__(self, files, settings: batch.Settings):
        super().__init__()
        self.setStackSize(64 << 20)
        self.files, self.settings = files, settings
        self.stop = False

    def run(self) -> None:
        try:
            results, warnings = batch.run(self.files, self.settings, self.one_done.emit, lambda: self.stop)
        except Exception as e:
            results, warnings = [], [str(e)]
        self.all_done.emit(results, warnings)


class BatchDialog(QDialog):
    """`base` is a batch.Settings snapshot of the main window (method, options, preprocess, render)."""

    def __init__(self, parent, base: batch.Settings, base_desc: str, settings: QSettings,
                 paths: list[str] | None = None):
        super().__init__(parent)
        self.setWindowTitle("여러 파일 변환")
        self.setAcceptDrops(True)
        self.base, self.settings = base, settings
        self.job: BatchJob | None = None
        self.files: list[tuple[Path, Path]] = []
        self.results: list[batch.Result] = []

        # --- inputs
        inp = QGroupBox("변환할 파일")
        il = QVBoxLayout(inp)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setToolTip("파일이나 폴더를 여기로 끌어다 놓아도 됩니다")
        il.addWidget(self.list, 1)
        row = QHBoxLayout()
        for text, slot in (("파일 추가…", self._add_files), ("폴더 추가…", self._add_folder),
                           ("선택 제거", self._remove), ("비우기", self._clear)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        il.addLayout(row)
        row = QHBoxLayout()
        self.recursive = QCheckBox("하위 폴더 포함")
        self.recursive.setChecked(settings.value("batch_recursive", False, type=bool))
        self.include = QLineEdit(settings.value("batch_include", "*"))
        self.include.setPlaceholderText("*.bin;*.dat")
        self.include.setToolTip("폴더에서 고를 파일 패턴. ; 로 여러 개")
        row.addWidget(self.recursive)
        row.addWidget(QLabel("폴더에서 고를 파일"))
        row.addWidget(self.include, 1)
        il.addLayout(row)
        self.count_label = QLabel()
        il.addWidget(self.count_label)
        self.recursive.toggled.connect(self._refresh_count)
        self.include.editingFinished.connect(self._refresh_count)

        # --- method
        meth = QGroupBox("해석 방식")
        ml = QVBoxLayout(meth)
        self.base_label = QLabel()
        self.base_label.setWordWrap(True)
        ml.addWidget(self.base_label)
        self.use_lib = QCheckBox()
        self.fallback = QCheckBox("맞는 규칙 세트가 없으면 현재 화면 설정으로 변환 (끄면 건너뜀)")
        self.fallback.setChecked(settings.value("batch_fallback", True, type=bool))
        self.use_lib.toggled.connect(self.fallback.setEnabled)
        self.set_base(base, base_desc, settings.value("batch_use_lib", True, type=bool))
        ml.addWidget(self.use_lib)
        ml.addWidget(self.fallback)

        # --- output
        outg = QGroupBox("저장")
        of = QFormLayout(outg)
        orow = QHBoxLayout()
        self.out_dir = QLineEdit(settings.value("batch_out_dir", ""))
        self.out_dir.setPlaceholderText("비우면 각 원본 파일 옆에 저장")
        ob = QPushButton("찾기…")
        ob.clicked.connect(self._choose_out)
        orow.addWidget(self.out_dir, 1)
        orow.addWidget(ob)
        of.addRow("출력 폴더", orow)
        krow = QHBoxLayout()
        self.as_text = QRadioButton("텍스트")
        self.as_json = QRadioButton("JSON")
        (self.as_json if settings.value("batch_json", False, type=bool) else self.as_text).setChecked(True)
        krow.addWidget(self.as_text)
        krow.addWidget(self.as_json)
        krow.addStretch(1)
        of.addRow("형식", krow)
        self.pattern = QLineEdit(settings.value("batch_pattern", "") or batch.default_pattern(self.as_json.isChecked()))
        self.pattern.setToolTip("{name} = 원본 파일 이름(확장자 포함), {stem} = 확장자 뺀 이름, {ext} = 확장자\n"
                                "예: {stem}_해석.txt")
        self.as_json.toggled.connect(self._kind_changed)
        self.pattern.textChanged.connect(self._update_example)
        of.addRow("파일 이름", self.pattern)
        self.example = QLabel()
        of.addRow("", self.example)
        self.overwrite = QCheckBox("같은 이름이 있으면 덮어쓰기 (끄면 '이름 (2)' 로 저장)")
        self.overwrite.setChecked(settings.value("batch_overwrite", False, type=bool))
        of.addRow("", self.overwrite)

        # --- run + results
        rrow = QHBoxLayout()
        self.run_btn = QPushButton("변환 시작")
        self.run_btn.setDefault(True)
        self.run_btn.clicked.connect(self.start)
        self.stop_btn = QPushButton("중지")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)
        self.open_btn = QPushButton("출력 폴더 열기")
        self.open_btn.clicked.connect(self._open_out)
        self.progress = QProgressBar()
        rrow.addWidget(self.run_btn)
        rrow.addWidget(self.stop_btn)
        rrow.addWidget(self.progress, 1)
        rrow.addWidget(self.open_btn)
        self.status = QLabel()
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["원본", "상태", "해석 방식", "해석률", "저장 위치 / 사유"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        h = self.table.horizontalHeader()
        for c in range(4):
            h.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        h.setStretchLastSection(True)
        self.table.cellDoubleClicked.connect(self._open_result)
        self.table.setToolTip("두 번 클릭하면 원본 파일을 메인 창에서 엽니다")

        top = QWidget()
        tl = QHBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        right = QVBoxLayout()
        right.addWidget(meth)
        right.addWidget(outg)
        right.addStretch(1)
        tl.addWidget(inp, 1)
        tl.addLayout(right, 1)
        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addLayout(rrow)
        bl.addWidget(self.status)
        bl.addWidget(self.table, 1)
        split = QSplitter(Qt.Vertical)
        split.addWidget(top)
        split.addWidget(bottom)
        split.setSizes([380, 320])
        close = QPushButton("닫기")
        close.clicked.connect(self.close)
        lay = QVBoxLayout(self)
        lay.addWidget(split, 1)
        crow = QHBoxLayout()
        crow.addStretch(1)
        crow.addWidget(close)
        lay.addLayout(crow)
        self.resize(1100, 760)

        self.inputs: list[str] = []
        if paths:
            self.add_paths(paths)
        self._refresh_count()
        self._update_example()

    def set_base(self, base: batch.Settings, desc: str, use_lib: bool | None = None) -> None:
        """Take a fresh snapshot of the main window settings (dialog reopened)."""
        self.base = base
        self.base_label.setText(f"현재 화면 설정: {desc}")
        has = bool(base.library_dir)
        self.use_lib.setText("규칙 폴더에서 파일마다 자동 선택" +
                             (f"  ({Path(base.library_dir).name})" if has else "  (규칙 폴더 지정 안 됨)"))
        self.use_lib.setToolTip(base.library_dir)
        self.use_lib.setEnabled(has)
        if use_lib is None:
            use_lib = self.settings.value("batch_use_lib", True, type=bool)
        self.use_lib.setChecked(has and use_lib)
        self.fallback.setEnabled(self.use_lib.isChecked())

    # ------------------------------------------------------------ inputs
    def add_paths(self, paths: list[str]) -> None:
        for p in paths:
            p = os.path.normpath(p)
            if p not in self.inputs:
                self.inputs.append(p)
                self.list.addItem(p + (os.sep + "  (폴더)" if os.path.isdir(p) else ""))
        self._refresh_count()

    def _add_files(self) -> None:
        ps, _ = QFileDialog.getOpenFileNames(self, "변환할 파일", self.settings.value("last_dir", ""))
        self.add_paths(ps)

    def _add_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "변환할 폴더", self.settings.value("last_dir", ""))
        if d:
            self.add_paths([d])

    def _remove(self) -> None:
        for row in sorted((self.list.row(i) for i in self.list.selectedItems()), reverse=True):
            self.list.takeItem(row)
            del self.inputs[row]
        self._refresh_count()

    def _clear(self) -> None:
        self.list.clear()
        self.inputs.clear()
        self._refresh_count()

    def _collect(self) -> list[tuple[Path, Path]]:
        return batch.collect(self.inputs, self.recursive.isChecked(), self.include.text(), self.out_dir.text().strip())

    def _refresh_count(self, *_) -> None:
        self.files = self._collect()
        self.count_label.setText(f"변환 대상 {len(self.files)}개 파일")
        self._update_example()

    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        self.add_paths([u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile()])

    # ------------------------------------------------------------ output
    def _choose_out(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "출력 폴더", self.out_dir.text() or self.settings.value("last_dir", ""))
        if d:
            self.out_dir.setText(os.path.normpath(d))
            self._refresh_count()

    def _kind_changed(self, as_json: bool) -> None:
        old, new = batch.default_pattern(not as_json), batch.default_pattern(as_json)
        text = self.pattern.text()
        if text == old:
            self.pattern.setText(new)
        elif text.endswith(Path(old).suffix):
            self.pattern.setText(text[:-len(Path(old).suffix)] + Path(new).suffix)

    def _update_example(self, *_) -> None:
        err = batch.check_pattern(self.pattern.text())
        if err:
            self.example.setText(f"<span style='color:#c00'>{err}</span>")
        else:
            src = self.files[0][0] if self.files else Path("data.bin")
            self.example.setText(f"예: {src.name} → {batch.output_name(src, self.pattern.text())}")
        self.run_btn.setEnabled(not err and self.job is None)

    def _open_out(self) -> None:
        d = self.out_dir.text().strip()
        if not d and self.results:
            d = str(next((r.out.parent for r in self.results if r.out), ""))
        if d and Path(d).is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    # ------------------------------------------------------------ run
    def settings_for_run(self) -> batch.Settings:
        st = batch.Settings(**{k: getattr(self.base, k) for k in batch.Settings.__dataclass_fields__})
        use_lib = self.use_lib.isChecked()
        st.library_dir = self.base.library_dir if use_lib else ""
        st.fallback = self.fallback.isChecked() or not use_lib
        st.as_json = self.as_json.isChecked()
        st.out_dir = self.out_dir.text().strip()
        st.pattern = self.pattern.text().strip()
        st.overwrite = self.overwrite.isChecked()
        return st

    def _save_settings(self) -> None:
        s = self.settings
        s.setValue("batch_recursive", self.recursive.isChecked())
        s.setValue("batch_include", self.include.text())
        s.setValue("batch_use_lib", self.use_lib.isChecked())
        s.setValue("batch_fallback", self.fallback.isChecked())
        s.setValue("batch_out_dir", self.out_dir.text().strip())
        s.setValue("batch_json", self.as_json.isChecked())
        s.setValue("batch_pattern", self.pattern.text().strip())
        s.setValue("batch_overwrite", self.overwrite.isChecked())

    def start(self) -> None:
        self._refresh_count()
        if not self.files:
            QMessageBox.information(self, "여러 파일 변환", "변환할 파일이 없습니다. 파일이나 폴더를 추가하세요.")
            return
        d = self.out_dir.text().strip()
        if d and not Path(d).is_dir():
            try:
                Path(d).mkdir(parents=True)
            except OSError as e:
                QMessageBox.warning(self, "출력 폴더", f"출력 폴더를 만들 수 없습니다: {e}")
                return
        self._save_settings()
        self.results = []
        self.table.setRowCount(0)
        self.progress.setRange(0, len(self.files))
        self.progress.setValue(0)
        self.status.setText("변환 중…")
        self.job = BatchJob(self.files, self.settings_for_run())
        self.job.one_done.connect(self._on_one)
        self.job.all_done.connect(self._on_all)
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.job.start()

    def _stop(self) -> None:
        if self.job:
            self.job.stop = True
            self.status.setText("중지하는 중… (지금 파일까지 처리)")

    def _on_one(self, i: int, r: batch.Result) -> None:
        self.results.append(r)
        row = self.table.rowCount()
        self.table.insertRow(row)
        cells = [r.src.name, batch.STATUS_TEXT[r.status], r.method,
                 f"{r.coverage:.0%}" if r.status == batch.OK else "",
                 str(r.out) if r.out else r.message]
        for c, text in enumerate(cells):
            it = QTableWidgetItem(text)
            it.setToolTip(str(r.src) if c == 0 else text)
            if STATUS_COLOR[r.status] is not None:
                it.setForeground(STATUS_COLOR[r.status])
            self.table.setItem(row, c, it)
        self.progress.setValue(i + 1)

    def _on_all(self, results, warnings) -> None:
        self.job = None
        self.stop_btn.setEnabled(False)
        self._update_example()
        msg = batch.summary(results, len(self.files))
        if warnings:
            msg += "  /  경고: " + "; ".join(warnings)
        self.status.setText(msg)

    def _open_result(self, row: int, _col: int) -> None:
        if row < len(self.results) and hasattr(self.parent(), "open_file"):
            self.parent().open_file(str(self.results[row].src))

    def closeEvent(self, e) -> None:
        if self.job:
            self.job.stop = True
            self.job.wait()
        self._save_settings()
        super().closeEvent(e)
