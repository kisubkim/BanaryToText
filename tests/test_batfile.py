"""Generated .bat files: argument mapping, quoting, encoding, and a real run through cmd.exe."""

import subprocess
import sys
from pathlib import Path

import pytest

from binarytotext import batfile
from binarytotext.batfile import BatSpec, bat_text, batch_args, quote, write_bat
from binarytotext.render import RenderOptions
from binarytotext.rules import FieldSpec, Rule, RuleSet

ROOT = Path(__file__).resolve().parents[1]


def test_quote():
    assert quote("abc.json") == "abc.json"
    assert quote(r"C:\내 폴더\a.json") == r'"C:\내 폴더\a.json"'
    assert quote("*.bin;*.dat") == '"*.bin;*.dat"'
    assert quote("100%") == "100%%"  # a .bat would expand %
    assert quote("") == '""'
    assert quote('a"b') == '"a""b"'


def test_batch_args_follow_the_settings():
    spec = BatSpec(rules="r.json", recursive=True, include="*.bin", out_dir="out", pattern="{stem}_x.txt",
                   overwrite=True, pre="xor", pre_arg="5A",
                   render=RenderOptions(offsets=False, labels=False, quotes=False, commas=True, layout="inline",
                                        float_digits=4))
    assert batch_args(spec) == ["--rules", "r.json", "-r", "--include", "*.bin", "--pre", "xor", "--pre-arg", "5A",
                                "-d", "out", "--name", "{stem}_x.txt", "--overwrite", "--no-offsets", "--no-labels",
                                "--no-quotes", "--commas", "--layout", "inline", "--float-digits", "4"]
    fmt = BatSpec(fmt="msgpack", fmt_opts={"max_depth": 50, "raw": True}, library="lib", as_json=True)
    assert batch_args(fmt) == ["--library", "lib", "--format", "msgpack", "--opt", "max_depth=50", "--opt", "raw=true",
                               "--json"]
    assert batch_args(BatSpec(rules="r.json")) == ["--rules", "r.json"]  # defaults add nothing


def test_bat_text_modes():
    drop = bat_text(BatSpec(btt=r"C:\app\btt.exe", rules="r.json", description="규칙 'x'"))
    lines = drop.split("\r\n")
    assert lines[0] == "@echo off" and "rem 해석 방식: 규칙 'x'" in lines
    assert any(ln.startswith('if "%~1"=="" (') for ln in lines)  # nothing dropped: explain and stop
    assert r"C:\app\btt.exe batch %* --rules r.json" in drop and "pause" in lines
    fixed = bat_text(BatSpec(btt=r"C:\내 앱\btt.exe", inputs=[r"D:\data"], rules="r.json", pause=False))
    assert r'"C:\내 앱\btt.exe" batch D:\data --rules r.json' in fixed
    assert "%~1" not in fixed and "pause" not in fixed.split("\r\n")
    beside = bat_text(BatSpec(btt_beside=True, btt="ignored.exe", rules="r.json"))
    assert '"%~dp0btt.exe" batch %*' in beside
    src = bat_text(BatSpec(python=r"C:\py\python.exe", source_dir=r"C:\src", rules="r.json"))
    assert r'set "PYTHONPATH=C:\src"' in src and r"C:\py\python.exe -m binarytotext batch" in src


def test_check():
    assert batfile.check(BatSpec(btt="b.exe")) != ""
    assert batfile.check(BatSpec(rules="r.json")) != ""
    assert batfile.check(BatSpec(btt="b.exe", library="lib")) == ""


def test_write_bat_encoding(tmp_path):
    p = tmp_path / "a.bat"
    text = bat_text(BatSpec(btt="btt.exe", rules="C:\\규칙\\ルール\\r.json", pause=False))
    write_bat(p, text)
    raw = p.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and raw.decode("utf-8") == text  # UTF-8, no BOM
    lines = text.split("\r\n")
    # Everything before the switch to UTF-8 is ASCII; every exit puts the caller's code page back.
    switch = lines.index("chcp 65001 >nul")
    assert all(ln.isascii() for ln in lines[:switch]) and "BTT_CP" in lines[switch - 1]
    assert lines[-3:] == ["chcp %BTT_CP% >nul", "exit /b %ERR%", ""]
    drop = bat_text(BatSpec(btt="btt.exe", rules="r.json")).split("\r\n")
    assert drop[drop.index("  exit /b 1") - 1] == "  chcp %BTT_CP% >nul"


@pytest.mark.skipif(sys.platform != "win32", reason="runs cmd.exe")
def test_generated_bat_really_runs(tmp_path):
    data = tmp_path / "입력 폴더"
    data.mkdir()
    (data / "a.bin").write_bytes(b"\x09hi\x00\x04\x07")
    (data / "b.bin").write_bytes(b"\x04\x08")
    rules = tmp_path / "규칙.json"
    RuleSet(rules=[Rule("09", "s", fields=[FieldSpec("cstring")]), Rule("04", "n", fields=[FieldSpec("uint")])]).save(rules)
    out = tmp_path / "결과 100%"
    base = dict(python=sys.executable, source_dir=str(ROOT), rules=str(rules), out_dir=str(out), include="*.bin",
                pause=False, render=RenderOptions(offsets=False))
    bat = tmp_path / "변환.bat"
    write_bat(bat, bat_text(BatSpec(inputs=[str(data)], overwrite=True, **base)))
    # Korean console (949) and a UTF-8 terminal (65001), each in its own hidden console;
    # the trailing chcp shows the caller's code page is back afterwards.
    for cp in (949, 65001):
        r = subprocess.run(f'cmd /c "chcp {cp} >nul & call "{bat}" & chcp"', capture_output=True, timeout=120,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        assert r.returncode == 0, r.stderr.decode(errors="replace")
        assert r.stdout.decode(errors="replace").strip().endswith(str(cp))
        assert (out / "a.bin.txt").read_text(encoding="utf-8") == 's: "hi"\nn: 7\n'
        assert (out / "b.bin.txt").exists()
    # Drop mode: the files come in as arguments; nothing dropped -> exit code 1 with a hint.
    write_bat(bat, bat_text(BatSpec(**{**base, "pattern": "{stem}_drop.txt"})))
    r = subprocess.run(["cmd", "/c", str(bat), str(data / "b.bin")], capture_output=True, timeout=120)
    assert r.returncode == 0 and (out / "b_drop.txt").read_text(encoding="utf-8") == "n: 8\n"
    r = subprocess.run(["cmd", "/c", str(bat)], capture_output=True, timeout=60, input=b"\r\n")
    assert r.returncode == 1


def test_header_footer_flag():
    assert batch_args(BatSpec(rules="r.json", render=RenderOptions(header_footer=False)))[-1] == "--no-header-footer"
