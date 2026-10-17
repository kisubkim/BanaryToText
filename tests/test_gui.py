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
