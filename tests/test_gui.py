"""Drive the real main window offscreen."""

import os
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setenv("BINARYTOTEXT_SETTINGS", str(tmp_path / "settings.ini"))
    from binarytotext.gui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    w.show()
    yield w
    w.close()
    app.processEvents()


def wait_for(cond, timeout=10.0):
    app = QApplication.instance()
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError("timeout")


def test_open_decode_and_link(win, tmp_path):
    f = tmp_path / "sample.bin"
    f.write_bytes(b"\x09hello\x00\x04\x2a\x0a\x09ab\x00\x0a\xff")
    win.open_file(str(f))
    wait_for(lambda: win.result is not None and "hello" in win.text.toPlainText())
    text = win.text.toPlainText()
    assert '문자열: "hello"' in text and "배열 [" in text and "?? 1 byte: FF" in text
    assert win.hex.model_.marks[-1] == 1  # trailing FF is unknown

    # hex cursor -> text line
    win.hex.go_to(7)
    wait_for(lambda: "정수" in win.text.textCursor().block().text())

    # toggle a rule off -> re-decode
    win.ruleset.rules[0].enabled = False
    win._rules_changed()
    wait_for(lambda: '"hello"' not in win.text.toPlainText())

    # switch to a built-in format
    win.mode.setCurrentIndex(win.mode.findData("hexdump"))
    wait_for(lambda: "|" in win.text.toPlainText() and "68 65 6C" in win.text.toPlainText())
    assert win.tree.topLevelItemCount() >= 1


def test_analysis_and_suggestions(win, tmp_path):
    f = tmp_path / "s.bin"
    f.write_bytes(b"".join(b"\x07" + w + b"\x00\x05\x01" for w in (b"alpha", b"bravo", b"charlie", b"delta")))
    win.ruleset.rules.clear()
    win.open_file(str(f))
    win.run_analysis()
    wait_for(lambda: win.report is not None, 20)
    assert win.an_sugg.rowCount() >= 1
    row = next(i for i in range(win.an_sugg.rowCount()) if win.an_sugg.item(i, 0).text() == "07")
    win.an_sugg.selectRow(row)
    win._apply_rule_suggestions()
    wait_for(lambda: '"charlie"' in win.text.toPlainText())


def test_rule_dialog_preview(win, tmp_path):
    from binarytotext.gui.rule_dialog import RuleDialog
    from binarytotext.rules import FieldSpec, Rule
    data = b"\x00\x00\x10\x34\x12"
    dlg = RuleDialog(win, Rule("10", "값", "value", [FieldSpec("uint", size=2)]), win.ruleset, data)
    assert "값: 4660" in dlg.preview.toPlainText()
    dlg.tag.setText("ZZ")
    assert not dlg.ok_button.isEnabled()
    dlg.tag.setText("10")
    ed = dlg.fields_editor
    ed.f_fmt.setCurrentIndex(ed.f_fmt.findData("hex"))
    assert "0x1234" in dlg.preview.toPlainText()


def test_rule_buttons_click(win, monkeypatch):
    """Real button clicks: `clicked(checked)` must not leak into add_rule(tag) / save_rules(save_as)."""
    from PySide6.QtWidgets import QPushButton

    from binarytotext.gui.rule_dialog import RuleDialog
    seen = []

    def fake_exec(dlg):
        seen.append(dlg.rule.tag)
        dlg.tag.setText("77")
        return 1
    monkeypatch.setattr(RuleDialog, "exec", fake_exec)
    buttons = {b.text(): b for b in win.tabs.widget(0).findChildren(QPushButton)}
    n = len(win.ruleset.rules)
    buttons["추가"].click()
    assert seen == [""] and len(win.ruleset.rules) == n + 1 and win.ruleset.rules[-1].tag == "77"


