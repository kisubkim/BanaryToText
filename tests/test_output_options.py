"""Data keys ("key = value"), values-only output and one-line arrays."""

from binarytotext.cli import main
from binarytotext.render import RenderOptions, render_json, render_text
from binarytotext.rules import FieldSpec, Rule, RuleSet, decode_with_rules


def kv_rules(**extra) -> RuleSet:
    return RuleSet(rules=[
        Rule("09", "문자열", fields=[FieldSpec("cstring")], key=True),
        Rule("04", "정수", fields=[FieldSpec("uint", size=1)]),
        Rule("0A", "배열", "toggle"),
        Rule("0B", "객체", "open", bracket="{", end_tag="00"),
    ], **extra)


def render(data, rs, **opts):
    return render_text(decode_with_rules(data, rs), data, RenderOptions(offsets=False, **opts))


def test_key_value_pairs_and_alternation():
    data = b"\x09volume\x00\x04\x1e\x09mode\x00\x09auto\x00\x09list\x00\x0a\x04\x01\x04\x02\x0a"
    text = render(data, kv_rules())
    assert text.splitlines()[:3] == ["volume = 30", "mode = auto", "list = ["]
    root = decode_with_rules(data, kv_rules())
    first = root.children[0]
    assert (first.offset, first.length, first.key) == (0, 10, True)  # span covers key and value
    assert '"key": true' in render_json(root)
    assert '"value": "\\"auto\\""' in render_json(root)  # JSON keeps the quoted string


def test_key_string_values_lose_only_their_own_quotes():
    rs = kv_rules()
    rs.rules.append(Rule("0D", "둘", fields=[FieldSpec("cstring"), FieldSpec("cstring")]))
    data = (b'\x09a\x00\x09say "hi"\x00' + b"\x09b\x00\x0dx\x00y\x00"
            + b"\x09c\x00\x0a\x09p\x00\x09q\x00\x0a")
    lines = render(data, rs, inline_arrays=True).splitlines()
    assert lines[0] == 'a = say \\"hi\\"'  # one string: outer quotes gone, escapes stay
    assert lines[1] == 'b = "x", "y"'  # two strings: left as is
    assert lines[2:] == ["c = [p = q]"]  # inside the array 09 is a key again


def test_plain_strings_keep_quotes():
    data = b"\x09a\x00\x09b\x00"
    rs = kv_rules()
    rs.rules[0].key = False
    assert render(data, rs).splitlines() == ['문자열: "a"', '문자열: "b"']


def test_key_inside_object_and_unpaired_key():
    data = b"\x0b\x09a\x00\x04\x07\x00\x09dangling\x00"
    assert render(data, kv_rules()).splitlines() == ["객체 {", "  a = 7", "}", '문자열: "dangling"']


def test_unknown_bytes_break_a_pair():
    data = b"\x09k\x00\xee\x04\x05"
    lines = render(data, kv_rules()).splitlines()
    assert lines[0] == '문자열: "k"' and lines[1].startswith("??") and lines[2] == "정수: 5"


def test_key_and_value_count_as_one_block_item():
    rs = kv_rules()
    rs.rules.append(Rule("0C", "맵", "count_block", [FieldSpec("uint", size=1)], bracket="{"))
    data = b"\x0c\x02\x09a\x00\x04\x01\x09b\x00\x04\x02\x04\x09"
    assert render(data, rs).splitlines() == ["맵 (2개) {", "  a = 1", "  b = 2", "}", "정수: 9"]


def test_values_only_and_inline_arrays():
    rs = RuleSet(rules=[
        Rule("0B", "센서", "open", [FieldSpec("uint", size=1)], bracket="{", end_tag="00"),
        Rule("09", "이름", fields=[FieldSpec("cstring")]),
        Rule("04", "채널", fields=[FieldSpec("uint", size=1)]),
        Rule("05", "온도", fields=[FieldSpec("uint", size=1)]),
        Rule("0A", "기록", "toggle"),
        Rule("FF", "구분선"),
    ])
    data = b"\x0b\x07\x09A\x00\x0a\x04\x15\x04\x16\x0a\x0a\x04\x01\x05\x02\x0a\xff\x00"
    assert render(data, rs, inline_arrays=True).splitlines() == [
        "센서 7 {", '  이름: "A"', "  기록 [21, 22]",
        "  기록 [",  # mixed labels stay one per line while labels are shown
        "    채널: 1", "    온도: 2", "  ]", "  구분선", "}"]
    assert render(data, rs, labels=False, inline_arrays=True).splitlines() == [
        "7 {", '  "A"', "  [21, 22]", "  [1, 2]", "  구분선", "}"]
    assert render(data, rs, labels=False).splitlines()[2:5] == ["  [", "    21", "    22"]


def test_cli_output_flags(tmp_path):
    rs_path = tmp_path / "r.json"
    kv_rules().save(rs_path)
    f = tmp_path / "x.bin"
    f.write_bytes(b"\x0a\x04\x01\x04\x02\x0a\x09k\x00\x04\x03")
    out = tmp_path / "o.txt"
    assert main(["decode", str(f), "--rules", str(rs_path), "--no-offsets", "--no-labels",
                 "--inline-arrays", "-o", str(out)]) == 0
    assert out.read_text(encoding="utf-8").splitlines() == ["[1, 2]", "k = 3"]
