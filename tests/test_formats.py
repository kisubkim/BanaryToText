import struct

import pytest

from binarytotext.formats import REGISTRY
from binarytotext.model import ERROR, compute_stats
from binarytotext.render import render_text


def run(key, data, **opts):
    root = REGISTRY[key].decode(data, opts)
    text = render_text(root, data)
    return root, text


def clean(root, data):
    st = compute_stats(root, len(data))
    assert st.errors == 0, render_text(root, data)
    assert st.unknown == 0, render_text(root, data)
    assert st.coverage == 1.0, render_text(root, data)


# ---- CBOR: RFC 8949 Appendix A ----
@pytest.mark.parametrize("hexstr, expect", [
    ("00", ": 0"), ("1903e8", ": 1000"), ("3863", ": -100"), ("f93c00", ": 1.0"),
    ("fb3ff199999999999a", ": 1.1"), ("6449455446", '"IETF"'), ("f5", "true"), ("f6", "null"),
    ("c11a514b67b0", "tag 1 epoch"), ("4401020304", "01 02 03 04"),
])
def test_cbor_scalars(hexstr, expect):
    data = bytes.fromhex(hexstr)
    root, text = run("cbor", data)
    clean(root, data)
    assert expect in text


def test_cbor_containers():
    data = bytes.fromhex("a26161016162820203")
    root, text = run("cbor", data)
    clean(root, data)
    assert "a: 1" in text and "b (2개) [" in text and "    3\n" in text  # array items: values only


def test_cbor_indefinite():
    data = bytes.fromhex("9f018202039f0405ffff") + bytes.fromhex("5f42010243030405ff")
    root, text = run("cbor", data)
    clean(root, data)
    assert "부정 길이" in text and "01 02 03 04 05" in text


# ---- MessagePack ----
def test_msgpack_map():
    data = b"\x82\xa7compact\xc3\xa6schema\x00" + b"\x93\x01\xcd\x01\x00\xd0\x80"
    root, text = run("msgpack", data)
    clean(root, data)
    assert "compact: true" in text and "schema: 0" in text
    assert "  256,  (uint16)" in text and "  -128  (int8)" in text


def test_msgpack_timestamp():
    data = b"\xd6\xff" + (1_600_000_000).to_bytes(4, "big")
    root, text = run("msgpack", data)
    assert "2020-09-13T12:26:40Z" in text


# ---- BSON ----
def test_bson_hello():
    data = b"\x16\x00\x00\x00\x02hello\x00\x06\x00\x00\x00world\x00\x00"
    root, text = run("bson", data)
    clean(root, data)
    assert 'hello: "world"' in text


def test_bson_nested():
    inner = b"\x10x\x00" + struct.pack("<i", 7)
    inner_doc = struct.pack("<i", 4 + len(inner) + 1) + inner + b"\x00"
    body = b"\x03sub\x00" + inner_doc + b"\x08ok\x00\x01"
    data = struct.pack("<i", 4 + len(body) + 1) + body + b"\x00"
    root, text = run("bson", data)
    clean(root, data)
    assert "x: 7" in text and "ok: true" in text


# ---- Protobuf ----
def test_protobuf():
    data = bytes.fromhex("089601") + b"\x12\x07testing" + bytes.fromhex("1a03089601")
    root, text = run("protobuf", data)
    clean(root, data)
    assert "#1: 150" in text and '#2: "testing"' in text and "#3 (메시지 3 byte)" in text


def test_protobuf_rejects_field_zero():
    data = b"\x00\x01"
    root, _ = run("protobuf", data)
    assert any(n.kind == ERROR for n in root.walk())


# ---- BER / ASN.1 / EMV ----
def test_ber_sequence_and_oid():
    data = bytes.fromhex("300b020105 0101ff 0603550403".replace(" ", ""))
    root, text = run("ber", data)
    clean(root, data)
    assert "INTEGER: 5" in text and "BOOLEAN: TRUE" in text and "2.5.4.3  (commonName)" in text


def test_ber_indefinite():
    data = bytes.fromhex("3080020101 0000".replace(" ", ""))
    root, text = run("ber", data)
    clean(root, data)
    assert "부정 길이" in text


def test_ber_multibyte_tag_and_long_length():
    val = b"x" * 200
    data = bytes.fromhex("5F20") + b"\x81" + bytes([200]) + val
    root, text = run("emv", data)
    clean(root, data)
    assert "5F20 Cardholder Name" in text


def test_emv_fci():
    body = bytes.fromhex("840E") + b"1PAY.SYS.DDF01" + bytes.fromhex("A508880102") + bytes.fromhex("5F2D02") + b"en"
    data = bytes([0x6F, len(body)]) + body
    root, text = run("emv", data)
    clean(root, data)
    assert "6F FCI Template" in text and '"1PAY.SYS.DDF01"' in text and '"en"' in text


def test_pem():
    import base64
    der = bytes.fromhex("3003020107")
    data = b"-----BEGIN TEST-----\n" + base64.b64encode(der) + b"\n-----END TEST-----\n"
    _, text = run("ber", data)
    assert "INTEGER: 7" in text


# ---- Generic TLV ----
def test_generic_tlv():
    data = b"\x01\x03abc\x02\x01\x05"
    root, text = run("tlv", data, nested=False)
    clean(root, data)
    assert 'abc' in text and "(= 5)" in text


def test_generic_tlv_nested_le16():
    inner = b"\x01\x00\x02\x00hi"
    data = b"\x07\x00" + len(inner).to_bytes(2, "little") + inner
    root, text = run("tlv", data, tag_size=2, len_size=2, endian="little")
    clean(root, data)
    assert "태그 0x0007" in text and "태그 0x0001" in text and '"hi"' in text


# ---- UBJSON / Bencode / Thrift ----
def test_ubjson():
    data = b"{i\x01ai\x01i\x01b[$i#i\x03\x01\x02\x03}"
    root, text = run("ubjson", data)
    clean(root, data)
    assert "a: 1" in text and "  3  (i)" in text


def test_bencode():
    data = b"d3:foo3:bar4:listli1ei-2ee3:numi42ee"
    root, text = run("bencode", data)
    clean(root, data)
    assert 'foo: "bar"' in text and "num: 42" in text and "  -2  (int)" in text


def test_thrift_binary():
    data = bytes.fromhex("0800010000002a") + bytes.fromhex("0b0002") + b"\x00\x00\x00\x02hi" + b"\x00"
    root, text = run("thrift_binary", data)
    clean(root, data)
    assert "#1: 42" in text and '#2: "hi"' in text


def test_thrift_compact():
    data = bytes.fromhex("1554") + bytes.fromhex("1802") + b"hi" + bytes.fromhex("1100")
    root, text = run("thrift_compact", data)
    clean(root, data)
    assert "#1: 42" in text and '#2: "hi"' in text and "#3: true" in text


def test_all_decoders_survive_random_bytes():
    import random
    rnd = random.Random(1)
    for _ in range(30):
        data = bytes(rnd.randrange(256) for _ in range(rnd.randrange(1, 300)))
        for dec in REGISTRY.values():
            root = dec.decode(data)
            render_text(root, data)
            st = compute_stats(root, len(data))
            assert st.total == len(data)


def test_strings_decoder_finds_korean():
    data = b"\x01\x02" + "안녕하세요".encode("cp949") + b"\x00\x05" + "hello".encode("utf-16-le")
    _, text = run("strings", data, min_len=3)
    assert "안녕하세요" in text and "hello" in text
