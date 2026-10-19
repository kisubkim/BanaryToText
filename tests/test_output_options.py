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
    assert text.splitlines()[:3] == ["volume = 30", 'mode = "auto"', "list = ["]
    assert render(data, kv_rules(), quotes=False).splitlines()[1] == "mode = auto"
    root = decode_with_rules(data, kv_rules())
    first = root.children[0]
    assert (first.offset, first.length, first.key) == (0, 10, True)  # span covers key and value
    assert '"key": true' in render_json(root)
    assert '"value": "\\"auto\\""' in render_json(root)  # JSON keeps the quoted string


def test_quotes_option():
    rs = kv_rules()
    rs.rules.append(Rule("0D", "둘", fields=[FieldSpec("cstring"), FieldSpec("cstring")]))
    data = (b'\x09a\x00\x09say "hi"\x00' + b"\x09b\x00\x0dx\x00y\x00"
            + b"\x09c\x00\x0a\x09p\x00\x09q\x00\x0a" + b"\x09t\x00\x09l1\nl2\x00")
    on = render(data, rs, layout="arrays").splitlines()
    # Inside [ ] the key rule reads plain values: no "p = q" in an array.
    assert on == ['a = "say \\"hi\\""', 'b = "x", "y"', 'c = ["p", "q"]', 't = "l1\\nl2"']
    off = render(data, rs, layout="arrays", quotes=False).splitlines()
    # Without quotes, text with blanks, separators, quotes or escapes keeps its quotes.
    assert off == ['a = "say \\"hi\\""', "b = x, y", "c = [p, q]", 't = "l1\\nl2"']
    # Plain strings and values-only output follow the option too.
    plain = kv_rules()
    plain.rules[0].key = False
    d2 = b"\x0a\x04\x01\x04\x02\x09abc\x00\x09\x00\x09cde\x00\x0a"
    assert render(d2, plain, labels=False, layout="inline", quotes=False).splitlines() == ['[1, 2, abc, "", cde]']
    assert render(d2, plain, labels=False, layout="inline").splitlines() == ['[1, 2, "abc", "", "cde"]']
    assert render(b"\x09\x00", plain, quotes=False).splitlines() == ['문자열: ""']


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
    # Array items are values only, so every [ ] of plain values fits on one line.
    assert render(data, rs, layout="arrays").splitlines() == [
        "센서 7 {", '  이름: "A"', "  기록 [21, 22]", "  기록 [1, 2]", "  구분선", "}"]
    assert render(data, rs, labels=False, layout="arrays").splitlines() == [
        "7 {", '  "A"', "  [21, 22]", "  [1, 2]", "  구분선", "}"]
    assert render(data, rs, labels=False).splitlines()[2:5] == ["  [", "    21,", "    22"]


def test_cli_output_flags(tmp_path):
    rs_path = tmp_path / "r.json"
    kv_rules().save(rs_path)
    f = tmp_path / "x.bin"
    f.write_bytes(b"\x0a\x04\x01\x04\x02\x0a\x09k\x00\x04\x03")
    out = tmp_path / "o.txt"
    assert main(["decode", str(f), "--rules", str(rs_path), "--no-offsets", "--no-labels",
                 "--layout", "arrays", "-o", str(out)]) == 0
    assert out.read_text(encoding="utf-8").splitlines() == ["[1, 2]", "k = 3"]


def sensor_like():
    rs = RuleSet(rules=[
        Rule("0B", "센서", "open", bracket="{", end_tag="00"),
        Rule("09", "이름", fields=[FieldSpec("cstring")]),
        Rule("04", "채널", fields=[FieldSpec("uint", size=1)]),
        Rule("0A", "기록", "toggle"),
    ])
    data = b"\x0b\x09A\x00\x04\x01\x0a\x04\x15\x04\x16\x0a\x00\x0b\x09B\x00\x00"
    return data, rs


