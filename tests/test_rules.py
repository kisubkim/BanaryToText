import pytest

from binarytotext import heuristics, preprocess
from binarytotext.cli import main
from binarytotext.model import ERROR, GROUP, deepest_at
from binarytotext.render import RenderOptions, render_text
from binarytotext.rules import FieldSpec, Rule, RuleSet, decode_with_rules, example_ruleset, parse_tag


def text_of(data, rs, offsets=False):
    return render_text(decode_with_rules(data, rs), data, RenderOptions(offsets=offsets))


def test_example_from_request():
    data = b"\x09hello\x00\x04\x2a\x0a\x09ab\x00\x04\x07\x0a"
    out = text_of(data, example_ruleset())
    assert out == ('문자열: "hello"\n정수: 42\n배열 [\n  문자열: "ab"\n  정수: 7\n]\n')


def test_unknown_runs_and_modes():
    rs = example_ruleset()
    data = b"\xff\xee\x04\x01\xdd"
    out = text_of(data, rs)
    assert "?? 2 byte: FF EE" in out and "정수: 1" in out and "?? 1 byte: DD" in out
    rs.unknown = "stop"
    root = decode_with_rules(data, rs)
    assert any(n.kind == ERROR for n in root.walk())


def test_parse_tag_forms():
    assert parse_tag("0A") == [0x0A]
    assert parse_tag("5F 20") == [0x5F, 0x20]
    assert parse_tag("0x5F??") == [0x5F, None]
    assert parse_tag('"STR"') == [0x53, 0x54, 0x52]
    with pytest.raises(ValueError):
        parse_tag("ABC")
    with pytest.raises(ValueError):
        parse_tag("??")


def test_longest_tag_wins_and_wildcard():
    rs = RuleSet(rules=[
        Rule("01", "짧은", fields=[FieldSpec("uint", size=1)]),
        Rule("01 02", "긴", fields=[FieldSpec("uint", size=1)]),
        Rule("F0 ??", "와일드", fields=[]),
    ])
    out = text_of(b"\x01\x02\x07\x01\x09\xf0\x33", rs)
    assert out == "긴: 7\n짧은: 9\n와일드\n"


def test_fields_and_formats():
    rs = RuleSet(endian="big", rules=[
        Rule("10", "레코드", fields=[
            FieldSpec("uint", "id", size=2),
            FieldSpec("int", "온도", size=1),
            FieldSpec("uint", "상태", size=1, map={"1": "ON", "0": "OFF"}),
            FieldSpec("float", "값", size=4),
            FieldSpec("pstring", "이름", len_size="1", encoding="cp949"),
            FieldSpec("bcd", "날짜", size=3),
            FieldSpec("skip", size=2),
            FieldSpec("uint", "플래그", size=1, fmt="hex"),
        ]),
    ])
    import struct
    name = "홍길동".encode("cp949")
    data = (b"\x10\x01\x02\xfb\x01" + struct.pack(">f", 1.5) + bytes([len(name)]) + name
            + b"\x24\x10\x17" + b"\x00\x00" + b"\x0f")
    out = text_of(data, rs)
    assert out == 'id=258, 온도=-5, 상태=ON (1), 값=1.5, 이름="홍길동", 날짜=241017, 플래그=0x0F\n'.join(["레코드: ", ""])


def test_utf16_cstring_and_unixtime():
    rs = RuleSet(rules=[
        Rule("01", "u16", fields=[FieldSpec("cstring", encoding="utf-16-le", terminator="0000")]),
        Rule("02", "시각", fields=[FieldSpec("unixtime", size=4)]),
    ])
    data = b"\x01" + "가A".encode("utf-16-le") + b"\x00\x00" + b"\x02" + (0).to_bytes(4, "little")
    out = text_of(data, rs)
    assert 'u16: "가A"' in out and "1970-01-01T00:00:00Z" in out


