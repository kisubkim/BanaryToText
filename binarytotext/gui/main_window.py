"""Main window: hex view | rules / format options / analysis | text and tree output."""

from __future__ import annotations

import copy
import os
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QEvent, QRect, QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QKeySequence, QTextCursor, QTextFormat
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMenu,
                               QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QStyle,
                               QStyledItemDelegate, QStyleOptionViewItem, QTableWidget,
                               QTableWidgetItem, QTabWidget, QTextEdit, QToolBar, QToolButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import __version__, batch, batfile, heuristics, library, preprocess
from ..formats import REGISTRY
from ..model import ERROR, GROUP, INFO, UNKNOWN, Node, compute_stats, deepest_at
from ..reader import float_digits
from ..render import LAYOUTS, RenderOptions, render_text, write_json, write_text
from ..rules import (ACTIONS, ENCODINGS, UNKNOWN_MODES, FieldSpec, Rule, RuleEngine, RuleSet, example_ruleset,
                     parse_tag)
from .bat_dialog import BatDialog
from .batch_dialog import BatchDialog
from .hexview import HexView, Inspector, mono_font
from .header_dialog import HeaderDialog
from .rule_dialog import RuleDialog
from .theme import DEFAULT as DEFAULT_THEME
from .theme import TITLES as THEME_TITLES
from .theme import apply_theme

RULES_MODE = "__rules__"
MAX_TEXT_LINES = 200_000  # default of the screen line limit (toolbar)


def build_marks(root: Node, n: int) -> bytearray:
    """Per byte: 0 explained, 1 unknown, 2 not covered by any node."""
    marks = bytearray(b"\x02") * n
    unknown = []
    for node in root.walk():
        if node is root or node.kind in (ERROR, INFO) or node.length <= 0:
            continue
        s, e = node.offset, min(node.end, n)
        if node.kind == UNKNOWN:
            unknown.append((s, e))
        elif e > s:
            marks[s:e] = bytes(e - s)
    for s, e in unknown:
        if e > s:
            marks[s:e] = b"\x01" * (e - s)
    return marks


class DecodeJob(QThread):
    done = Signal(int, object)

    def __init__(self, gen: int, data: bytes, mode: str, ruleset: RuleSet, opts: dict, ropts: RenderOptions,
                 max_lines: int = MAX_TEXT_LINES):
        super().__init__()
        self.setStackSize(64 << 20)  # deep recursive formats
        self.gen, self.data, self.mode, self.ruleset, self.opts, self.ropts = gen, data, mode, ruleset, opts, ropts
        self.max_lines = max_lines

    def run(self) -> None:
        t0 = time.perf_counter()
        try:
            with float_digits(self.ropts.float_digits):
                if self.mode == RULES_MODE:
                    root = RuleEngine(self.ruleset).decode(self.data)
                else:
                    root = REGISTRY[self.mode].decode(self.data, self.opts)
            lines: list[Node] = []
            ro = copy.copy(self.ropts)
            ro.max_lines = self.max_lines
            text = render_text(root, self.data, ro, lines)
            res = dict(root=root, text=text, lines=lines, stats=compute_stats(root, len(self.data)),
                       marks=build_marks(root, len(self.data)), elapsed=time.perf_counter() - t0)
        except Exception:
            res = dict(error=traceback.format_exc())
        self.done.emit(self.gen, res)


class AnalyzeJob(QThread):
    done = Signal(object)

    def __init__(self, data: bytes):
        super().__init__()
        self.setStackSize(64 << 20)
        self.data = data

    def run(self) -> None:
        try:
            self.done.emit(heuristics.analyze(self.data))
        except Exception:
            self.done.emit(traceback.format_exc())


class SaveJob(QThread):
    """Full decode + streamed write off the GUI thread (big stack, no giant string in memory)."""
    done = Signal(bool, str)

    def __init__(self, path: str, data: bytes, mode: str, ruleset: RuleSet, opts: dict,
                 ropts: RenderOptions, as_json: bool):
        super().__init__()
        self.setStackSize(64 << 20)
        self.path, self.data, self.mode, self.ruleset, self.opts, self.ropts = path, data, mode, ruleset, opts, ropts
        self.as_json = as_json

    def run(self) -> None:
        try:
            with float_digits(self.ropts.float_digits):
                root = (RuleEngine(self.ruleset).decode(self.data) if self.mode == RULES_MODE
                        else REGISTRY[self.mode].decode(self.data, self.opts))
            if self.as_json:
                write_json(self.path, root, self.ropts)
                msg = f"저장: {self.path}"
            else:
                msg = f"저장: {self.path} ({write_text(self.path, root, self.data, self.ropts):,}줄)"
            self.done.emit(True, msg)
        except MemoryError:
            self.done.emit(False, "메모리가 부족해 저장하지 못했습니다. 그룹 규칙(닫는 태그)이 맞는지 확인하세요.")
        except Exception:
            self.done.emit(False, traceback.format_exc())


class CenteredCheckDelegate(QStyledItemDelegate):
    """Draws and toggles an item's check box in the middle of the cell (Qt puts it at the left)."""

    def _check_rect(self, option, index) -> QRect:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = opt.widget.style() if opt.widget else QApplication.style()
        r = style.subElementRect(QStyle.SE_ItemViewItemCheckIndicator, opt, opt.widget)
        r.moveCenter(option.rect.center())
        return r

    def paint(self, painter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = opt.widget.style() if opt.widget else QApplication.style()
        checked = opt.checkState == Qt.Checked
        opt.features &= ~QStyleOptionViewItem.HasCheckIndicator
        opt.text = ""
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)  # background and selection
        box = QStyleOptionViewItem(opt)
        box.rect = self._check_rect(option, index)
        box.state = (opt.state & ~QStyle.State_HasFocus) | (QStyle.State_On if checked else QStyle.State_Off)
        style.drawPrimitive(QStyle.PE_IndicatorItemViewItemCheck, box, painter, opt.widget)

    def editorEvent(self, event, model, option, index) -> bool:
        if not (index.flags() & Qt.ItemIsUserCheckable and index.flags() & Qt.ItemIsEnabled):
            return False
        t = event.type()
        if t in (QEvent.MouseButtonRelease, QEvent.MouseButtonDblClick):
            if event.button() != Qt.LeftButton or not self._check_rect(option, index).contains(event.position().toPoint()):
                return False
            if t == QEvent.MouseButtonDblClick:
                return True  # swallow, like the default delegate
        elif t == QEvent.KeyPress:
            if event.key() not in (Qt.Key_Space, Qt.Key_Select):
                return False
        else:
            return False
        checked = Qt.CheckState(index.data(Qt.CheckStateRole) or 0) == Qt.Checked  # data() may give a plain int
        state = Qt.Unchecked if checked else Qt.Checked
        return model.setData(index, state, Qt.CheckStateRole)


def _ro_item(text, data=None) -> QTableWidgetItem:
    it = QTableWidgetItem(str(text))
    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    if data is not None:
        it.setData(Qt.UserRole, data)
    return it