def test_commas_between_items_not_after_last():
    data, rs = sensor_like()
    assert render(data, rs, commas=True).splitlines() == [
        "센서 {", '  이름: "A",', "  채널: 1,", "  기록 [", "    21,", "    22", "  ]", "}",
        "센서 {", '  이름: "B"', "}"]  # top level has no commas: only items inside a group
    # Arrays always separate their items, even with the comma option off; objects follow the option.
    assert render(data, rs).splitlines()[:7] == [
        "센서 {", '  이름: "A"', "  채널: 1", "  기록 [", "    21,", "    22", "  ]"]


def test_inline_groups_and_rule_override():
    data, rs = sensor_like()
    assert render(data, rs, layout="inline").splitlines() == [
        '센서 {이름: "A", 채널: 1, 기록 [21, 22]}', '센서 {이름: "B"}']
    assert render(data, rs, layout="inline", labels=False).splitlines() == ['{"A", 1, [21, 22]}', '{"B"}']
    rs.rules[3].layout = "inline"  # only the array on one line
    assert render(data, rs, commas=True).splitlines()[:5] == [
        "센서 {", '  이름: "A",', "  채널: 1,", "  기록 [21, 22]", "}"]
    rs.rules[0].layout = "lines"
    rs.rules[3].layout = ""
    assert render(data, rs, layout="inline").splitlines()[:4] == [
        "센서 {", '  이름: "A"', "  채널: 1", "  기록 [21, 22]"]


def test_commas_with_keys():
    data = b"\x0b\x09ip\x00\x09x\x00\x09port\x00\x04\x50\x00"
    assert render(data, kv_rules(), commas=True).splitlines() == ["객체 {", '  ip = "x",', "  port = 80", "}"]
    assert render(data, kv_rules(), layout="inline", quotes=False).splitlines() == ["객체 {ip = x, port = 80}"]


def test_cli_layout_and_commas(tmp_path):
    data, rs = sensor_like()
    rs_path, f, out = tmp_path / "r.json", tmp_path / "x.bin", tmp_path / "o.txt"
    rs.save(rs_path)
    f.write_bytes(data)
    assert main(["decode", str(f), "--rules", str(rs_path), "--no-offsets", "--layout", "inline",
                 "-o", str(out)]) == 0
    assert out.read_text(encoding="utf-8").splitlines()[0] == '센서 {이름: "A", 채널: 1, 기록 [21, 22]}'
    assert main(["decode", str(f), "--rules", str(rs_path), "--no-offsets", "--commas", "-o", str(out)]) == 0
    assert '  이름: "A",' in out.read_text(encoding="utf-8").splitlines()


def test_empty_strings_stay_visible():
    # 09 00 = empty string: read without shifting the following bytes.
    plain = kv_rules()
    plain.rules[0].key = False
    assert render(b"\x09\x00\x04\x01", plain).splitlines() == ['문자열: ""', "정수: 1"]
    assert render(b"\x0a\x09\x00\x09a\x00\x0a", plain, labels=False, layout="inline").splitlines() == ['["", "a"]']
    # Empty value of a key, and the next key still pairs normally.
    data = b"\x09mode\x00\x09\x00\x09ip\x00\x09x\x00"
    assert render(data, kv_rules()).splitlines() == ['mode = ""', 'ip = "x"']
    assert render(data, kv_rules(), quotes=False).splitlines() == ['mode = ""', "ip = x"]  # an empty string stays ""
    # Empty key: shown as "" rather than the rule name, with or without quotes.
    assert render(b"\x09\x00\x04\x1e", kv_rules()).splitlines() == ['"" = 30']
    assert render(b"\x09\x00\x04\x1e", kv_rules(), quotes=False).splitlines() == ['"" = 30']


def test_cli_no_quotes(tmp_path):
    rs_path, f, out = tmp_path / "r.json", tmp_path / "x.bin", tmp_path / "o.txt"
    kv_rules().save(rs_path)
    f.write_bytes(b"\x09mode\x00\x09auto\x00")
    assert main(["decode", str(f), "--rules", str(rs_path), "--no-offsets", "--no-quotes", "-o", str(out)]) == 0
    assert out.read_text(encoding="utf-8").splitlines() == ["mode = auto"]



