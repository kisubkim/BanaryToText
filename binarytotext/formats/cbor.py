"""CBOR (RFC 8949)."""

from __future__ import annotations

from ..model import GROUP, VALUE, Node
from ..reader import DecodeError, Reader, bytes_value, quote, unpack_float
from .base import Decoder, Option, map_entry, new_root, register, run_stream

TAGS = {0: "날짜/시각 문자열", 1: "epoch 시각", 2: "양의 bignum", 3: "음의 bignum", 4: "decimal fraction",
        5: "bigfloat", 21: "base64url 예정", 22: "base64 예정", 23: "base16 예정", 24: "내장 CBOR",
        32: "URI", 33: "base64url", 34: "base64", 36: "MIME", 37: "UUID", 55799: "self-describe CBOR"}
SIMPLE = {20: "false", 21: "true", 22: "null", 23: "undefined"}


class _Break(Exception):
    pass


def decode_cbor(data: bytes, opts: dict) -> Node:
    max_depth = opts["max_depth"]

    def arg(r: Reader, info: int, pos: int) -> int | None:
        if info < 24:
            return info
        if info in (24, 25, 26, 27):
            return r.uint(1 << (info - 24))
        if info == 31:
            return None
        raise DecodeError(f"예약된 additional info {info}", pos)

    def obj(pos: int, parent: Node, label: str, depth: int = 0) -> int:
        if depth > max_depth:
            raise DecodeError("최대 깊이 초과", pos)
        r = Reader(data, pos)
        ib = r.u8()
        major, info = ib >> 5, ib & 0x1F

        def val(text: str, note: str = "") -> int:
            parent.add(Node(pos, r.pos - pos, VALUE, label, text, note=note))
            return r.pos

        if major == 7:
            if info == 31:
                raise _Break(r.pos)
            if info < 24:
                return val(SIMPLE.get(info, f"simple({info})"))
            if info == 24:
                v = r.u8()
                return val(SIMPLE.get(v, f"simple({v})"))
            if info in (25, 26, 27):
                return val(repr(unpack_float(r.read(1 << (info - 24)), "big")),
                           {25: "half", 26: "float", 27: "double"}[info])
            raise DecodeError(f"예약된 simple/float 형식 {info}", pos)
        n = arg(r, info, pos)
        if n is None and major in (0, 1, 6):
            raise DecodeError(f"major type {major} 에는 부정 길이를 쓸 수 없습니다", pos)
        if major == 0:
            return val(str(n))
        if major == 1:
            return val(str(-1 - n))
        if major in (2, 3):
            if n is None:  # indefinite chunks
                chunks = b""
                while True:
                    if r.peek(1) == b"\xff":
                        r.u8()
                        break
                    cb = r.u8()
                    if cb >> 5 != major or cb & 0x1F == 31:
                        raise DecodeError("부정 길이 문자열 조각 형식 오류", r.pos - 1)
                    chunks += r.read(arg(r, cb & 0x1F, r.pos - 1))
                b = chunks
            else:
                b = r.read(n)
            if major == 3:
                return val(quote(b.decode("utf-8", "replace")), "text")
            return val(bytes_value(b, ()), "bytes")
        if major in (4, 5):
            is_map = major == 5
            g = parent.add(Node(pos, 0, GROUP, label, "" if n is None else f"({n}개)",
                                close="}" if is_map else "]"))
            p = r.pos
            i = 0
            while n is None or i < n:
                if n is None and data[p:p + 1] == b"\xff":
                    p += 1
                    g.note = "부정 길이"
                    break
                if is_map:
                    p = map_entry(g, lambda q, par, lab: obj(q, par, lab, depth + 1), p, i)
                else:
                    p = obj(p, g, f"[{i}]", depth + 1)
                i += 1
            g.length = p - pos
            return p
        if major == 6:
            name = TAGS.get(n, "")
            g = parent.add(Node(pos, 0, GROUP, label, f"tag {n}" + (f" {name}" if name else ""), close=")"))
            p = obj(r.pos, g, "값", depth + 1)
            g.length = p - pos
            return p
        raise DecodeError("알 수 없는 major type", pos)

    root = new_root("CBOR", data)
    idx = [0]

    def one(pos, parent):
        idx[0] += 1
        try:
            return obj(pos, parent, f"항목 {idx[0]}")
        except _Break:
            raise DecodeError("예상치 못한 break(0xFF)", pos)
    return run_stream(root, data, one)


register(Decoder(
    "cbor", "CBOR (RFC 8949)", "직렬화",
    "major type 0~7, 부정 길이, 태그(시각/bignum/URI 등), half/float/double, simple 값.",
    decode_cbor,
    [Option("max_depth", "최대 깊이", "int", 100, minimum=1, maximum=1000)],
    single_root=True,
))
