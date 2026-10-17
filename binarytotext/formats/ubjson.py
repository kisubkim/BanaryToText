"""UBJSON (Universal Binary JSON, draft 12, big-endian)."""

from __future__ import annotations

from ..model import GROUP, VALUE, Node
from ..reader import DecodeError, Reader, quote
from .base import Decoder, Option, new_root, register, run_stream

INTS = {b"i": (1, True), b"U": (1, False), b"I": (2, True), b"l": (4, True), b"L": (8, True)}


def decode_ubjson(data: bytes, opts: dict) -> Node:
    max_depth = opts["max_depth"]

    def int_value(r: Reader, marker: bytes) -> int:
        if marker not in INTS:
            raise DecodeError(f"정수 마커가 아닙니다: {marker!r}", r.pos - 1)
        size, signed = INTS[marker]
        return r.sint(size) if signed else r.uint(size)

    def length(r: Reader) -> int:
        n = int_value(r, r.read(1))
        if n < 0:
            raise DecodeError("음수 길이", r.pos)
        return n

    def obj(pos: int, parent: Node, label: str, depth: int = 0, marker: bytes | None = None) -> int:
        if depth > max_depth:
            raise DecodeError("최대 깊이 초과", pos)
        r = Reader(data, pos)
        m = marker if marker is not None else r.read(1)
        while marker is None and m == b"N":  # no-op
            m = r.read(1)

        def val(text: str, note: str = "") -> int:
            parent.add(Node(pos, r.pos - pos, VALUE, label, text, note=note))
            return r.pos

        if m == b"Z":
            return val("null")
        if m == b"T":
            return val("true")
        if m == b"F":
            return val("false")
        if m in INTS:
            return val(str(int_value(r, m)), m.decode())
        if m == b"d":
            return val(r.float_text(4), "float32")
        if m == b"D":
            return val(repr(r.float(8)), "float64")
        if m == b"H":
            return val(r.read(length(r)).decode("ascii", "replace"), "high-precision")
        if m == b"C":
            return val(quote(r.read(1).decode("latin-1")), "char")
        if m == b"S":
            return val(quote(r.read(length(r)).decode("utf-8", "replace")), "string")
        if m in (b"[", b"{"):
            is_map = m == b"{"
            g = parent.add(Node(pos, 0, GROUP, label, close="}" if is_map else "]"))
            etype = count = None
            if r.peek(1) == b"$":
                r.u8()
                etype = r.read(1)
                if r.peek(1) != b"#":
                    raise DecodeError("$ 다음에 # 가 필요합니다", r.pos)
            if r.peek(1) == b"#":
                r.u8()
                count = length(r)
                g.value = f"({count}개" + (f", 타입 {etype.decode()})" if etype else ")")
            p, i = r.pos, 0
            while count is None or i < count:
                if count is None:
                    while data[p:p + 1] == b"N":
                        p += 1
                    if data[p:p + 1] == (b"}" if is_map else b"]"):
                        p += 1
                        break
                    if p >= len(data):
                        raise DecodeError("컨테이너가 닫히지 않았습니다", p)
                if is_map:
                    kr = Reader(data, p)
                    key = kr.read(length(kr)).decode("utf-8", "replace")
                    kstart, p = p, obj(kr.pos, g, key, depth + 1, etype)
                    g.children[-1].offset, g.children[-1].length = kstart, p - kstart
                else:
                    p = obj(p, g, f"[{i}]", depth + 1, etype)
                i += 1
            g.length = p - pos
            return p
        raise DecodeError(f"알 수 없는 UBJSON 마커 {m!r}", pos)

    root = new_root("UBJSON", data)
    idx = [0]

    def one(pos, parent):
        idx[0] += 1
        return obj(pos, parent, f"항목 {idx[0]}")
    return run_stream(root, data, one)


register(Decoder(
    "ubjson", "UBJSON", "직렬화",
    "Universal Binary JSON (빅 엔디언). 최적화 컨테이너($ 타입, # 개수) 지원.",
    decode_ubjson,
    [Option("max_depth", "최대 깊이", "int", 100, minimum=1, maximum=1000)],
    single_root=True,
))