def test_no_quotes_keeps_quotes_where_needed():
    plain = kv_rules()
    plain.rules[0].key = False
    plain.encoding = "utf-8"
    data = b"".join(b"\x09" + s.encode("utf-8") + b"\x00" for s in
                    ["abc", "센서-A_1.5", "a b", "x,y", "k=v", "t:1", "[a]", "it's", ""])
    out = render(data, plain, labels=False, quotes=False).splitlines()
    assert out == ["abc", "센서-A_1.5", '"a b"', '"x,y"', '"k=v"', '"t:1"', '"[a]"', "\"it's\"", '""']
    # Keys follow the same rule.
    kv = b"\x09my key\x00\x04\x01\x09ok\x00\x04\x02"
    assert render(kv, kv_rules(), quotes=False).splitlines() == ['"my key" = 1', "ok = 2"]


def test_arrays_hold_values_only():
    # A key rule inside [ ] reads plain values; the array never shows names, and always has commas.
    data = b"\x09list\x00\x0a\x09a\x00\x04\x01\x09b\x00\x04\x02\x0a\x09n\x00\x04\x03"
    assert render(data, kv_rules()).splitlines() == [
        "list = [", '  "a",', "  1,", '  "b",', "  2", "]", "n = 3"]
    assert render(data, kv_rules(), layout="inline", quotes=False).splitlines() == ["list = [a, 1, b, 2]", "n = 3"]
    # An object inside an array: no name of its own, its fields keep theirs.
    obj = b"\x0a\x0b\x09k\x00\x04\x05\x00\x0a"
    assert render(obj, kv_rules()).splitlines() == ["배열 [", "  {", "    k = 5", "  }", "]"]


def header_rules() -> RuleSet:
    return RuleSet(rules=[Rule("04", "n", fields=[FieldSpec("uint", size=1)])],
                   header=[FieldSpec("fstring", "매직", size=2)], footer=[FieldSpec("uint", "crc", size=1)])


def test_header_footer_option(tmp_path):
    data = b"MG\x04\x01\x04\x02\x99"
    rs = header_rules()
    full = render(data, rs).splitlines()
    assert full[0] == "헤더 {" and full[-3] == "꼬리 {"
    assert render(data, rs, header_footer=False).splitlines() == ["n: 1", "n: 2"]
    root = decode_with_rules(data, rs)
    assert '"매직"' not in render_json(root, RenderOptions(header_footer=False))
    assert '"매직"' in render_json(root)
    f, rp, out = tmp_path / "x.bin", tmp_path / "r.json", tmp_path / "o.txt"
    f.write_bytes(data)
    rs.save(rp)
    assert main(["decode", str(f), "--rules", str(rp), "--no-offsets", "--no-header-footer", "-o", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == "n: 1\nn: 2\n"
    assert main(["decode", str(f), "--rules", str(rp), "--json", "--no-header-footer", "-o", str(out)]) == 0
    assert "crc" not in out.read_text(encoding="utf-8")



def test_group_items_choice_per_rule():
    # Same data, three choices on the 0A array rule: by bracket ([ ] values), named, values.
    data = b"\x0a\x09a\x00\x04\x01\x09b\x00\x04\x02\x0a"
    rs = kv_rules()
    assert render(data, rs).splitlines() == ["배열 [", '  "a",', "  1,", '  "b",', "  2", "]"]
    rs.rules[2].items = "named"  # keys pair up even inside [ ]
    assert render(data, rs).splitlines() == ["배열 [", "  a = 1", "  b = 2", "]"]
    assert render(data, rs, commas=True, layout="inline").splitlines() == ["배열 [a = 1, b = 2]"]
    # An object set to values only: no keys, no names, commas.
    obj = b"\x0b\x09k\x00\x04\x05\x04\x06\x00"
    rs = kv_rules()
    assert render(obj, rs).splitlines() == ["객체 {", "  k = 5", "  정수: 6", "}"]
    rs.rules[3].items = "values"
    assert render(obj, rs).splitlines() == ["객체 {", '  "k",', "  5,", "  6", "}"]
    assert rs.rules[3].fields_summary().endswith("[값만]")
    assert RuleSet.from_dict(__import__("json").loads(rs.to_json())).rules[3].items == "values"