def test_len_block_and_count_block():
    rs = RuleSet(rules=[
        Rule("B0", "블록", "len_block", [FieldSpec("uint", size=1)], bracket="{"),
        Rule("C0", "목록", "count_block", [FieldSpec("uint", size=1)]),
        Rule("04", "n", fields=[FieldSpec("uint", size=1)]),
    ])
    data = b"\xb0\x04\x04\x01\x04\x02" + b"\x04\x09" + b"\xc0\x02\x04\x05\x04\x06\x04\x07"
    out = text_of(data, rs)
    assert out == ("블록 (길이 4) {\n  n: 1\n  n: 2\n}\nn: 9\n"
                   "목록 (2개) [\n  n: 5\n  n: 6\n]\nn: 7\n")


def test_nested_count_blocks_close_together():
    rs = RuleSet(rules=[
        Rule("C0", "목록", "count_block", [FieldSpec("uint", size=1)]),
        Rule("04", "n", fields=[FieldSpec("uint", size=1)]),
    ])
    data = b"\xc0\x01\xc0\x01\x04\x05\x04\x06"
    out = text_of(data, rs)
    assert out == "목록 (1개) [\n  목록 (1개) [\n    n: 5\n  ]\n]\nn: 6\n"


def test_open_close_and_errors():
    rs = RuleSet(rules=[
        Rule("7B", "객체", "open", bracket="{"),
        Rule("7D", "객체 끝", "close"),
        Rule("01", "s", fields=[FieldSpec("cstring")]),
        Rule("FF", "끝", "end"),
    ])
    data = b"\x7d\x7b\x01ab\x00\x7b\x7d\x01broken"
    root = decode_with_rules(data, rs)
    out = render_text(root, data, RenderOptions(offsets=False))
    assert "닫을 그룹이 없습니다" in out
    assert "적용 실패" in out  # cstring without terminator
    assert "닫히지 않음" in out
    data2 = b"\x01a\x00\xff\x01b\x00"
    out2 = text_of(data2, rs)
    assert "해석 종료" in out2 and "해석 중단 이후" in out2


def _obj_rules(**kw):
    return RuleSet(rules=[
        Rule("0B", "객체", "open", bracket="{", end_tag=kw.get("end_tag", "00")),
        Rule("09", "문자열", fields=[FieldSpec("cstring")]),
        Rule("05", "정수", fields=[FieldSpec("int", size=4)]),
        Rule("0A", "배열", "toggle"),
    ])


def test_end_tag_closes_its_own_group():
    i = lambda v: v.to_bytes(4, "little")  # noqa: E731
    data = (b"\x0b\x09name\x00\x0b\x05" + i(1) + b"\x00"
            + b"\x0a\x05" + i(2) + b"\x05" + i(3) + b"\x0a\x00")
    assert text_of(data, _obj_rules()) == (
        '객체 {\n  문자열: "name"\n  객체 {\n    정수: 1\n  }\n'
        "  배열 [\n    정수: 2\n    정수: 3\n  ]\n}\n")


def test_end_tag_is_ignored_inside_other_groups_and_outside():
    # 00 inside the 0A array is just an unknown byte; it does not close 0B.
    data = b"\x0b\x0a\x09a\x00\x00\x09b\x00\x0a\x00"
    out = text_of(data, _obj_rules())
    assert out == '객체 {\n  배열 [\n    문자열: "a"\n    ?? 1 byte: 00  |.|\n    문자열: "b"\n  ]\n}\n'
    # 00 outside any 0B group (padding) is unknown, not an error.
    out = text_of(b"\x00\x00\x0b\x09a\x00\x00\x00", _obj_rules())
    assert "오류" not in out
    assert out == '?? 2 byte: 00 00  |..|\n객체 {\n  문자열: "a"\n}\n?? 1 byte: 00  |.|\n'


