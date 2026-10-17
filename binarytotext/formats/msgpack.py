"""MessagePack (https://github.com/msgpack/msgpack/blob/master/spec.md)."""

from __future__ import annotations

from ..model import GROUP, VALUE, Node
from ..reader import DecodeError, Reader, bytes_value, quote, unix_time
from .base import Decoder, Option, map_entry, new_root, register, run_stream


def decode_msgpack(data: bytes, opts: dict) -> Node:
    max_depth = opts["max_depth"]

    def obj(pos: int, parent: Node, label: str, depth: int = 0) -> int:
        if depth > max_depth:
            raise DecodeError("최대 깊이 초과", pos)
        r = Reader(data, pos)
        b = r.u8()

        def val(text: str, note: str = "") -> int:
            parent.add(Node(pos, r.pos - pos, VALUE, label, text, note=note))
            return r.pos

        if b <= 0x7F:
            return val(str(b), "fixint")
        if b >= 0xE0:
            return val(str(b - 256), "fixint")
        if 0x80 <= b <= 0x8F:
            return container(r, pos, parent, label, b & 0x0F, True, depth)
        if 0x90 <= b <= 0x9F:
            return container(r, pos, parent, label, b & 0x0F, False, depth)
        if 0xA0 <= b <= 0xBF:
            return val(quote(r.read(b & 0x1F).decode("utf-8", "replace")), "str")
        if b == 0xC0:
            return val("nil")
        if b == 0xC2:
            return val("false")
        if b == 0xC3:
            return val("true")
        if b in (0xC4, 0xC5, 0xC6):
            n = r.uint(1 << (b - 0xC4))
            return val(bytes_value(r.read(n), ()), "bin")
        if b in (0xC7, 0xC8, 0xC9):
            n = r.uint(1 << (b - 0xC7))
            return ext(r, pos, parent, label, n)
        if b == 0xCA:
            return val(repr(r.float(4)), "float32")
        if b == 0xCB:
            return val(repr(r.float(8)), "float64")
        if 0xCC <= b <= 0xCF:
            return val(str(r.uint(1 << (b - 0xCC))), f"uint{8 << (b - 0xCC)}")
        if 0xD0 <= b <= 0xD3:
            return val(str(r.sint(1 << (b - 0xD0))), f"int{8 << (b - 0xD0)}")
        if 0xD4 <= b <= 0xD8:
            return ext(r, pos, parent, label, 1 << (b - 0xD4))
        if 0xD9 <= b <= 0xDB:
            n = r.uint(1 << (b - 0xD9))
            return val(quote(r.read(n).decode("utf-8", "replace")), "str")
        if b in (0xDC, 0xDD):
            return container(r, pos, parent, label, r.uint(2 if b == 0xDC else 4), False, depth)
        if b in (0xDE, 0xDF):
            return container(r, pos, parent, label, r.uint(2 if b == 0xDE else 4), True, depth)
        raise DecodeError(f"사용되지 않는 MessagePack 바이트 0x{b:02X}", pos)

    def ext(r: Reader, pos: int, parent: Node, label: str, n: int) -> int:
        t = r.sint(1)
        payload = r.read(n)
        text = f"ext type {t}: {bytes_value(payload, ())}"
        if t == -1:  # timestamp
            if n == 4:
                text = unix_time(int.from_bytes(payload, "big"))
            elif n == 8:
                v = int.from_bytes(payload, "big")
                text = unix_time((v & 0x3FFFFFFFF) + (v >> 34) / 1e9)
            elif n == 12:
                text = unix_time(int.from_bytes(payload[4:], "big", signed=True)
                                 + int.from_bytes(payload[:4], "big") / 1e9)
        parent.add(Node(pos, r.pos - pos, VALUE, label, text, note="timestamp" if t == -1 else "ext"))
        return r.pos

    def container(r: Reader, pos: int, parent: Node, label: str, count: int, is_map: bool, depth: int) -> int:
        g = parent.add(Node(pos, 0, GROUP, label, f"({count}개)", close="}" if is_map else "]"))
        p = r.pos
        for i in range(count):
            if is_map:
                p = map_entry(g, lambda q, par, lab: obj(q, par, lab, depth + 1), p, i)
            else:
                p = obj(p, g, f"[{i}]", depth + 1)
        g.length = p - pos
        return p

    root = new_root("MessagePack", data)
    idx = [0]

    def one(pos, parent):
        idx[0] += 1
        return obj(pos, parent, f"객체 {idx[0]}")
    return run_stream(root, data, one)


register(Decoder(
    "msgpack", "MessagePack", "직렬화",
    "MessagePack 전체 사양 (fixint/str/bin/ext/timestamp/map/array). 연속된 객체 스트림 지원.",
    decode_msgpack,
    [Option("max_depth", "최대 깊이", "int", 100, minimum=1, maximum=1000)],
    single_root=True,
))
