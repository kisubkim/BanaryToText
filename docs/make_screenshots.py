"""Regenerate the manual's sample files (examples/manual) and screenshots (docs/images).

Run from the project folder:  .venv/Scripts/python docs/make_screenshots.py
Windows pop up briefly; real fonts are used (no offscreen platform).
"""

from __future__ import annotations

import os
import struct
import sys
import tempfile
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"  # 1:1 pixel screenshots
os.environ["BINARYTOTEXT_SETTINGS"] = str(Path(tempfile.mkdtemp()) / "settings.ini")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402

from binarytotext.rules import FieldSpec, Rule, RuleSet  # noqa: E402

SAMPLES = ROOT / "examples" / "manual"
RULES = SAMPLES / "rules"
IMAGES = ROOT / "docs" / "images"


# ---------------------------------------------------------------- sample files
def sensor_ruleset() -> RuleSet:
    return RuleSet(
        name="센서 로그 (BDAT)", endian="little", encoding="utf-8",
        match_pattern="'BDAT'", match_offset=0,
        header=[FieldSpec("fstring", "매직", size=4), FieldSpec("uint", "버전", size=2),
                FieldSpec("uint", "센서 수", size=1)],
        footer=[FieldSpec("uint", "CRC32", size=4, endian="big", fmt="hex")],
        rules=[
            Rule("0B", "센서", "open", bracket="{", end_tag="00", comment="0B 로 열고 00 으로 닫는 객체"),
            Rule("09", "이름", "value", [FieldSpec("cstring")], comment="00 까지 문자열"),
            Rule("04", "채널", "value", [FieldSpec("uint", size=1)], comment="1 byte 정수"),
            Rule("05", "온도", "value", [FieldSpec("float", size=4)], comment="4 byte 실수"),
            Rule("06", "측정 시각", "value", [FieldSpec("unixtime", size=4)], comment="Unix 초"),
            Rule("0A", "기록", "toggle", bracket="[", comment="0A 로 열고 다시 0A 로 닫는 배열"),
        ])


def sensor_file(sensors: list[tuple[str, int, float, int, list[int]]], version: int = 1) -> bytes:
    body = b""
    for name, ch, temp, ts, hist in sensors:
        body += b"\x0b" + b"\x09" + name.encode() + b"\x00" + b"\x04" + bytes([ch])
        body += b"\x05" + struct.pack("<f", temp) + b"\x06" + struct.pack("<I", ts)
        body += b"\x0a" + b"".join(b"\x04" + bytes([h]) for h in hist) + b"\x0a" + b"\x00"
    data = b"BDAT" + struct.pack("<HB", version, len(sensors)) + body
    return data + struct.pack(">I", zlib.crc32(data))


def msgpack_sample() -> bytes:
    def s(t: str) -> bytes:
        b = t.encode()
        return bytes([0xA0 | len(b)]) + b
    return (b"\x83" + s("name") + s("Sensor-A") + s("temp") + b"\xcb" + struct.pack(">d", 36.6)
            + s("tags") + b"\x93\x01\x02\x03")