def test_end_tag_multibyte_beats_other_rules_and_unclosed_note():
    rs = _obj_rules(end_tag="09 FF")  # 09 is also the string rule's tag
    out = text_of(b"\x0b\x09a\x00\x09\xff\x0b\x09b\x00", rs)
    assert out == '객체 {\n  문자열: "a"\n}\n객체 {  (닫히지 않음)\n  문자열: "b"\n}\n'


def test_generic_close_skips_groups_with_end_tag():
    rs = _obj_rules()
    rs.rules += [Rule("7B", "블록", "open"), Rule("7D", "블록 끝", "close")]
    out = text_of(b"\x7b\x0b\x09a\x00\x00\x7d", rs)
    assert out == '블록 [\n  객체 {\n    문자열: "a"\n  }\n]\n'


def test_bad_end_tag_reported():
    root = decode_with_rules(b"\x0b", _obj_rules(end_tag="XYZ"))
    assert any(n.kind == ERROR and "객체" in n.label for n in root.walk())


def test_unknown_full_dump():
    rs = example_ruleset()
    rs.unknown = "full"
    data = bytes(range(0x30, 0x30 + 20)) + b"\x04\x01"
    root = decode_with_rules(data, rs)
    out = render_text(root, data, RenderOptions(offsets=False))
    assert out.splitlines()[:3] == [
        "?? 20 byte",
        "  30 31 32 33 34 35 36 37 38 39 3A 3B 3C 3D 3E 3F  |0123456789:;<=>?|",
        "  40 41 42 43                                      |@ABC|",
    ]
    from binarytotext.model import compute_stats
    st = compute_stats(root, len(data))
    assert st.unknown == 20 and st.explained == 2


def test_ruleset_without_end_tag_still_loads():
    rs = RuleSet.from_dict({"rules": [{"tag": "0B", "name": "x", "action": "open"}]})
    assert rs.rules[0].end_tag == ""


def _with_header_footer():
    rs = example_ruleset()
    rs.header = [FieldSpec("bytes", "매직", size=2), FieldSpec("uint", "버전", size=2),
                 FieldSpec("skip", size=1), FieldSpec("cstring", "제목")]
    rs.footer = [FieldSpec("uint", "CRC", size=4, endian="big", fmt="hex")]
    return rs


def test_header_and_footer_bound_the_body():
    data = b"\xfe\xca\x02\x00\xff" + b"T\x00" + b"\x09hi\x00\x04\x07" + b"\x09\x00\x00\x00"
    out = text_of(data, _with_header_footer())
    # The body's last 09 must not read into the footer, which holds 09 00 00 00.
    assert out == ('헤더 {\n  매직: FE CA\n  버전: 2\n  제목: "T"\n}\n'
                   '문자열: "hi"\n정수: 7\n꼬리 {\n  CRC: 0x09000000\n}\n')


def test_header_footer_errors():
    rs = _with_header_footer()
    out = text_of(b"\xfe\xca\x02", rs)  # header cut short, footer cannot fit
    assert "헤더 필드 2 읽기 실패" in out and "헤더+꼬리" in out
    rs.footer = [FieldSpec("cstring")]
    root = decode_with_rules(b"\xfe\xca\x02\x00\xffT\x00", rs)
    assert any(n.kind == ERROR and "고정 크기" in n.label for n in root.walk())


def test_header_only_and_json_roundtrip(tmp_path):
    rs = _with_header_footer()
    p = tmp_path / "h.json"
    rs.save(p)
    assert RuleSet.load(p) == rs
    rs.footer = []
    data = b"\xfe\xca\x01\x00\x00\x00\x04\x05"
    assert text_of(data, rs).endswith("정수: 5\n")


def test_ruleset_json_roundtrip(tmp_path):
    rs = example_ruleset()
    rs.rules[1].fields[0].map = {"1": "하나"}
    p = tmp_path / "r.json"
    rs.save(p)
    back = RuleSet.load(p)
    assert back == rs


