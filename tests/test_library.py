import pytest

from binarytotext import library
from binarytotext.cli import main
from binarytotext.rules import FieldSpec, Rule, RuleSet


def make_library(folder):
    a = RuleSet(name="A 포맷", match_pattern="FE CA", rules=[Rule("09", "문자열A", fields=[FieldSpec("cstring")])])
    a2 = RuleSet(name="A v2", match_pattern="FE CA 02", rules=[Rule("09", "문자열A2", fields=[FieldSpec("cstring")])])
    b = RuleSet(name="B 포맷", match_pattern="'BDAT'", match_offset=4, rules=[Rule("01", "b", fields=[])])
    none = RuleSet(name="식별 없음", rules=[Rule("09", "x")])
    for name, rs in (("a.json", a), ("a2.json", a2), ("b.json", b), ("none.json", none)):
        rs.save(folder / name)
    (folder / "broken.json").write_text("{ not json", encoding="utf-8")


def test_matches_offset_wildcard_and_ascii():
    rs = RuleSet(match_pattern="CA ?? 02", match_offset=1)
    assert rs.matches(b"\xfe\xca\x99\x02")
    assert not rs.matches(b"\xfe\xca\x99")
    assert not RuleSet().matches(b"anything")
    assert not RuleSet(match_pattern="ZZ").matches(b"ZZ")


def test_scan_and_find_most_specific_first(tmp_path):
    make_library(tmp_path)
    entries, errors = library.scan(tmp_path)
    assert len(entries) == 4 and len(errors) == 1 and "broken.json" in errors[0]
    hits = library.find(b"\xfe\xca\x02\x00", entries)
    assert [h.ruleset.name for h in hits] == ["A v2", "A 포맷"]
    assert [h.ruleset.name for h in library.find(b"\x00\x00\x00\x00BDAT", entries)] == ["B 포맷"]
    assert library.find(b"\x00", entries) == []
    assert library.scan(tmp_path / "missing")[0] == []


def test_cli_library(tmp_path, capsys):
    lib = tmp_path / "lib"
    lib.mkdir()
    make_library(lib)
    f = tmp_path / "x.bin"
    f.write_bytes(b"\xfe\xca\x01\x00\x09hi\x00")
    out = tmp_path / "o.txt"
    assert main(["decode", str(f), "--library", str(lib), "-o", str(out), "--no-offsets"]) == 0
    assert '문자열A: "hi"' in out.read_text(encoding="utf-8")
    assert "a.json" in capsys.readouterr().err
    f.write_bytes(b"\x00")
    with pytest.raises(SystemExit):
        main(["decode", str(f), "--library", str(lib)])
