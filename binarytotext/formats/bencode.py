"""Bencode (BitTorrent)."""

from __future__ import annotations

from ..model import GROUP, VALUE, Node
from ..reader import DecodeError, Reader, bytes_value
from .base import Decoder, Option, new_root, register, run_stream


def decode_bencode(data: bytes, opts: dict) -> Node:
    max_depth = opts["max_depth"]

    def raw_string(r: Reader) -> bytes:
        start = r.pos
        idx = data.find(b":", r.pos, min(len(data), r.pos + 20))
        digits = data[r.pos:idx] if idx > r.pos else b""
        if not digits.isdigit():
            raise DecodeError("문자열 길이(숫자:)가 필요합니다", start)
        r.pos = idx + 1
        return r.read(int(digits))

    def obj(pos: int, parent: Node, label: str, depth: int = 0) -> int:
        if depth > max_depth:
            raise DecodeError("최대 깊이 초과", pos)
        r = Reader(data, pos)
        c = r.peek(1)
        if c == b"i":
            end = data.find(b"e", pos + 1, pos + 40)
            body = data[pos + 1:end] if end > 0 else b""
            if not body or not body.lstrip(b"-").isdigit():
                raise DecodeError("정수 형식 오류", pos)
            parent.add(Node(pos, end + 1 - pos, VALUE, label, body.decode(), note="int"))
            return end + 1
        if c in (b"l", b"d"):
            is_map = c == b"d"
            g = parent.add(Node(pos, 0, GROUP, label, close="}" if is_map else "]"))
            p, i = pos + 1, 0
            while True:
                if p >= len(data):
                    raise DecodeError("리스트/딕셔너리가 e 로 닫히지 않았습니다", p)
                if data[p:p + 1] == b"e":
                    p += 1
                    break
                if is_map:
                    kr = Reader(data, p)
                    key = raw_string(kr).decode("utf-8", "replace")
                    kstart, p = p, obj(kr.pos, g, key, depth + 1)
                    g.children[-1].offset, g.children[-1].length = kstart, p - kstart
                else:
                    p = obj(p, g, f"[{i}]", depth + 1)
                i += 1
            g.length = p - pos
            return p
        b = raw_string(r)
        parent.add(Node(pos, r.pos - pos, VALUE, label, bytes_value(b), note="bytes"))
        return r.pos

    root = new_root("Bencode", data)
    idx = [0]

    def one(pos, parent):
        idx[0] += 1
        return obj(pos, parent, f"항목 {idx[0]}")
    return run_stream(root, data, one)


register(Decoder(
    "bencode", "Bencode (.torrent)", "직렬화",
    "i…e 정수, 길이:문자열, l…e 리스트, d…e 딕셔너리.",
    decode_bencode,
    [Option("max_depth", "최대 깊이", "int", 100, minimum=1, maximum=1000)],
    single_root=True,
))