def test_key_rule_and_output_toggles(win, tmp_path):
    from binarytotext.gui.rule_dialog import RuleDialog
    from binarytotext.rules import FieldSpec, Rule
    f = tmp_path / "kv.bin"
    f.write_bytes(b"\x09volume\x00\x04\x1e\x0a\x04\x01\x04\x02\x0a")
    win.open_file(str(f))
    dlg = RuleDialog(win, win.ruleset.rules[0], win.ruleset, win.data)
    dlg.key.setChecked(True)
    assert dlg.rule.key and "volume = 30" in dlg.preview.toPlainText()
    dlg.action.setCurrentIndex(dlg.action.findData("open"))
    assert not dlg.key.isEnabled() and not dlg.rule.key
    win.ruleset.rules[0] = Rule("09", "문자열", fields=[FieldSpec("cstring")], key=True)
    win._rules_changed()
    win.layout_combo.setCurrentIndex(win.layout_combo.findData("arrays"))
    win.chk_labels.setChecked(False)
    wait_for(lambda: "volume = 30" in win.text.toPlainText() and "[1, 2]" in win.text.toPlainText())
    assert "정수" not in win.text.toPlainText()
    win.layout_combo.setCurrentIndex(win.layout_combo.findData("lines"))
    win.chk_commas.setChecked(True)
    wait_for(lambda: "1," in win.text.toPlainText() and "[1, 2]" not in win.text.toPlainText())
    # Per-rule layout wins over the toolbar; the rule dialog preview follows the toolbar options.
    dlg = RuleDialog(win, win.ruleset.rules[2], win.ruleset, win.data)
    dlg.layout.setCurrentIndex(dlg.layout.findData("inline"))
    assert dlg.rule.layout == "inline" and "[1, 2]" in dlg.preview.toPlainText()
    dlg.action.setCurrentIndex(dlg.action.findData("value"))
    assert not dlg.layout.isEnabled() and dlg.rule.layout == ""


def test_rule_dialog_end_tag(win):
    from binarytotext.gui.rule_dialog import RuleDialog
    from binarytotext.rules import Rule
    data = bytes.fromhex("0B 09 61 00 00")
    dlg = RuleDialog(win, Rule("0B", "객체", "open", bracket="{"), win.ruleset, data)
    assert dlg.end_tag.isEnabled()
    dlg.end_tag.setText("ZZ")
    assert not dlg.ok_button.isEnabled()
    dlg.end_tag.setText("00")
    assert dlg.ok_button.isEnabled() and dlg.rule.end_tag == "00"
    dlg.action.setCurrentIndex(dlg.action.findData("value"))
    assert not dlg.end_tag.isEnabled()


def test_library_auto_select(win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from binarytotext.rules import FieldSpec, Rule, RuleSet
    lib = tmp_path / "lib"
    lib.mkdir()
    RuleSet(name="카페 포맷", match_pattern="CA FE",
            rules=[Rule("09", "카페문자열", fields=[FieldSpec("cstring")])]).save(lib / "cafe.json")
    win.set_library(str(lib))
    assert "규칙 1개" in win.lib_label.text()
    f = tmp_path / "c.bin"
    f.write_bytes(b"\xca\xfe\x09yo\x00")

    # Unsaved edits + user cancels -> current rules kept.
    win.ruleset.rules.append(Rule("77", "수정됨"))
    win._rules_changed()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Cancel)
    win.open_file(str(f))
    assert win.ruleset.name != "카페 포맷" and "자동 선택 취소" in win.statusBar().currentMessage()

    # User discards -> library rule set applied.
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Discard)
    win.open_file(str(f))
    assert win.ruleset.name == "카페 포맷" and win.rules_path == lib / "cafe.json"
    wait_for(lambda: '카페문자열: "yo"' in win.text.toPlainText())
    assert not win._dirty()

    # Reopening with nothing changed keeps it without asking.
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: pytest.fail("물어보면 안 됨"))
    win.open_file(str(f))
    assert "유지" in win.statusBar().currentMessage()

    # Identification from the current file.
    win.rs_match_off.setValue(0)
    win._take_match_from_file()
    assert win.ruleset.match_pattern == "CA FE 09 79"


