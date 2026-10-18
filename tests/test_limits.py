"""Runaway decodes (wrong group end tag), streamed output files and the const rule."""

import pytest

from binarytotext.cli import main
from binarytotext.model import ERROR, GROUP, Node
from binarytotext.render import RenderOptions, render_json, render_text, write_json, write_text
from binarytotext.rules import FieldSpec, Rule, RuleEngine, RuleSet, decode_with_rules


def depth_of(root: Node) -> int:
    best, stack = 0, [(root, 0)]
    while stack:
        n, d = stack.pop()
        best = max(best, d)
        stack.extend((c, d + 1) for c in n.children)
    return best


def wrong_end_tag() -> tuple[bytes, RuleSet]:
    # Every record really ends with 00, but the rule waits for FF FF: each 0B opens a group inside the last one.
    rs = RuleSet(rules=[Rule("0B", "레코드", "open", bracket="{", end_tag="FF FF"),
                        Rule("09", "이름", fields=[FieldSpec("cstring")])])
    return b"\x0b\x09abc\x00" * 50_000, rs


def test_wrong_end_tag_stops_at_max_depth():
    data, rs = wrong_end_tag()
    root = decode_with_rules(data, rs)
    assert depth_of(root) <= rs.max_depth + 2
    errors = [n for n in root.walk() if n.kind == ERROR]
    assert errors and "100단계" in errors[0].label
    text = render_text(root, data, RenderOptions())
    assert len(text) < 200_000  # was gigabytes: one more indentation level per record
    rs.max_depth = 5
    root = decode_with_rules(data, rs)
    assert depth_of(root) <= 7 and "5단계" in [n for n in root.walk() if n.kind == ERROR][0].label


def test_max_depth_round_trips_and_defaults():
    rs = RuleSet(max_depth=7)
    assert RuleSet.from_dict({"name": "x"}).max_depth == 100  # old rule files
    assert RuleSet.from_dict(__import__("json").loads(rs.to_json())).max_depth == 7


def test_node_limit_reports_why_it_stopped():
    rs = RuleSet(rules=[Rule("04", "정수", fields=[FieldSpec("uint", size=1)])])
    root = RuleEngine(rs).decode(b"\x04\x01" * 100, max_nodes=10)
    assert any("10개" in n.label for n in root.children if n.kind == ERROR)


def test_walk_is_not_recursive():
    root = cur = Node(0, 0, GROUP, "r")
    for _ in range(50_000):
        cur = cur.add(Node(0, 0, GROUP, "g"))
    assert sum(1 for _ in root.walk()) == 50_001


def test_write_text_and_json_stream_to_file(tmp_path):
    data = b"\x09ab\x00\x0a\x04\x01\x04\x02\x0a"
    rs = RuleSet(rules=[Rule("09", "s", fields=[FieldSpec("cstring")]), Rule("04", "n", fields=[FieldSpec("uint")]),
                        Rule("0A", "a", "toggle")])
    root = decode_with_rules(data, rs)
    out = tmp_path / "o.txt"
    assert write_text(out, root, data, RenderOptions()) == len(render_text(root, data).splitlines())
    assert out.read_text(encoding="utf-8") == render_text(root, data)
    write_json(tmp_path / "o.json", root)
    assert (tmp_path / "o.json").read_text(encoding="utf-8") == render_json(root)
    assert not list(tmp_path.glob("*.part"))


def test_failed_write_keeps_old_file(tmp_path):
    out = tmp_path / "o.txt"
    out.write_text("old", encoding="utf-8")
    bad = Node(0, 0, GROUP, "r")
    bad.children.append(None)  # rendering blows up half way
    with pytest.raises(AttributeError):
        write_text(out, bad, b"")
    assert out.read_text(encoding="utf-8") == "old" and not list(tmp_path.glob("*.part"))


def const_rules() -> RuleSet:
    return RuleSet(rules=[
        Rule("01", "켜짐", "const", text="true"),
        Rule("02", "없음", "const", text="null"),
        Rule("03", "상태", "const", [FieldSpec("skip", size=2)], text='"OK"'),
        Rule("09", "문자열", fields=[FieldSpec("cstring")], key=True),
        Rule("0A", "배열", "toggle"),
    ])