def test_deepest_at():
    data = b"\x0a\x04\x05\x0a"
    root = decode_with_rules(data, example_ruleset())
    n = deepest_at(root, 2)
    assert n.label == "정수"
    assert deepest_at(root, 0).kind == GROUP


def test_suggest_rules_finds_string_tag():
    data = b"".join(b"\x09" + w + b"\x00\x04\x01" for w in (b"alpha", b"bravo", b"charlie", b"delta"))
    rep = heuristics.analyze(data)
    tags = [s.tag for s in rep.suggestions]
    assert "09" in tags
    s = rep.suggestions[tags.index("09")]
    rs = RuleSet(rules=[s.rule])
    out = text_of(data, rs)
    assert '"charlie"' in out
    assert heuristics.report_text(rep)


def test_suggest_length_prefixed():
    data = b"".join(b"\x07" + bytes([len(w)]) + w + b"\xee" for w in (b"alpha", b"bravo", b"charlie"))
    tags = {(s.tag, s.kind) for s in heuristics.suggest_rules(data, heuristics.extract_strings(data, 3))}
    assert ("07", "1 byte 길이 + 문자열") in tags


def test_record_periods():
    rec = b"\x01\x00ABCD\x00\x00\x00\x10"
    data = b"".join(rec[:-1] + bytes([i]) for i in range(200))
    assert heuristics.record_periods(data)[0][0] == len(rec)


def test_rank_formats_prefers_true_format():
    data = b"\x82\xa7compact\xc3\xa6schema\x00" * 3
    ranked = heuristics.rank_formats(data)
    assert ranked[0].key == "msgpack"


def test_rank_formats_does_not_trust_noise():
    # Custom tagged data: every byte is a valid msgpack fixint, which must not win big.
    recs = [b"\x0a\x09" + n + b"\x00\x04" + bytes([a]) + b"\x09Seoul\x00\x0a"
            for n, a in ((b"Kim", 31), (b"Lee", 27), (b"Park", 45))]
    ranked = heuristics.rank_formats(b"\xfe\xca\x01\x00" + b"".join(recs))
    assert ranked[0].score < 0.3


def test_preprocess():
    import zlib
    raw = b"hello world" * 10
    z = zlib.compress(raw)
    assert "zlib" in preprocess.detect(z)
    assert preprocess.apply(z, "zlib") == raw
    assert preprocess.apply(preprocess.apply(raw, "xor", "5A A5"), "xor", "5AA5") == raw
    assert preprocess.apply(b"0x01, 0x02 ff", "hextext") == b"\x01\x02\xff"
    assert preprocess.apply(raw, "skip", "6") == raw[6:]


def test_cli_decode(tmp_path, capsys):
    f = tmp_path / "a.bin"
    f.write_bytes(b"\x09hi\x00\x04\x05")
    r = tmp_path / "r.json"
    example_ruleset().save(r)
    out = tmp_path / "o.txt"
    assert main(["decode", str(f), "--rules", str(r), "-o", str(out), "--no-offsets"]) == 0
    assert out.read_text(encoding="utf-8") == '문자열: "hi"\n정수: 5\n'
    assert main(["decode", str(f), "--format", "hexdump"]) == 0
    assert main(["analyze", str(f), "--no-rank"]) == 0
    assert main(["formats"]) == 0


def test_float_text_shortest_round_trip():
    import struct

    from binarytotext.reader import float_text
    assert float_text(struct.pack("<f", 36.6), "little") == "36.6"
    assert float_text(struct.pack(">f", 0.1)) == "0.1"
    assert float_text(struct.pack(">e", 1.5)) == "1.5"
    assert float_text(struct.pack(">d", 0.1)) == "0.1"
    assert float_text(struct.pack(">f", float("inf"))) == "inf"
    x = struct.unpack(">f", bytes.fromhex("3F800001"))[0]  # needs all 9 digits
    assert struct.pack(">f", float(float_text(bytes.fromhex("3F800001")))) == struct.pack(">f", x)