class MainWindow(QMainWindow):
    def __init__(self, path: str | None = None):
        super().__init__()
        ini = os.environ.get("BINARYTOTEXT_SETTINGS")  # tests use a throwaway INI file
        self.settings = QSettings(ini, QSettings.IniFormat) if ini else QSettings("BinaryToText", "BinaryToText")
        self.raw = b""
        self.data = b""
        self.path: Path | None = None
        self.rules_path: Path | None = None
        self.ruleset = self._restore_ruleset()
        self._saved_json = self._saved_snapshot()  # JSON at last load/save, for the unsaved check
        self.format_opts: dict[str, dict] = {}
        self.gen = 0
        self.jobs: list[QThread] = []
        self.result: dict | None = None
        self.node_line: dict[int, int] = {}
        self._syncing = False
        self.report = None
        self.batch_dlg: BatchDialog | None = None
        self.save_job: SaveJob | None = None
        self.theme = apply_theme(QApplication.instance(), self.settings.value("theme", DEFAULT_THEME))

        self.setWindowTitle(f"BinaryToText {__version__}")
        self.setAcceptDrops(True)
        self.decode_timer = QTimer(self, singleShot=True, interval=200, timeout=self.decode)

        self._build_toolbar()
        self._build_body()
        self._refresh_rule_table()
        self._on_mode_changed()
        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        else:
            self.resize(1600, 900)
        if path:
            self.open_file(path)
        else:
            self.statusBar().showMessage("파일을 열거나 창으로 끌어다 놓으세요 (Ctrl+O)")

    # ------------------------------------------------------------------ UI
    def _build_toolbar(self) -> None:
        tb = QToolBar("도구")
        tb.setMovable(False)
        self.addToolBar(tb)

        def act(text, slot, key=None, tip=""):
            a = QAction(text, self)
            a.triggered.connect(lambda *_: slot())  # don't pass `checked` into optional parameters
            if key:
                a.setShortcut(QKeySequence(key))
            a.setToolTip(tip or text)
            tb.addAction(a)
            return a

        act("열기", self.open_dialog, "Ctrl+O", "바이너리 파일 열기 (Ctrl+O)")
        act("텍스트 저장", self.save_text, "Ctrl+S", "해석 결과를 텍스트 파일로 저장 (Ctrl+S)")
        act("JSON 저장", self.save_json, None, "해석 트리를 JSON으로 저장")
        act("여러 파일 변환", self.batch_convert, "Ctrl+B",
            "여러 파일·폴더를 현재 설정(또는 규칙 폴더 자동 선택)으로 한 번에 변환 (Ctrl+B)")
        act("bat 만들기", self.make_bat, None,
            "현재 설정으로 여러 파일을 변환하는 bat 파일(btt.exe batch …)을 만듭니다")
        tb.addSeparator()
        tb.addWidget(QLabel(" 해석 방식 "))
        self.mode = QComboBox()
        self.mode.addItem("사용자 규칙 (직접 정의)", RULES_MODE)
        for dec in sorted(REGISTRY.values(), key=lambda d: (d.family != "TLV", d.family, d.title)):
            self.mode.addItem(f"[{dec.family}] {dec.title}", dec.key)
            self.mode.setItemData(self.mode.count() - 1, dec.description, Qt.ToolTipRole)
        last = self.settings.value("mode", RULES_MODE)
        i = self.mode.findData(last)
        self.mode.setCurrentIndex(max(0, i))
        self.mode.currentIndexChanged.connect(self._on_mode_changed)
        tb.addWidget(self.mode)
        tb.addWidget(QLabel("  전처리 "))
        self.pre = QComboBox()
        for k, v in preprocess.TRANSFORMS.items():
            self.pre.addItem(v, k)
        self.pre.currentIndexChanged.connect(self._apply_preprocess)
        tb.addWidget(self.pre)
        self.pre_arg = QLineEdit()
        self.pre_arg.setPlaceholderText("N / XOR 키")
        self.pre_arg.setMinimumWidth(80)
        self.pre_arg.setMaximumWidth(110)
        self.pre_arg.editingFinished.connect(self._apply_preprocess)
        tb.addWidget(self.pre_arg)
        tb.addSeparator()
        act("다시 해석", self.decode, "F5")
        act("자동 분석", self.run_analysis, "F6", "모르는 파일 분석: 포맷 추정, 태그 후보, 문자열 (F6)")

    def _build_output_options(self) -> QWidget:
        """Two rows above the result tabs: how the result is written (screen, saved files, batch)."""
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(2, 0, 2, 4)
        lay.setSpacing(4)
        row1, row2 = QHBoxLayout(), QHBoxLayout()
        lay.addLayout(row1)
        lay.addLayout(row2)
        self.chk_offsets = QCheckBox("오프셋")
        self.chk_offsets.setChecked(self.settings.value("offsets", True, type=bool))
        self.chk_raw = QCheckBox("원본 hex")
        self.chk_raw.setChecked(self.settings.value("raw_hex", False, type=bool))
        self.chk_labels = QCheckBox("이름")
        self.chk_labels.setToolTip("끄면 규칙 이름(라벨) 없이 해석된 값만 출력합니다.\n"
                                   "파일에서 읽은 키(key = 값)는 그대로 남습니다.")
        self.chk_labels.setChecked(self.settings.value("labels", True, type=bool))
        self.chk_commas = QCheckBox("쉼표")
        self.chk_commas.setToolTip("그룹 안의 항목 뒤에 쉼표를 붙입니다 (마지막 항목 제외).\n"
                                   "한 줄로 이어 쓰는 그룹은 항상 쉼표로 구분합니다.")
        self.chk_commas.setChecked(self.settings.value("commas", False, type=bool))
        self.chk_quotes = QCheckBox("따옴표")
        self.chk_quotes.setToolTip("문자열을 \"…\" 로 감싸 표시합니다 (key = \"값\" 포함).\n"
                                   "끄면 따옴표 없이: 1, 2, abc, \"\", cde (빈 문자열은 \"\").\n"
                                   "단 공백이나 , = : ; [ ] { } ( ) < > # 따옴표 \\ 가 든 문자열은 \"…\" 로 남깁니다.")
        self.chk_quotes.setChecked(self.settings.value("quotes", True, type=bool))
        for c in (self.chk_offsets, self.chk_raw, self.chk_labels, self.chk_quotes, self.chk_commas):
            c.toggled.connect(self.schedule)
            row1.addWidget(c)
        row1.addSpacing(10)
        row1.addWidget(QLabel("그룹"))
        self.layout_combo = QComboBox()
        for k, v in LAYOUTS.items():
            self.layout_combo.addItem(v, k)
        self.layout_combo.setToolTip("그룹 안 항목을 줄마다 쓸지, 한 줄로 이어 쓸지 (쉼표로 구분).\n"
                                     "'배열만 한 줄' = 값만 든 [ ] 배열만 [21, 22, 24] 처럼.\n"
                                     "규칙 편집 창에서 그룹 규칙마다 따로 정할 수 있습니다.")
        self.layout_combo.setCurrentIndex(max(0, self.layout_combo.findData(self.settings.value("layout", "lines"))))
        self.layout_combo.currentIndexChanged.connect(self.schedule)
        row1.addWidget(self.layout_combo)
        row1.addStretch(1)
        row2.addWidget(QLabel("실수 유효 숫자"))
        self.digits_spin = QSpinBox()
        self.digits_spin.setRange(0, 17)
        self.digits_spin.setSpecialValueText("자동")
        self.digits_spin.setToolTip("float/double 값을 몇 자리 유효 숫자로 쓸지. 저장 파일·여러 파일 변환에도 적용됩니다.\n"
                                     "자동 = 값을 정확히 나타내는 가장 짧은 표기 (float32 36.6 → 36.6).\n"
                                     "예: 3자리 → 3.14, 123e3 / float32 의 실제 값을 보려면 9, double 은 17.\n"
                                     "지수는 3의 배수로 씁니다: 12.3e-6, 1.5e3.")
        self.digits_spin.setValue(self.settings.value("float_digits", 0, type=int))
        self.digits_spin.setKeyboardTracking(False)
        self.digits_spin.valueChanged.connect(self.schedule)
        row2.addWidget(self.digits_spin)
        row2.addSpacing(4)
        row2.addWidget(QLabel("화면 줄 수"))
        self.lines_spin = QSpinBox()
        self.lines_spin.setRange(1_000, 10_000_000)
        self.lines_spin.setSingleStep(50_000)
        self.lines_spin.setGroupSeparatorShown(True)
        self.lines_spin.setToolTip("텍스트 탭에 보여 줄 최대 줄 수. 넘으면 잘라서 보여 줍니다.\n"
                                  "저장 파일에는 항상 전체가 들어갑니다. 크게 하면 화면 표시에 메모리를 더 씁니다.")
        self.lines_spin.setValue(self.settings.value("max_lines", MAX_TEXT_LINES, type=int))
        self.lines_spin.setKeyboardTracking(False)  # re-decode when typing is finished, not per digit
        self.lines_spin.valueChanged.connect(self.schedule)
        row2.addWidget(self.lines_spin)
        row2.addSpacing(4)
        self.chk_header_footer = QCheckBox("헤더/꼬리")
        self.chk_header_footer.setToolTip("규칙 세트의 헤더·꼬리(위치로 읽는 파일 앞뒤 부분)를 출력에 넣을지.\n"
                                          "화면, 텍스트·JSON 저장, 여러 파일 변환에 똑같이 적용됩니다.")
        self.chk_header_footer.setChecked(self.settings.value("header_footer", True, type=bool))
        self.chk_header_footer.toggled.connect(self.schedule)
        row2.addWidget(self.chk_header_footer)
        row2.addStretch(1)
        return box

    def _build_body(self) -> None:
        # Left: hex + inspector
        self.hex = HexView()
        self.hex.cursor_moved.connect(self._on_hex_cursor)
        self.hex.add_rule_requested.connect(self._add_rule_from_bytes)
        self.inspector = Inspector()
        left = QSplitter(Qt.Vertical)
        left.addWidget(self.hex)
        left.addWidget(self.inspector)
        left.setSizes([600, 300])

        # Middle: tabs
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_rules_tab(), "규칙")
        self.opt_tab = QWidget()
        self.opt_layout = QVBoxLayout(self.opt_tab)
        self.tabs.addTab(self.opt_tab, "포맷 옵션")
        self.tabs.addTab(self._build_analysis_tab(), "자동 분석")

        # Right: output
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.text.setFont(mono_font())
        self.text.setMaximumBlockCount(0)
        self.text.cursorPositionChanged.connect(self._on_text_cursor)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["항목", "값", "오프셋", "길이"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Interactive)
        self.tree.setColumnWidth(0, 260)
        self.tree.setColumnWidth(1, 300)
        self.tree.itemExpanded.connect(self._on_tree_expand)
        self.tree.currentItemChanged.connect(self._on_tree_current)
        out = QTabWidget()
        out.addTab(self.text, "텍스트")
        out.addTab(self.tree, "트리")
        self.out_panel = QWidget()
        rl = QVBoxLayout(self.out_panel)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        rl.addWidget(self._build_output_options())
        rl.addWidget(out, 1)

        main = QSplitter(Qt.Horizontal)
        main.addWidget(left)
        main.addWidget(self.tabs)
        main.addWidget(self.out_panel)
        hw = self.hex.preferred_width()
        main.setSizes([hw, 460, max(480, 1600 - hw - 460)])
        for i, stretch in enumerate((0, 0, 1)):  # window resizes go to the result panel; hex keeps its width
            main.setStretchFactor(i, stretch)
        self.setCentralWidget(main)
        self.stats_label = QLabel()
        self.statusBar().addPermanentWidget(self.stats_label)
        menu = QMenu(self)
        group = QActionGroup(self)
        self.theme_actions: dict[str, QAction] = {}
        for key, title in THEME_TITLES.items():
            a = menu.addAction(title)
            a.setCheckable(True)
            a.setChecked(key == self.theme)
            a.triggered.connect(lambda *_, k=key: self.set_theme(k))
            group.addAction(a)
            self.theme_actions[key] = a
        self.theme_btn = QToolButton()
        self.theme_btn.setText("테마")
        self.theme_btn.setToolTip("화면 색 테마 바꾸기")
        self.theme_btn.setMenu(menu)
        self.theme_btn.setPopupMode(QToolButton.InstantPopup)
        self.statusBar().addPermanentWidget(self.theme_btn)

    def _build_rules_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        form = QFormLayout()
        self.rs_name = QLineEdit()
        self.rs_endian = QComboBox()
        self.rs_endian.addItem("리틀 엔디언", "little")
        self.rs_endian.addItem("빅 엔디언", "big")
        self.rs_enc = QComboBox()
        self.rs_enc.setEditable(True)
        self.rs_enc.addItems(ENCODINGS)
        self.rs_unknown = QComboBox()
        for k, v in UNKNOWN_MODES.items():
            self.rs_unknown.addItem(v, k)
        form.addRow("규칙 세트 이름", self.rs_name)
        form.addRow("기본 엔디언", self.rs_endian)
        form.addRow("기본 문자 인코딩", self.rs_enc)
        self.rs_depth = QSpinBox()
        self.rs_depth.setRange(1, 10000)
        self.rs_depth.setToolTip("그룹이 이 단계보다 깊게 열리면 해석을 멈춥니다.\n"
                                 "닫는 태그가 틀리면 그룹이 끝없이 겹쳐 열려 메모리를 크게 쓰는 것을 막습니다.")
        form.addRow("규칙에 없는 byte", self.rs_unknown)
        deprow = QHBoxLayout()
        deprow.addWidget(self.rs_depth)
        deprow.addWidget(QLabel("단계까지 (넘으면 해석 중단)"))
        deprow.addStretch(1)
        form.addRow("최대 그룹 깊이", deprow)
        idrow = QHBoxLayout()
        self.rs_match_off = QSpinBox()
        self.rs_match_off.setRange(0, 1 << 30)
        self.rs_match_off.setPrefix("위치 ")
        self.rs_match = QLineEdit()
        self.rs_match.setFont(mono_font())
        self.rs_match.setPlaceholderText("예: FE CA   (비우면 자동 선택 안 함)")
        take = QPushButton("현재 파일 값")
        take.setToolTip("현재 파일의 그 위치 4 byte를 식별값으로 넣습니다")
        take.clicked.connect(self._take_match_from_file)
        idrow.addWidget(self.rs_match_off)
        idrow.addWidget(self.rs_match, 1)
        idrow.addWidget(take)
        form.addRow("식별 조건", idrow)
        librow = QHBoxLayout()
        self.lib_label = QLabel()
        self.lib_auto = QCheckBox("열 때 자동 선택")
        self.lib_auto.setChecked(self.settings.value("library_auto", True, type=bool))
        self.lib_auto.toggled.connect(lambda v: self.settings.setValue("library_auto", v))
        lb = QPushButton("폴더…")
        lb.clicked.connect(self.choose_library)
        librow.addWidget(self.lib_label, 1)
        librow.addWidget(self.lib_auto)
        librow.addWidget(lb)
        form.addRow("규칙 폴더", librow)
        lay.addLayout(form)
        self._update_library_label()
        self.rs_match_off.valueChanged.connect(self._rs_settings_changed)
        self.rs_match.editingFinished.connect(self._rs_settings_changed)
        self.rs_name.editingFinished.connect(self._rs_settings_changed)
        self.rs_endian.currentIndexChanged.connect(self._rs_settings_changed)
        self.rs_enc.currentTextChanged.connect(self._rs_settings_changed)
        self.rs_unknown.currentIndexChanged.connect(self._rs_settings_changed)
        self.rs_depth.valueChanged.connect(self._rs_settings_changed)

        hrow = QHBoxLayout()
        self.hf_label = QLabel()
        self.hf_label.setWordWrap(True)
        hrow.addWidget(self.hf_label, 1)
        hb = QPushButton("헤더 / 꼬리…")
        hb.setToolTip("태그와 상관없이 파일 맨 앞·맨 끝의 위치로 정해지는 구조")
        hb.clicked.connect(self.edit_header)
        hrow.addWidget(hb)
        lay.addLayout(hrow)

        self.rule_table = QTableWidget(0, 5)
        self.rule_table.setHorizontalHeaderLabels(["사용", "태그", "이름", "동작", "필드 / 메모"])
        self.rule_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.rule_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.rule_table.verticalHeader().hide()
        self.rule_table.setItemDelegateForColumn(0, CenteredCheckDelegate(self.rule_table))
        hh = self.rule_table.horizontalHeader()
        hh.setStretchLastSection(True)
        for c, wd in ((0, 40), (1, 80), (2, 110), (3, 110)):
            self.rule_table.setColumnWidth(c, wd)
        self.rule_table.itemChanged.connect(self._on_rule_item_changed)
        self.rule_table.itemDoubleClicked.connect(lambda *_: self.edit_rule())
        lay.addWidget(self.rule_table, 1)

        row1 = QHBoxLayout()
        for text, fn in (("추가", self.add_rule), ("편집", self.edit_rule), ("복제", self.dup_rule),
                         ("삭제", self.del_rule), ("▲", lambda: self.move_rule(-1)), ("▼", lambda: self.move_rule(1))):
            b = QPushButton(text)
            b.clicked.connect(lambda *_, f=fn: f())  # add_rule(tag) must not receive `checked`
            row1.addWidget(b)
        lay.addLayout(row1)
        row2 = QHBoxLayout()
        for text, fn in (("불러오기…", self.load_rules), ("저장", self.save_rules),
                         ("다른 이름으로…", lambda: self.save_rules(True)), ("예시 규칙", self.example_rules),
                         ("새 규칙 세트", self.new_rules)):
            b = QPushButton(text)
            b.clicked.connect(lambda *_, f=fn: f())  # save_rules(save_as) must not receive `checked`
            row2.addWidget(b)
        lay.addLayout(row2)
        hint = QLabel("팁: 왼쪽 hex 에서 바이트를 선택하고 우클릭 → 태그로 규칙 추가. "
                      "규칙을 바꾸면 결과가 바로 다시 해석됩니다. 주황색 = 아직 규칙이 없는 byte.")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        return w

    def _build_analysis_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        top = QHBoxLayout()
        b = QPushButton("분석 실행 (F6)")
        b.clicked.connect(self.run_analysis)
        top.addWidget(b)
        self.an_status = QLabel("파일을 연 뒤 실행하세요.")
        top.addWidget(self.an_status, 1)
        lay.addLayout(top)

        split = QSplitter(Qt.Vertical)
        self.an_summary = QPlainTextEdit()
        self.an_summary.setReadOnly(True)
        self.an_summary.setFont(mono_font())
        split.addWidget(self.an_summary)

        g1 = QGroupBox("내장 포맷 적합도 (높을수록 파일을 많이 설명함)")
        l1 = QVBoxLayout(g1)
        self.an_formats = QTableWidget(0, 4)
        self.an_formats.setHorizontalHeaderLabels(["점수", "포맷", "옵션", "첫 오류"])
        self.an_formats.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.an_formats.verticalHeader().hide()
        self.an_formats.horizontalHeader().setStretchLastSection(True)
        self.an_formats.itemDoubleClicked.connect(lambda *_: self._apply_format_suggestion())
        l1.addWidget(self.an_formats)
        bf = QPushButton("선택한 포맷으로 해석")
        bf.clicked.connect(self._apply_format_suggestion)
        l1.addWidget(bf)
        split.addWidget(g1)

        g2 = QGroupBox("태그 후보 → 규칙 제안 (문자열 앞에 반복해서 나오는 byte)")
        l2 = QVBoxLayout(g2)
        self.an_sugg = QTableWidget(0, 4)
        self.an_sugg.setHorizontalHeaderLabels(["태그", "종류", "횟수", "예"])
        self.an_sugg.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.an_sugg.verticalHeader().hide()
        self.an_sugg.horizontalHeader().setStretchLastSection(True)
        l2.addWidget(self.an_sugg)
        bs = QPushButton("선택한 제안을 규칙에 추가")
        bs.clicked.connect(self._apply_rule_suggestions)
        l2.addWidget(bs)
        split.addWidget(g2)

        g3 = QGroupBox("문자열 (더블클릭 → hex 위치로 이동)")
        l3 = QVBoxLayout(g3)
        self.an_strings = QTableWidget(0, 4)
        self.an_strings.setHorizontalHeaderLabels(["오프셋", "인코딩", "앞 byte", "텍스트"])
        self.an_strings.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.an_strings.verticalHeader().hide()
        self.an_strings.horizontalHeader().setStretchLastSection(True)
        self.an_strings.setFont(mono_font())
        self.an_strings.itemDoubleClicked.connect(self._jump_string)
        l3.addWidget(self.an_strings)
        split.addWidget(g3)
        split.setSizes([150, 200, 200, 250])
        lay.addWidget(split, 1)
        return w

    # ------------------------------------------------------------ files
    def open_dialog(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "바이너리 파일 열기", self.settings.value("last_dir", ""))
        if p:
            self.open_file(p)

    def open_file(self, p: str) -> None:
        try:
            raw = Path(p).read_bytes()
        except OSError as e:
            QMessageBox.warning(self, "열기 실패", str(e))
            return
        self.path = Path(p)
        self.settings.setValue("last_dir", str(self.path.parent))
        self.raw = raw
        self.pre.blockSignals(True)
        self.pre.setCurrentIndex(0)
        self.pre.blockSignals(False)
        self.report = None
        self.an_status.setText("F6 또는 '분석 실행'으로 이 파일을 분석할 수 있습니다.")
        msg = self._auto_select_rules(raw)
        self._set_data(raw)
        if msg:
            self.statusBar().showMessage(msg)
        self.setWindowTitle(f"{self.path.name} — BinaryToText {__version__}")

    def _set_data(self, data: bytes) -> None:
        self.data = data
        self.hex.set_data(data)
        if data:
            self.inspector.show_at(data, 0)
        self.decode()

    def _apply_preprocess(self) -> None:
        kind = self.pre.currentData()
        try:
            data = preprocess.apply(self.raw, kind, self.pre_arg.text().strip())
        except Exception as e:
            QMessageBox.warning(self, "전처리 실패", f"{preprocess.TRANSFORMS[kind]}: {e}")
            self.pre.blockSignals(True)
            self.pre.setCurrentIndex(0)
            self.pre.blockSignals(False)
            data = self.raw
        self._set_data(data)
        if kind != "none":
            self.statusBar().showMessage(f"전처리 '{preprocess.TRANSFORMS[kind]}' 적용: "
                                         f"{len(self.raw):,} → {len(data):,} byte (오프셋은 변환 후 기준)")

    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile()]
        if len(paths) > 1 or (paths and Path(paths[0]).is_dir()):
            self.batch_convert(paths)
        elif paths:
            p = paths[0]
            if p.lower().endswith(".json") and self._looks_like_rules(p):
                self._load_rules_path(p)
            else:
                self.open_file(p)

    @staticmethod
    def _looks_like_rules(p: str) -> bool:
        try:
            import json
            d = json.loads(Path(p).read_text(encoding="utf-8"))
            return isinstance(d, dict) and "rules" in d
        except Exception:
            return False

    def save_text(self) -> None:
        self._save("텍스트로 저장", ".txt", "텍스트 (*.txt);;모든 파일 (*)", as_json=False)

    def save_json(self) -> None:
        self._save("JSON으로 저장", ".json", "JSON (*.json)", as_json=True)

    def _save(self, title: str, suffix: str, filters: str, as_json: bool) -> SaveJob | None:
        if not self.data and not self.path:
            QMessageBox.information(self, "저장", "먼저 파일을 여세요.")
            return None
        if self.save_job is not None:
            QMessageBox.information(self, "저장", "이전 저장이 아직 진행 중입니다.")
            return None
        default = str(self.path.with_suffix(self.path.suffix + suffix)) if self.path else ""
        p, _ = QFileDialog.getSaveFileName(self, title, default, filters)
        if not p:
            return None
        mode = self.mode.currentData()
        job = SaveJob(p, self.data, mode, copy.deepcopy(self.ruleset), dict(self._current_opts()),
                      self._render_opts(), as_json)
        job.done.connect(self._on_saved)
        job.finished.connect(lambda j=job: self.jobs.remove(j) if j in self.jobs else None)
        self.jobs.append(job)
        self.save_job = job
        self.statusBar().showMessage(f"저장 중… {p}")
        job.start()
        return job

    def _on_saved(self, ok: bool, msg: str) -> None:
        self.save_job = None
        if ok:
            self.statusBar().showMessage(msg)
        else:
            self.statusBar().showMessage("저장 실패")
            QMessageBox.warning(self, "저장 실패", msg)

    def batch_settings(self) -> tuple[batch.Settings, str]:
        """Snapshot of the current method/options for batch conversion, plus a description."""
        mode = self.mode.currentData()
        kind = self.pre.currentData()
        st = batch.Settings(
            ruleset=copy.deepcopy(self.ruleset) if mode == RULES_MODE else None,
            fmt="" if mode == RULES_MODE else mode, fmt_opts=dict(self._current_opts()),
            library_dir=self._library_dir(), pre=kind, pre_arg=self.pre_arg.text().strip(),
            render=self._render_opts())
        desc = f"사용자 규칙 '{self.ruleset.name}'" if mode == RULES_MODE else f"내장 포맷 {REGISTRY[mode].title}"
        if kind != "none":
            desc += f", 전처리 {preprocess.TRANSFORMS[kind]}"
        return st, desc

    def batch_convert(self, paths: list[str] | None = None) -> None:
        """Open (or bring back) the batch dialog with the current settings."""
        st, desc = self.batch_settings()
        if self.batch_dlg is None:
            self.batch_dlg = BatchDialog(self, st, desc, self.settings)
        elif self.batch_dlg.job is None:
            self.batch_dlg.set_base(st, desc)
        if paths:
            self.batch_dlg.add_paths(paths)
        self.batch_dlg.show()
        self.batch_dlg.raise_()
        self.batch_dlg.activateWindow()

    def bat_spec(self) -> batfile.BatSpec | None:
        """Current method/options as a BatSpec. A .bat needs the rule set as a saved file: offers to save."""
        mode = self.mode.currentData()
        if mode == RULES_MODE and (self.rules_path is None or self._dirty()):
            ans = QMessageBox.question(
                self, "bat 파일 만들기",
                "bat 파일은 저장된 규칙 JSON 파일을 씁니다. 현재 규칙 세트를 지금 저장할까요?",
                QMessageBox.Save | QMessageBox.Cancel, QMessageBox.Save)
            if ans != QMessageBox.Save:
                return None
            self.save_rules()
            if self.rules_path is None or self._dirty():
                return None
        st, desc = self.batch_settings()
        return batfile.BatSpec(
            rules=str(self.rules_path) if mode == RULES_MODE else "", fmt=st.fmt, fmt_opts=st.fmt_opts,
            library=st.library_dir, pre=st.pre, pre_arg=st.pre_arg, render=st.render,
            description=desc + (f" ({self.rules_path.name})" if mode == RULES_MODE else ""))

    def make_bat(self) -> BatDialog | None:
        spec = self.bat_spec()
        if spec is None:
            return None
        start = str(self.path.parent) if self.path else ""
        dlg = BatDialog(self, spec, self.settings, start)
        dlg.show()
        return dlg

    # ------------------------------------------------------------ decode
    def _render_opts(self) -> RenderOptions:
        return RenderOptions(offsets=self.chk_offsets.isChecked(), raw_hex=self.chk_raw.isChecked(),
                             labels=self.chk_labels.isChecked(), layout=self.layout_combo.currentData(),
                             commas=self.chk_commas.isChecked(), quotes=self.chk_quotes.isChecked(),
                             float_digits=self.digits_spin.value(),
                             header_footer=self.chk_header_footer.isChecked())

    def _current_opts(self) -> dict:
        mode = self.mode.currentData()
        if mode == RULES_MODE:
            return {}
        return self.format_opts.setdefault(mode, REGISTRY[mode].defaults())

    def schedule(self, *_) -> None:
        self.decode_timer.start()

    def decode(self) -> None:
        self.decode_timer.stop()
        self.gen += 1
        mode = self.mode.currentData()
        job = DecodeJob(self.gen, self.data, mode, copy.deepcopy(self.ruleset), dict(self._current_opts()),
                        self._render_opts(), self.lines_spin.value())
        job.done.connect(self._on_decoded)
        job.finished.connect(lambda j=job: self.jobs.remove(j) if j in self.jobs else None)
        self.jobs.append(job)
        self.stats_label.setText("해석 중…")
        job.start()

    def _on_decoded(self, gen: int, res: dict) -> None:
        if gen != self.gen:
            return  # a newer request is pending
        if "error" in res:
            self.text.setPlainText("내부 오류가 발생했습니다:\n\n" + res["error"])
            self.stats_label.setText("오류")
            return
        self.result = res
        self.node_line = {}
        for i, n in enumerate(res["lines"]):
            self.node_line.setdefault(id(n), i)
        self._syncing = True
        self.text.setPlainText(res["text"])
        self._syncing = False
        self.hex.model_.set_marks(res["marks"])
        self._fill_tree(res["root"])
        st = res["stats"]
        self.stats_label.setText(
            f"해석 {st.coverage:.1%} · 미해석 {st.unknown:,} byte · 오류 {st.errors} · 항목 {st.nodes:,} · "
            f"{len(self.data):,} byte · {res['elapsed']:.2f}s")

    # ------------------------------------------------------------ tree
    def _fill_tree(self, root: Node) -> None:
        self.tree.clear()
        self._tree_nodes: dict[int, Node] = {}
        self._add_tree_children(self.tree.invisibleRootItem(), root)

    def _add_tree_children(self, parent_item, node: Node, cap: int = 5000) -> None:
        for i, c in enumerate(node.children):
            if i >= cap:
                more = QTreeWidgetItem([f"… 외 {len(node.children) - cap:,}개 (텍스트 탭에서 확인)"])
                parent_item.addChild(more)
                break
            label = c.label
            if c.kind == UNKNOWN:
                label = f"?? {c.label}"
            elif c.kind == ERROR:
                label = f"!! {c.label}"
            it = QTreeWidgetItem([label, c.value + (f"  ({c.note})" if c.note else ""),
                                  f"0x{c.offset:X}", str(c.length)])
            it.setData(0, Qt.UserRole, id(c))
            self._tree_nodes[id(c)] = c
            if c.kind == UNKNOWN:
                it.setForeground(0, QColor(220, 120, 0))
            elif c.kind == ERROR:
                it.setForeground(0, QColor(220, 40, 40))
            if c.children:
                it.addChild(QTreeWidgetItem(["…"]))  # placeholder until expanded
            parent_item.addChild(it)

    def _on_tree_expand(self, item: QTreeWidgetItem) -> None:
        if item.childCount() == 1 and item.child(0).data(0, Qt.UserRole) is None and item.child(0).text(0) == "…":
            item.takeChild(0)
            node = self._tree_nodes.get(item.data(0, Qt.UserRole))
            if node:
                self._add_tree_children(item, node)

    def _on_tree_current(self, item, _prev) -> None:
        if item is None:
            return
        node = self._tree_nodes.get(item.data(0, Qt.UserRole))
        if node:
            self.hex.highlight(node.offset, node.end)
            self.inspector.show_at(self.data, node.offset) if node.offset < len(self.data) else None

    # ------------------------------------------------------------ linking
    def _on_text_cursor(self) -> None:
        if self._syncing or not self.result:
            return
        line = self.text.textCursor().blockNumber()
        lines = self.result["lines"]
        if 0 <= line < len(lines):
            n = lines[line]
            if n.kind != GROUP or n.length:
                self._syncing = True
                self.hex.highlight(n.offset, n.end)
                self._syncing = False
            self._mark_text_line()

    def _mark_text_line(self) -> None:
        sel = QTextEdit.ExtraSelection()
        sel.format.setBackground(QColor(40, 120, 255, 50))
        sel.format.setProperty(QTextFormat.FullWidthSelection, True)
        sel.cursor = self.text.textCursor()
        sel.cursor.clearSelection()
        self.text.setExtraSelections([sel])

    def _on_hex_cursor(self, off: int) -> None:
        self.inspector.show_at(self.data, off)
        if self._syncing or not self.result:
            return
        n = deepest_at(self.result["root"], off)
        if n is None or n is self.result["root"]:
            return
        line = self.node_line.get(id(n))
        self.statusBar().showMessage(f"0x{off:X}: {n.label} {n.value}"[:200])
        if line is not None:
            self._syncing = True
            block = self.text.document().findBlockByNumber(line)
            cur = QTextCursor(block)
            self.text.setTextCursor(cur)
            self.text.centerCursor()
            self._mark_text_line()
            self._syncing = False
            self.hex.model_.set_highlight(n.offset, max(n.end, n.offset + 1))

    # ------------------------------------------------------------ mode / options
    def _on_mode_changed(self, *_) -> None:
        mode = self.mode.currentData()
        self.settings.setValue("mode", mode)
        self._build_option_form(mode)
        if mode != RULES_MODE and self.tabs.currentIndex() == 0:
            self.tabs.setCurrentIndex(1)
        elif mode == RULES_MODE and self.tabs.currentIndex() == 1:
            self.tabs.setCurrentIndex(0)
        self.schedule()

    def _build_option_form(self, mode: str) -> None:
        while self.opt_layout.count():
            item = self.opt_layout.takeAt(0)
            if item.widget():
                item.widget().hide()  # deleteLater may run later; never paint the old form meanwhile
                item.widget().deleteLater()
        if mode == RULES_MODE:
            lbl = QLabel("지금은 '사용자 규칙' 모드입니다. 내장 포맷을 쓰려면 위의 '해석 방식'에서 고르세요.")
            lbl.setWordWrap(True)
            self.opt_layout.addWidget(lbl)
            self.opt_layout.addStretch()
            return
        dec = REGISTRY[mode]
        opts = self._current_opts()
        title = QLabel(f"<b>{dec.title}</b><br>{dec.description}")
        title.setWordWrap(True)
        self.opt_layout.addWidget(title)
        box = QWidget()
        form = QFormLayout(box)
        for o in dec.options:
            if o.kind == "choice":
                w = QComboBox()
                for val, label in o.choices:
                    w.addItem(label, val)
                idx = next((i for i, (v, _) in enumerate(o.choices) if v == opts.get(o.key)), 0)
                w.setCurrentIndex(idx)
                w.currentIndexChanged.connect(lambda _i, k=o.key, w=w: self._set_opt(k, w.currentData()))
            elif o.kind == "int":
                w = QSpinBox()
                w.setRange(o.minimum, o.maximum)
                w.setValue(int(opts.get(o.key, o.default)))
                w.valueChanged.connect(lambda v, k=o.key: self._set_opt(k, v))
            else:
                w = QCheckBox()
                w.setChecked(bool(opts.get(o.key, o.default)))
                w.toggled.connect(lambda v, k=o.key: self._set_opt(k, v))
            form.addRow(o.label, w)
        self.opt_layout.addWidget(box)
        reset = QPushButton("기본값으로")
        reset.clicked.connect(lambda: self._reset_opts(mode))
        self.opt_layout.addWidget(reset)
        self.opt_layout.addStretch()

    def _set_opt(self, key, value) -> None:
        self._current_opts()[key] = value
        self.schedule()

    def _reset_opts(self, mode: str) -> None:
        self.format_opts[mode] = REGISTRY[mode].defaults()
        self._build_option_form(mode)
        self.schedule()

    # ------------------------------------------------------------ rules
    def _restore_ruleset(self) -> RuleSet:
        js = self.settings.value("ruleset_json")
        if js:
            try:
                import json
                return RuleSet.from_dict(json.loads(js))
            except Exception:
                pass
        return example_ruleset()

    def _rules_changed(self) -> None:
        self.settings.setValue("ruleset_json", self.ruleset.to_json())
        self._refresh_rule_table()
        if self.mode.currentData() != RULES_MODE:
            self.mode.setCurrentIndex(0)  # editing rules implies rule mode
        self.schedule()

    def _refresh_rule_table(self) -> None:
        rs = self.ruleset
        for w, v in ((self.rs_name, rs.name),):
            w.blockSignals(True)
            w.setText(v)
            w.blockSignals(False)
        for c, v in ((self.rs_endian, rs.endian), (self.rs_unknown, rs.unknown)):
            c.blockSignals(True)
            c.setCurrentIndex(max(0, c.findData(v)))
            c.blockSignals(False)
        self.rs_enc.blockSignals(True)
        self.rs_enc.setEditText(rs.encoding)
        self.rs_enc.blockSignals(False)
        for spin, v in ((self.rs_match_off, rs.match_offset), (self.rs_depth, rs.max_depth)):
            spin.blockSignals(True)
            spin.setValue(v)
            spin.blockSignals(False)
        self.rs_match.setText(rs.match_pattern)

        def fsum(fs):
            return ", ".join(f.summary() for f in fs) if fs else "없음"
        self.hf_label.setText(f"헤더: {fsum(rs.header)}   /   꼬리: {fsum(rs.footer)}")

        t = self.rule_table
        row = t.currentRow()
        t.blockSignals(True)
        t.setRowCount(len(rs.rules))
        for i, r in enumerate(rs.rules):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            chk.setCheckState(Qt.Checked if r.enabled else Qt.Unchecked)
            t.setItem(i, 0, chk)
            tag = _ro_item(r.tag)
            tag.setFont(mono_font())
            t.setItem(i, 1, tag)
            t.setItem(i, 2, _ro_item(r.name))
            t.setItem(i, 3, _ro_item(ACTIONS.get(r.action, r.action)))
            desc = r.fields_summary()
            if r.comment:
                desc = f"{desc}  — {r.comment}" if desc else r.comment
            t.setItem(i, 4, _ro_item(desc))
        t.blockSignals(False)
        if 0 <= row < t.rowCount():
            t.selectRow(row)

    def _rs_settings_changed(self, *_) -> None:
        rs = self.ruleset
        rs.name = self.rs_name.text().strip() or rs.name
        rs.endian = self.rs_endian.currentData()
        rs.encoding = self.rs_enc.currentText().strip() or "ascii"
        rs.unknown = self.rs_unknown.currentData()
        rs.match_offset = self.rs_match_off.value()
        rs.max_depth = self.rs_depth.value()
        rs.match_pattern = self.rs_match.text().strip()
        if rs.match_pattern:
            try:
                parse_tag(rs.match_pattern)
            except ValueError as e:
                self.statusBar().showMessage(f"식별 조건 오류: {e}")
        self.settings.setValue("ruleset_json", rs.to_json())
        if self.mode.currentData() == RULES_MODE:
            self.schedule()

    def _on_rule_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 0 and 0 <= item.row() < len(self.ruleset.rules):
            self.ruleset.rules[item.row()].enabled = item.checkState() == Qt.Checked
            self._rules_changed()

    def _edit_dialog(self, rule: Rule) -> Rule | None:
        dlg = RuleDialog(self, rule, self.ruleset, self.data)
        return dlg.rule if dlg.exec() else None

    def add_rule(self, tag: str = "") -> None:
        r = self._edit_dialog(Rule(tag, "", "value", [FieldSpec("uint", size=1)]))
        if r:
            self.ruleset.rules.append(r)
            self._rules_changed()
            self.rule_table.selectRow(len(self.ruleset.rules) - 1)

    def _add_rule_from_bytes(self, b: bytes) -> None:
        self.tabs.setCurrentIndex(0)
        self.add_rule(b.hex(" ").upper())

    def edit_rule(self) -> None:
        i = self.rule_table.currentRow()
        if 0 <= i < len(self.ruleset.rules):
            r = self._edit_dialog(self.ruleset.rules[i])
            if r:
                self.ruleset.rules[i] = r
                self._rules_changed()

    def edit_header(self) -> None:
        dlg = HeaderDialog(self, self.ruleset, self.data)
        if dlg.exec():
            self.ruleset.header, self.ruleset.footer = dlg.header, dlg.footer
            self._rules_changed()

    def dup_rule(self) -> None:
        i = self.rule_table.currentRow()
        if 0 <= i < len(self.ruleset.rules):
            self.ruleset.rules.insert(i + 1, copy.deepcopy(self.ruleset.rules[i]))
            self._rules_changed()
            self.rule_table.selectRow(i + 1)

    def del_rule(self) -> None:
        rows = sorted({ix.row() for ix in self.rule_table.selectedIndexes()}, reverse=True)
        for i in rows:
            del self.ruleset.rules[i]
        if rows:
            self._rules_changed()

    def move_rule(self, d: int) -> None:
        i = self.rule_table.currentRow()
        j = i + d
        rules = self.ruleset.rules
        if 0 <= i < len(rules) and 0 <= j < len(rules):
            rules[i], rules[j] = rules[j], rules[i]
            self._rules_changed()
            self.rule_table.selectRow(j)

    def load_rules(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "규칙 불러오기", self.settings.value("rules_dir", ""),
                                           "규칙 JSON (*.json)")
        if p:
            self._load_rules_path(p)

    def _load_rules_path(self, p: str) -> None:
        if not self._confirm_replace():
            return
        try:
            self.ruleset = RuleSet.load(p)
        except Exception as e:
            QMessageBox.warning(self, "불러오기 실패", str(e))
            return
        self._set_rules_source(Path(p))
        self.settings.setValue("rules_dir", str(self.rules_path.parent))
        self._rules_changed()
        self.statusBar().showMessage(f"규칙 불러옴: {p}")

    def save_rules(self, save_as: bool = False) -> None:
        p = self.rules_path
        if save_as or p is None:
            base = self._library_dir() or self.settings.value("rules_dir", "")
            default = str(self.rules_path or Path(base) / f"{self.ruleset.name}.json")
            s, _ = QFileDialog.getSaveFileName(self, "규칙 저장", default, "규칙 JSON (*.json)")
            if not s:
                return
            p = Path(s)
        self.ruleset.save(p)
        self._set_rules_source(p)
        self.settings.setValue("rules_dir", str(p.parent))
        self._update_library_label()
        self.statusBar().showMessage(f"규칙 저장: {p}")

    def example_rules(self) -> None:
        if self._confirm_replace():
            self.ruleset = example_ruleset()
            self._set_rules_source(None)
            self._rules_changed()

    def new_rules(self) -> None:
        if self._confirm_replace():
            self.ruleset = RuleSet()
            self._set_rules_source(None)
            self._rules_changed()

    def _dirty(self) -> bool:
        return self.ruleset.to_json() != self._saved_json

    def _confirm_replace(self) -> bool:
        """Before replacing the current rule set: offer to save unsaved changes."""
        if not self._dirty():
            return True
        ans = QMessageBox.question(
            self, "저장하지 않은 규칙",
            f"현재 규칙 세트 '{self.ruleset.name}' 에 저장하지 않은 변경이 있습니다.\n"
            "저장할까요? (취소하면 현재 규칙을 그대로 둡니다)",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
        if ans == QMessageBox.Save:
            self.save_rules()
            return not self._dirty()
        return ans == QMessageBox.Discard

    def _set_rules_source(self, path: Path | None) -> None:
        self.rules_path = path
        self._saved_json = self.ruleset.to_json()
        self.settings.setValue("rules_path", str(path) if path else "")

    def _saved_snapshot(self) -> str:
        p = self.settings.value("rules_path", "")
        if p and Path(p).is_file():
            try:
                saved = RuleSet.load(p).to_json()
                self.rules_path = Path(p)
                return saved
            except Exception:
                pass
        return example_ruleset().to_json()

    # ------------------------------------------------------------ rule library
    def _library_dir(self) -> str:
        return self.settings.value("library_dir", "") or ""

    def _update_library_label(self) -> None:
        d = self._library_dir()
        if not d:
            self.lib_label.setText("지정 안 됨 (규칙 JSON 을 모아 둔 폴더)")
            return
        entries, _ = library.scan(d)
        with_id = sum(1 for e in entries if e.ruleset.match_pattern.strip())
        self.lib_label.setText(f"{Path(d).name}  (규칙 {len(entries)}개, 식별 조건 {with_id}개)")
        self.lib_label.setToolTip(d)

    def choose_library(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "규칙 폴더 선택", self._library_dir())
        if d:
            self.set_library(d)

    def set_library(self, d: str) -> None:
        self.settings.setValue("library_dir", d)
        self._update_library_label()

    def _take_match_from_file(self) -> None:
        off = self.rs_match_off.value()
        b = self.data[off:off + 4]
        if not b:
            QMessageBox.information(self, "식별 조건", "현재 파일에 그 위치의 데이터가 없습니다.")
            return
        self.rs_match.setText(b.hex(" ").upper())
        self._rs_settings_changed()

    def _auto_select_rules(self, data: bytes) -> str:
        """Pick the library rule set whose identification matches. Returns a status message."""
        d = self._library_dir()
        if not d or not self.lib_auto.isChecked():
            return ""
        entries, errors = library.scan(d)
        hits = library.find(data, entries)
        warn = f"  (읽지 못한 규칙 파일 {len(errors)}개)" if errors else ""
        if not hits:
            return f"규칙 폴더에 이 파일과 맞는 규칙 세트가 없어 현재 규칙을 그대로 씁니다{warn}"
        best = hits[0]
        rs = best.ruleset
        others = f", 다른 후보 {len(hits) - 1}개" if len(hits) > 1 else ""
        why = f"위치 0x{rs.match_offset:X} = {rs.match_pattern}"
        if self.rules_path and self.rules_path.resolve() == best.path.resolve() and not self._dirty():
            return f"규칙 세트 '{rs.name}' 유지 ({why}{others}){warn}"
        if not self._confirm_replace():
            return f"자동 선택 취소: 현재 규칙 유지 (맞는 규칙: {best.path.name})"
        self.ruleset = rs
        self._set_rules_source(best.path)
        self._rules_changed()
        self.mode.setCurrentIndex(0)
        return f"규칙 자동 선택: {best.path.name} — '{rs.name}' ({why}{others}){warn}"

    # ------------------------------------------------------------ analysis
    def run_analysis(self) -> None:
        if not self.data:
            QMessageBox.information(self, "자동 분석", "먼저 파일을 여세요.")
            return
        self.tabs.setCurrentIndex(2)
        self.an_status.setText("분석 중… (모든 내장 포맷을 시도합니다)")
        job = AnalyzeJob(self.data)
        job.done.connect(self._on_analyzed)
        job.finished.connect(lambda j=job: self.jobs.remove(j) if j in self.jobs else None)
        self.jobs.append(job)
        job.start()

    def _on_analyzed(self, rep) -> None:
        if isinstance(rep, str):
            self.an_status.setText("분석 중 오류")
            self.an_summary.setPlainText(rep)
            return
        self.report = rep
        self.an_status.setText(f"완료: 문자열 {len(rep.strings):,}개, 태그 후보 {len(rep.suggestions)}개")
        summary = heuristics.report_text(heuristics.Report(
            rep.size, rep.entropy, rep.entropy_blocks, rep.magic, rep.transforms, [], [], rep.periods, [],
            rep.top_bytes))
        if rep.transforms:
            summary += "→ 위 툴바의 '전처리'에서 선택하면 변환된 데이터로 해석합니다.\n"
        self.an_summary.setPlainText(summary.replace("== 문자열 0개 (처음 50개) ==\n", ""))

        t = self.an_formats
        t.setRowCount(len(rep.formats))
        for i, f in enumerate(rep.formats):
            t.setItem(i, 0, _ro_item(f"{f.score:.1%}", i))
            t.setItem(i, 1, _ro_item(f.title))
            t.setItem(i, 2, _ro_item(", ".join(f"{k}={v}" for k, v in f.options.items())))
            t.setItem(i, 3, _ro_item(f.first_error))
        t.resizeColumnsToContents()

        s = self.an_sugg
        s.setRowCount(len(rep.suggestions))
        for i, g in enumerate(rep.suggestions):
            s.setItem(i, 0, _ro_item(g.tag, i))
            s.setItem(i, 1, _ro_item(g.kind))
            s.setItem(i, 2, _ro_item(g.count))
            s.setItem(i, 3, _ro_item(", ".join(repr(e) for e in g.examples)))
        s.resizeColumnsToContents()

        st = self.an_strings
        shown = rep.strings[:20000]
        st.setRowCount(len(shown))
        for i, x in enumerate(shown):
            st.setItem(i, 0, _ro_item(f"{x.offset:08X}", x.offset))
            st.setItem(i, 1, _ro_item(x.encoding))
            prev = self.data[max(0, x.offset - 3):x.offset].hex(" ").upper()
            st.setItem(i, 2, _ro_item(prev))
            st.setItem(i, 3, _ro_item(x.text[:200] + ("  ⏎00" if x.terminated else "")))
        st.resizeColumnToContents(0)
        st.resizeColumnToContents(1)
        st.resizeColumnToContents(2)

    def _apply_format_suggestion(self) -> None:
        if not self.report:
            return
        row = self.an_formats.currentRow()
        if not 0 <= row < len(self.report.formats):
            return
        f = self.report.formats[row]
        opts = REGISTRY[f.key].defaults()
        opts.update(f.options)
        self.format_opts[f.key] = opts
        self.mode.setCurrentIndex(self.mode.findData(f.key))
        self._build_option_form(f.key)
        self.decode()

    def _apply_rule_suggestions(self) -> None:
        if not self.report:
            return
        rows = sorted({ix.row() for ix in self.an_sugg.selectedIndexes()})
        existing = {r.tag.replace(" ", "").upper() for r in self.ruleset.rules}
        added = 0
        for i in rows:
            r = copy.deepcopy(self.report.suggestions[i].rule)
            if r.tag.upper() in existing:
                continue
            existing.add(r.tag.upper())
            self.ruleset.rules.append(r)
            added += 1
        if added:
            self._rules_changed()
            self.mode.setCurrentIndex(0)
        self.statusBar().showMessage(f"규칙 {added}개 추가" + ("" if added == len(rows) else " (같은 태그는 건너뜀)"))

    def _jump_string(self, item: QTableWidgetItem) -> None:
        off = self.an_strings.item(item.row(), 0).data(Qt.UserRole)
        if off is not None:
            self.hex.go_to(off)

    def set_theme(self, key: str) -> None:
        self.theme = apply_theme(QApplication.instance(), key)
        self.theme_actions[self.theme].setChecked(True)
        self.settings.setValue("theme", self.theme)

    # ------------------------------------------------------------ close
    def closeEvent(self, e) -> None:
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("offsets", self.chk_offsets.isChecked())
        self.settings.setValue("raw_hex", self.chk_raw.isChecked())
        self.settings.setValue("labels", self.chk_labels.isChecked())
        self.settings.setValue("commas", self.chk_commas.isChecked())
        self.settings.setValue("quotes", self.chk_quotes.isChecked())
        self.settings.setValue("layout", self.layout_combo.currentData())
        self.settings.setValue("float_digits", self.digits_spin.value())
        self.settings.setValue("max_lines", self.lines_spin.value())
        self.settings.setValue("header_footer", self.chk_header_footer.isChecked())
        self.settings.setValue("ruleset_json", self.ruleset.to_json())
        if self.batch_dlg is not None:
            self.batch_dlg.close()
        for j in list(self.jobs):
            j.wait(2000)
        super().closeEvent(e)