def test_batch_dialog(win, tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    (src / "a.bin").write_bytes(b"\x09one\x00")
    (src / "b.bin").write_bytes(b"\x09two\x00")
    out = tmp_path / "out"
    win.batch_convert([str(src)])
    dlg = win.batch_dlg
    assert "변환 대상 2개" in dlg.count_label.text() and "사용자 규칙" in dlg.base_label.text()
    dlg.out_dir.setText(str(out))
    dlg.pattern.setText("{bad}.txt")
    assert not dlg.run_btn.isEnabled()
    dlg.pattern.setText("{stem}.txt")
    dlg.as_json.setChecked(True)
    assert dlg.pattern.text() == "{stem}.json"
    dlg.as_text.setChecked(True)
    dlg.start()
    wait_for(lambda: dlg.job is None)
    assert dlg.table.rowCount() == 2 and "완료 2개" in dlg.status.text()
    assert '"two"' in (out / "b.txt").read_text(encoding="utf-8")
    # Reopening keeps the list; settings were remembered.
    win.batch_convert()
    assert win.batch_dlg is dlg and win.settings.value("batch_pattern") == "{stem}.txt"


def test_header_dialog(win):
    from binarytotext.gui.header_dialog import HeaderDialog
    from binarytotext.rules import FieldSpec
    data = bytes.fromhex("FECA 0100 0904 0A DEADBEEF")
    dlg = HeaderDialog(win, win.ruleset, data)
    dlg.head_editor._add()
    ed = dlg.head_editor
    ed.f_type.setCurrentIndex(ed.f_type.findData("uint"))
    ed.f_size.setValue(2)
    ed.f_name.setText("매직")
    ed.f_fmt.setCurrentIndex(ed.f_fmt.findData("hex"))
    assert "매직: 0xCAFE" in dlg.preview.toPlainText()
    dlg.footer.append(FieldSpec("cstring"))
    dlg._changed()
    assert "고정 크기" in dlg.msg.text()
    dlg.footer[:] = [FieldSpec("uint", "CRC", size=4, endian="big", fmt="hex")]
    dlg._changed()
    assert "CRC: 0xDEADBEEF" in dlg.preview.toPlainText()
    win.ruleset.header, win.ruleset.footer = dlg.header, dlg.footer
    win._rules_changed()
    assert "매직" in win.hf_label.text() and "CRC" in win.hf_label.text()


def test_themes(win):
    from binarytotext.gui.theme import DEFAULT, THEMES
    app = QApplication.instance()
    assert win.theme == DEFAULT
    for key in [*THEMES, "system", DEFAULT]:
        win.theme_actions[key].trigger()
        app.processEvents()
        assert win.theme == key and win.theme_actions[key].isChecked()
        if key == "system":
            assert app.styleSheet() == ""
        else:
            assert app.palette().window().color().name() == THEMES[key].bg
    assert app.styleSheet() and win.settings.value("theme") == DEFAULT
    win.set_theme("no-such-theme")  # unknown keys fall back to the default
    assert win.theme == DEFAULT


def test_rule_enable_checkbox_centered(win):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    t = win.rule_table
    rect = t.visualRect(t.model().index(0, 0))
    assert win.ruleset.rules[0].enabled
    QTest.mouseClick(t.viewport(), Qt.LeftButton, pos=rect.center())  # middle of the cell toggles
    assert not win.ruleset.rules[0].enabled
    QTest.mouseClick(t.viewport(), Qt.LeftButton, pos=QPoint(rect.left() + 4, rect.center().y()))
    assert not win.ruleset.rules[0].enabled  # the old left-edge spot is just a row click now
    QTest.mouseClick(t.viewport(), Qt.LeftButton, pos=rect.center())
    assert win.ruleset.rules[0].enabled


def test_save_runs_in_background_and_stops_runaway_groups(win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    from binarytotext.rules import FieldSpec, Rule, RuleSet
    f, out = tmp_path / "x.bin", tmp_path / "out.txt"
    f.write_bytes(b"\x0b\x09abc\x00" * 20_000)  # wrong end tag: every 0B nests one level deeper
    win.ruleset = RuleSet(rules=[Rule("0B", "레코드", "open", bracket="{", end_tag="FF FF"),
                                 Rule("09", "이름", fields=[FieldSpec("cstring")])], max_depth=30)
    win._refresh_rule_table()
    assert win.rs_depth.value() == 30
    win.open_file(str(f))
    wait_for(lambda: win.result is not None and "30단계" in win.text.toPlainText())
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    win.save_text()
    assert win.save_job is not None
    wait_for(lambda: win.save_job is None)
    assert "30단계" in out.read_text(encoding="utf-8") and "저장:" in win.statusBar().currentMessage()
    win.save_json()
    wait_for(lambda: win.save_job is None)
    assert '"kind": "error"' in out.read_text(encoding="utf-8")
    win.rs_depth.setValue(12)
    assert win.ruleset.max_depth == 12


def test_rule_dialog_const(win):
    from binarytotext.gui.rule_dialog import RuleDialog
    from binarytotext.rules import Rule
    dlg = RuleDialog(win, Rule("01", "켜짐", "value"), win.ruleset, b"\x01\x01")
    assert not dlg.const_text.isEnabled()
    dlg.action.setCurrentIndex(dlg.action.findData("const"))
    assert dlg.const_text.isEnabled() and dlg.key.isEnabled()
    dlg.const_text.setText("true")
    assert dlg.rule.text == "true" and "켜짐: true" in dlg.preview.toPlainText()
    dlg.action.setCurrentIndex(dlg.action.findData("open"))
    assert dlg.rule.text == "" and not dlg.key.isEnabled()
    dlg.close()


def test_screen_line_limit_and_float_digits(win, tmp_path):
    import struct
    f = tmp_path / "x.bin"
    f.write_bytes((b"\x04\x01" * 3000) + b"\x05" + struct.pack("<f", 36.6))
    from binarytotext.rules import FieldSpec, Rule, RuleSet
    win.ruleset = RuleSet(rules=[Rule("04", "n", fields=[FieldSpec("uint")]),
                                 Rule("05", "t", fields=[FieldSpec("float", size=4)])])
    win._refresh_rule_table()
    win.open_file(str(f))
    wait_for(lambda: win.result is not None and "t: 36.6" in win.text.toPlainText())
    win.lines_spin.setValue(1000)
    wait_for(lambda: "1,000줄에서 잘렸습니다" in win.text.toPlainText())
    assert len(win.text.toPlainText().splitlines()) == 1001  # 1000 lines + the cut note
    win.lines_spin.setValue(10_000)
    win.digits_spin.setValue(9)
    wait_for(lambda: "t: 36.5999985" in win.text.toPlainText())
    assert win.digits_spin.text() != "자동" and win._render_opts().float_digits == 9
    win.digits_spin.setValue(0)
    assert win.digits_spin.text() == "자동"
    win.close()
    assert win.settings.value("max_lines", type=int) == 10_000


def test_output_options_above_results(win):
    # The output options sit in the result panel, above the text/tree tabs.
    for w in (win.chk_offsets, win.chk_quotes, win.layout_combo, win.digits_spin, win.lines_spin):
        assert win.out_panel.isAncestorOf(w)
    assert win.out_panel.isAncestorOf(win.text)


def test_make_bat(win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    rules_file, bat = tmp_path / "규칙.json", tmp_path / "변환.bat"
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Save)
    answers = iter([(str(rules_file), ""), (str(bat), "")])  # save rules first, then the .bat
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: next(answers))
    win.chk_commas.setChecked(True)
    win.digits_spin.setValue(5)
    dlg = win.make_bat()
    assert dlg is not None and rules_file.exists()  # unsaved rules were saved for the .bat
    dlg.fixed.setChecked(True)
    dlg.input.setText(str(tmp_path / "data"))
    dlg.out_dir.setText(str(tmp_path / "out"))
    preview = dlg.preview.toPlainText()
    assert f"--rules {rules_file}" in preview.replace('"', "") and "--commas" in preview
    assert "--float-digits 5" in preview and "%~1" not in preview
    assert dlg.save() == bat and bat.exists()
    assert win.settings.value("bat_out_dir") == str(tmp_path / "out")
    dlg.drop.setChecked(True)
    dlg.input.setText("")
    assert "%*" in dlg.preview.toPlainText() and dlg.save_btn.isEnabled()
    dlg.close()


def test_header_footer_checkbox(win, tmp_path):
    from binarytotext.rules import FieldSpec, Rule, RuleSet
    f = tmp_path / "x.bin"
    f.write_bytes(b"MG\x04\x01\x99")
    win.ruleset = RuleSet(rules=[Rule("04", "n", fields=[FieldSpec("uint")])],
                          header=[FieldSpec("fstring", "magic", size=2)], footer=[FieldSpec("uint", "crc")])
    win._refresh_rule_table()
    win.open_file(str(f))
    wait_for(lambda: win.result is not None and "magic" in win.text.toPlainText())
    win.chk_header_footer.setChecked(False)
    wait_for(lambda: "magic" not in win.text.toPlainText() and "n: 1" in win.text.toPlainText())
    assert "crc" not in win.text.toPlainText() and not win._render_opts().header_footer



def test_rule_dialog_group_items(win):
    from binarytotext.gui.rule_dialog import RuleDialog
    from binarytotext.rules import Rule
    dlg = RuleDialog(win, Rule("0A", "배열", "toggle"), win.ruleset, b"\x0a\x09a\x00\x04\x01\x0a")
    assert dlg.items.isEnabled() and dlg.rule.items == ""
    dlg.items.setCurrentIndex(dlg.items.findData("named"))
    assert dlg.rule.items == "named"
    dlg.action.setCurrentIndex(dlg.action.findData("value"))
    assert not dlg.items.isEnabled() and dlg.rule.items == ""
    dlg.close()



def test_rule_dialog_commas(win):
    from binarytotext.gui.rule_dialog import RuleDialog
    arr = next(r for r in win.ruleset.rules if r.tag == "0A")  # the example set's own 0A array rule
    dlg = RuleDialog(win, arr, win.ruleset, b"\x0a\x04\x01\x04\x02\x0a")
    assert dlg.commas.isEnabled() and dlg.rule.commas == ""
    dlg.commas.setCurrentIndex(dlg.commas.findData("off"))
    assert dlg.rule.commas == "off" and "," not in dlg.preview.toPlainText().split("\n", 1)[1]
    dlg.action.setCurrentIndex(dlg.action.findData("value"))
    assert not dlg.commas.isEnabled() and dlg.rule.commas == ""
    dlg.close()