def test_const_rule_outputs_fixed_text():
    data = b"\x09on\x00\x01\x09none\x00\x02\x09st\x00\x03\xaa\xbb\x0a\x01\x02\x0a"
    root = decode_with_rules(data, const_rules())
    opts = RenderOptions(offsets=False, layout="arrays", labels=False)
    assert render_text(root, data, opts).splitlines() == ["on = true", "none = null", 'st = "OK"', "[true, null]"]
    plain = RenderOptions(offsets=False)
    lines = render_text(decode_with_rules(b"\x01\x03\x00\x00", const_rules()), b"", plain).splitlines()
    assert lines == ["켜짐: true", '상태: "OK"']  # the skip field's 2 bytes are consumed, not shown
    assert decode_with_rules(b"\x03\x00\x00", const_rules()).children[0].length == 3


def test_const_rule_as_key_and_summary():
    rs = const_rules()
    rs.rules.append(Rule("20", "id 키", "const", text="id", key=True))
    rs.rules.append(Rule("04", "정수", fields=[FieldSpec("uint", size=1)]))
    data = b"\x20\x04\x07"
    assert render_text(decode_with_rules(data, rs), data, RenderOptions(offsets=False)) == "id = 7\n"
    assert rs.rules[2].fields_summary() == '= "OK"  (건너뛰기: skip[2])'
    assert rs.rules[-2].fields_summary() == "= id  → 다음 값의 키"


def test_cli_writes_runaway_file_quickly(tmp_path):
    data, rs = wrong_end_tag()
    f, rp, out = tmp_path / "x.bin", tmp_path / "r.json", tmp_path / "o.txt"
    f.write_bytes(data)
    rs.save(rp)
    assert main(["decode", str(f), "--rules", str(rp), "-o", str(out)]) == 0
    assert "100단계" in out.read_text(encoding="utf-8")


def float_rules() -> RuleSet:
    return RuleSet(rules=[Rule("05", "f", fields=[FieldSpec("float", size=4)]),
                          Rule("06", "d", fields=[FieldSpec("float", size=8)])])


def test_float_digits():
    import struct

    from binarytotext.reader import float_digits
    data = b"\x05" + struct.pack("<f", 36.6) + b"\x06" + struct.pack("<d", 1 / 3)

    def values(n):
        with float_digits(n):
            return [c.value for c in decode_with_rules(data, float_rules()).children]
    assert values(0) == ["36.6", "0.3333333333333333"]  # automatic: shortest exact form
    assert values(3) == ["36.6", "0.333"]
    assert values(9) == ["36.5999985", "0.333333333"]  # float32's real value shows up
    assert values(2) == ["37", "0.33"]
    assert values(0) == ["36.6", "0.3333333333333333"]  # the setting does not leak


def test_float_digits_in_formats_and_threads():
    import struct
    import threading

    from binarytotext.formats import REGISTRY
    from binarytotext.reader import float_digits
    mp = b"\xcb" + struct.pack(">d", 3.14159265)
    dec = REGISTRY["msgpack"]
    with float_digits(4):
        assert "3.142" in render_text(dec.decode(mp, dec.defaults()), mp)
        seen = []
        t = threading.Thread(target=lambda: seen.append(render_text(dec.decode(mp, dec.defaults()), mp)))
        t.start()
        t.join()
    assert "3.14159265" in seen[0]  # other threads keep their own setting


def test_cli_float_digits(tmp_path):
    import struct
    f, rp, out = tmp_path / "x.bin", tmp_path / "r.json", tmp_path / "o.txt"
    f.write_bytes(b"\x06" + struct.pack("<d", 2 / 3))
    float_rules().save(rp)
    assert main(["decode", str(f), "--rules", str(rp), "--no-offsets", "--float-digits", "4", "-o", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == "d: 0.6667\n"
    d = tmp_path / "out"
    assert main(["batch", str(f), "--rules", str(rp), "-d", str(d), "--no-offsets", "--float-digits", "2"]) == 0
    assert [p.read_text(encoding="utf-8") for p in d.iterdir()] == ["d: 0.67\n"]