def unknown_sample() -> bytes:
    out = b""
    for i, w in enumerate(["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]):
        out += b"\x07" + w.encode() + b"\x00" + b"\x03" + struct.pack("<H", 100 + i * 7)
    return b"\x7fLOG" + out


def settings_ruleset() -> RuleSet:
    return RuleSet(
        name="설정 파일 (CFG)", endian="little", encoding="utf-8",
        match_pattern="'CFG'", match_offset=0,
        header=[FieldSpec("fstring", "매직", size=3)],
        rules=[
            Rule("09", "문자열", "value", [FieldSpec("cstring")], key=True,
                 comment="키 이름. 바로 다음 항목이 그 값"),
            Rule("04", "정수", "value", [FieldSpec("uint", size=1)], comment="1 byte 정수"),
            Rule("05", "정수16", "value", [FieldSpec("uint", size=2)], comment="2 byte 정수"),
            Rule("0A", "배열", "toggle", bracket="[", comment="0A 로 열고 다시 0A 로 닫음"),
            Rule("0B", "객체", "open", bracket="{", end_tag="00", comment="0B 로 열고 00 으로 닫음"),
        ])


def settings_file() -> bytes:
    def s(t: str) -> bytes:
        return b"\x09" + t.encode() + b"\x00"
    return (b"CFG" + s("device") + s("BTT-01") + s("volume") + b"\x04\x1e" + s("mode") + s("auto")
            + s("channels") + b"\x0a\x04\x01\x04\x02\x04\x03\x0a"
            + s("network") + b"\x0b" + s("ip") + s("192.168.0.10") + s("port") + b"\x05" + struct.pack("<H", 8080)
            + b"\x00")


def write_samples() -> None:
    RULES.mkdir(parents=True, exist_ok=True)
    sensor_ruleset().save(RULES / "sensor_log.json")
    t0 = 1792224000  # 2026-10-17 08:00 UTC
    (SAMPLES / "sensor_01.bin").write_bytes(sensor_file([
        ("Sensor-A", 1, 36.6, t0, [21, 22, 24]), ("Sensor-B", 2, 21.3, t0 + 60, [18, 19])]))
    (SAMPLES / "sensor_02.bin").write_bytes(sensor_file([("Sensor-C", 3, 28.1, t0 + 120, [30, 31, 29, 28])]))
    (SAMPLES / "sensor_03.bin").write_bytes(sensor_file([("Sensor-D", 4, 19.9, t0 + 180, [7])]))
    (SAMPLES / "profile.msgpack").write_bytes(msgpack_sample())
    (SAMPLES / "unknown.bin").write_bytes(unknown_sample())
    settings_ruleset().save(RULES / "settings_kv.json")
    (SAMPLES / "settings.bin").write_bytes(settings_file())


# ---------------------------------------------------------------- screenshots
app: QApplication


def settle(sec: float = 0.4) -> None:
    end = time.time() + sec
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def wait(cond, timeout: float = 15) -> None:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            settle(0.2)
            return
        time.sleep(0.02)
    raise RuntimeError("timeout")


def save(widget, name: str) -> None:
    settle()
    widget.grab().save(str(IMAGES / name))
    print("saved", name)


def main() -> None:
    global app
    write_samples()
    IMAGES.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv)
    from binarytotext.gui.header_dialog import HeaderDialog
    from binarytotext.gui.icon import app_icon
    from binarytotext.gui.main_window import MainWindow
    from binarytotext.gui.rule_dialog import RuleDialog
    app.setWindowIcon(app_icon())

    w = MainWindow()
    w.resize(1600, 900)
    w.move(20, 20)
    w.show()
    settle()

    # 1. Empty window
    save(w, "01_start.png")

    # 2. Rule folder + auto selection on open
    w.set_library(str(RULES))
    w.open_file(str(SAMPLES / "sensor_01.bin"))
    wait(lambda: w.result is not None and "Sensor-B" in w.text.toPlainText())
    save(w, "02_main_decoded.png")  # status bar still shows the auto-selection message

    # 3. Panels
    w.hex.go_to(0x10)
    save(w.tabs, "03_rules_tab.png")
    save(w.hex.parent(), "04_hex_inspector.png")
    w.text.parent().parent().setCurrentIndex(1)  # output: tree tab
    tree = w.tree
    tree.expandAll()
    save(tree.parent().parent(), "05_tree.png")
    w.text.parent().parent().setCurrentIndex(0)

    # 3b. Output options: values only + one-line arrays
    w.chk_labels.setChecked(False)
    w.chk_inline.setChecked(True)
    wait(lambda: "[21, 22, 24]" in w.text.toPlainText())
    save(w.text.parent().parent(), "16_values_only.png")
    w.chk_labels.setChecked(True)
    wait(lambda: "이름:" in w.text.toPlainText())
    save(w.text.parent().parent(), "17_inline_arrays.png")
    w.chk_inline.setChecked(False)
    wait(lambda: "기록 [" in w.text.toPlainText() and "[21" not in w.text.toPlainText())

    # 4. Rule dialog (object with end tag) and a value rule
    rs = w.ruleset
    dlg = RuleDialog(w, rs.rules[0], rs, w.data)
    dlg.resize(820, 960)
    dlg.show()
    save(dlg, "06_rule_dialog_open.png")
    dlg.close()
    dlg = RuleDialog(w, rs.rules[3], rs, w.data)
    dlg.resize(820, 960)
    dlg.show()
    save(dlg, "07_rule_dialog_value.png")
    dlg.close()

    # 5. Header / footer
    hd = HeaderDialog(w, rs, w.data)
    hd.show()
    save(hd, "08_header_dialog.png")
    hd.close()

    # 6. Unknown bytes: disable the float rule and show unknown runs
    rs.rules[3].enabled = False
    w._rules_changed()
    wait(lambda: "?? " in w.text.toPlainText())
    w.hex.go_to(0x1A)
    save(w, "09_unknown_bytes.png")

    # 7. Hex context menu: add a rule from selected bytes
    first = w.data.index(b"\x05")
    w.hex.go_to(first)
    m = w.hex.model_
    sel = w.hex.selectionModel()
    from PySide6.QtCore import QItemSelectionModel
    sel.select(m.index(first // 16, first % 16), QItemSelectionModel.ClearAndSelect)
    captured: list[QMenu] = []

    class NonBlockingMenu(QMenu):
        def exec(self, pos=None, *a):
            self.popup(pos)
            captured.append(self)

    from binarytotext.gui import hexview
    hexview.QMenu = NonBlockingMenu
    rect = w.hex.visualRect(m.index(first // 16, first % 16))
    w.hex._menu(rect.center())
    settle()
    hexview.QMenu = QMenu
    menu = captured[0]
    base = w.hex.parent().grab()
    mp = menu.grab()
    p = QPainter(base)
    at = w.hex.viewport().mapTo(w.hex.parent(), rect.center()) + QPoint(4, 4)
    p.drawPixmap(at, mp)
    p.setPen(Qt.gray)
    p.drawRect(at.x(), at.y(), mp.width() - 1, mp.height() - 1)
    p.end()
    menu.close()
    base.save(str(IMAGES / "10_hex_menu.png"))
    print("saved 10_hex_menu.png")
    rs.rules[3].enabled = True
    w._rules_changed()
    w._set_rules_source(RULES / "sensor_log.json")

    # 8. Automatic analysis on an unknown file
    w.lib_auto.setChecked(False)
    from binarytotext.rules import RuleSet as RS
    w.ruleset = RS(name="새 규칙 세트")
    w._set_rules_source(None)
    w._rules_changed()
    w.open_file(str(SAMPLES / "unknown.bin"))
    w.tabs.setCurrentIndex(2)
    w.run_analysis()
    wait(lambda: w.report is not None, 30)
    save(w, "11_analysis.png")
    row = next(i for i in range(w.an_sugg.rowCount()) if w.an_sugg.item(i, 0).text() == "07")
    w.an_sugg.selectRow(row)
    w._apply_rule_suggestions()
    wait(lambda: '"charlie"' in w.text.toPlainText())
    w.tabs.setCurrentIndex(0)
    save(w, "12_suggestion_applied.png")

    # 9. Built-in format
    w.open_file(str(SAMPLES / "profile.msgpack"))
    w.mode.setCurrentIndex(w.mode.findData("msgpack"))
    w.tabs.setCurrentIndex(1)
    wait(lambda: "Sensor-A" in w.text.toPlainText())
    save(w, "13_builtin_format.png")

    # 9b. Keys read from the data: key = value
    w.mode.setCurrentIndex(0)
    w.lib_auto.setChecked(True)
    w._set_rules_source(None)  # current edits count as saved: no prompt on auto-selection
    w.open_file(str(SAMPLES / "settings.bin"))
    wait(lambda: "volume = 30" in w.text.toPlainText())
    w.chk_inline.setChecked(True)
    wait(lambda: "[1, 2, 3]" in w.text.toPlainText())
    save(w, "18_key_value.png")
    dlg = RuleDialog(w, w.ruleset.rules[0], w.ruleset, w.data)
    dlg.resize(820, 960)
    dlg.show()
    save(dlg, "19_rule_dialog_key.png")
    dlg.close()
    w.chk_inline.setChecked(False)

    # 10. Batch conversion
    out = ROOT / "build" / "manual" / "결과"  # build/ is gitignored
    import shutil
    shutil.rmtree(out, ignore_errors=True)
    w.batch_convert([str(SAMPLES)])
    bd = w.batch_dlg
    bd.include.setText("*.bin")
    bd._refresh_count()
    bd.out_dir.setText(str(out))
    bd.pattern.setText("{stem}_해석.txt")
    bd.move(60, 60)
    save(bd, "14_batch_before.png")
    bd.start()
    wait(lambda: bd.job is None, 30)
    save(bd, "15_batch_done.png")
    bd.close()
    w.close()


if __name__ == "__main__":
    main()
