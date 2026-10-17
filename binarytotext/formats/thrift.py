"""Apache Thrift binary and compact protocols (no IDL)."""

from __future__ import annotations

from ..model import GROUP, VALUE, Node
from ..reader import DecodeError, Reader, bytes_value, unpack_float, zigzag
from .base import Decoder, Option, new_root, register, run_stream

MSG_TYPES = {1: "CALL", 2: "REPLY", 3: "EXCEPTION", 4: "ONEWAY"}

# Binary protocol type ids
B_NAMES = {2: "bool", 3: "byte", 4: "double", 6: "i16", 8: "i32", 10: "i64", 11: "string",
           12: "struct", 13: "map", 14: "set", 15: "list", 16: "uuid"}
# Compact protocol type ids -> canonical names
C_NAMES = {1: "bool", 2: "bool", 3: "byte", 4: "i16", 5: "i32", 6: "i64", 7: "double",
           8: "string", 9: "list", 10: "set", 11: "map", 12: "struct", 13: "uuid"}


def _uuid(b: bytes) -> str:
    h = b.hex()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def decode_thrift_binary(data: bytes, opts: dict) -> Node:
    max_depth = opts["max_depth"]

    def value(r: Reader, t: int, parent: Node, label: str, depth: int) -> None:
        if depth > max_depth:
            raise DecodeError("최대 깊이 초과", r.pos)
        pos = r.pos

        def val(text: str) -> None:
            parent.add(Node(pos, r.pos - pos, VALUE, label, text, note=B_NAMES[t]))

        if t == 2:
            val("true" if r.u8() else "false")
        elif t == 3:
            val(str(r.sint(1)))
        elif t == 4:
            val(repr(r.float(8)))
        elif t == 6:
            val(str(r.sint(2)))
        elif t == 8:
            val(str(r.sint(4)))
        elif t == 10:
            val(str(r.sint(8)))
        elif t == 11:
            n = r.sint(4)
            if n < 0:
                raise DecodeError("음수 문자열 길이", pos)
            val(bytes_value(r.read(n)))
        elif t == 16:
            val(_uuid(r.read(16)))
        elif t == 12:
            g = parent.add(Node(pos, 0, GROUP, label, "(struct)", close="}"))
            struct(r, g, depth + 1)
            g.length = r.pos - pos
        elif t == 13:
            kt, vt, n = r.u8(), r.u8(), r.sint(4)
            check(kt, pos), check(vt, pos)
            g = parent.add(Node(pos, 0, GROUP, label, f"(map<{B_NAMES[kt]},{B_NAMES[vt]}> {n}개)", close="}"))
            for i in range(n):
                value(r, kt, g, f"키[{i}]", depth + 1)
                value(r, vt, g, f"값[{i}]", depth + 1)
            g.length = r.pos - pos
        elif t in (14, 15):
            et, n = r.u8(), r.sint(4)
            check(et, pos)
            g = parent.add(Node(pos, 0, GROUP, label, f"({B_NAMES[t]}<{B_NAMES[et]}> {n}개)", close="]"))
            for i in range(n):
                value(r, et, g, f"[{i}]", depth + 1)
            g.length = r.pos - pos
        else:
            raise DecodeError(f"알 수 없는 타입 {t}", pos)

    def check(t: int, pos: int) -> None:
        if t not in B_NAMES:
            raise DecodeError(f"알 수 없는 타입 {t}", pos)

    def struct(r: Reader, g: Node, depth: int) -> None:
        while True:
            t = r.u8()
            if t == 0:
                return
            check(t, r.pos - 1)
            fid = r.sint(2)
            fpos = r.pos - 3
            value(r, t, g, f"#{fid}", depth)
            g.children[-1].offset = fpos
            g.children[-1].length = r.pos - fpos

    def one(pos: int, parent: Node) -> int:
        r = Reader(data, pos)
        head = data[pos:pos + 4]
        if len(head) == 4 and head[0] == 0x80 and head[1] == 0x01:
            r.read(4)
            name = r.read(r.sint(4)).decode("utf-8", "replace")
            seq = r.sint(4)
            g = parent.add(Node(pos, 0, GROUP, f"메시지 {MSG_TYPES.get(head[3], head[3])}",
                                f"{name!r} seq={seq}", close="}"))
        else:
            g = parent.add(Node(pos, 0, GROUP, "struct", close="}"))
        struct(r, g, 0)
        g.length = r.pos - pos
        return r.pos

    return run_stream(new_root("Thrift Binary", data), data, one)


def decode_thrift_compact(data: bytes, opts: dict) -> Node:
    max_depth = opts["max_depth"]

    def value(r: Reader, t: int, parent: Node, label: str, depth: int, in_coll: bool = False) -> None:
        if depth > max_depth:
            raise DecodeError("최대 깊이 초과", r.pos)
        if t not in C_NAMES:
            raise DecodeError(f"알 수 없는 compact 타입 {t}", r.pos)
        pos = r.pos

        def val(text: str) -> None:
            parent.add(Node(pos, r.pos - pos, VALUE, label, text, note=C_NAMES[t]))

        if t in (1, 2):
            if in_coll:
                val("true" if r.u8() == 1 else "false")
            else:
                val("true" if t == 1 else "false")
        elif t == 3:
            val(str(r.sint(1)))
        elif t in (4, 5, 6):
            val(str(zigzag(r.varint())))
        elif t == 7:
            val(repr(unpack_float(r.read(8), "little")))
        elif t == 8:
            val(bytes_value(r.read(r.varint())))
        elif t == 13:
            val(_uuid(r.read(16)))
        elif t == 12:
            g = parent.add(Node(pos, 0, GROUP, label, "(struct)", close="}"))
            struct(r, g, depth + 1)
            g.length = r.pos - pos
        elif t in (9, 10):
            h = r.u8()
            n, et = h >> 4, h & 0x0F
            if n == 15:
                n = r.varint()
            g = parent.add(Node(pos, 0, GROUP, label, f"({C_NAMES[t]}<{C_NAMES.get(et, et)}> {n}개)", close="]"))
            for i in range(n):
                value(r, et, g, f"[{i}]", depth + 1, True)
            g.length = r.pos - pos
        elif t == 11:
            n = r.varint()
            kt = vt = 0
            if n:
                kv = r.u8()
                kt, vt = kv >> 4, kv & 0x0F
            g = parent.add(Node(pos, 0, GROUP, label, f"(map {n}개)", close="}"))
            for i in range(n):
                value(r, kt, g, f"키[{i}]", depth + 1, True)
                value(r, vt, g, f"값[{i}]", depth + 1, True)
            g.length = r.pos - pos

    def struct(r: Reader, g: Node, depth: int) -> None:
        last = 0
        while True:
            fpos = r.pos
            h = r.u8()
            if h == 0:
                return
            delta, t = h >> 4, h & 0x0F
            fid = last + delta if delta else zigzag(r.varint())
            last = fid
            value(r, t, g, f"#{fid}", depth)
            g.children[-1].offset = fpos
            g.children[-1].length = r.pos - fpos

    def one(pos: int, parent: Node) -> int:
        r = Reader(data, pos)
        if data[pos:pos + 1] == b"\x82":
            r.u8()
            vt = r.u8()
            seq = r.varint()
            name = r.read(r.varint()).decode("utf-8", "replace")
            g = parent.add(Node(pos, 0, GROUP, f"메시지 {MSG_TYPES.get(vt >> 5, vt >> 5)}",
                                f"{name!r} seq={seq}", close="}"))
        else:
            g = parent.add(Node(pos, 0, GROUP, "struct", close="}"))
        struct(r, g, 0)
        g.length = r.pos - pos
        return r.pos

    return run_stream(new_root("Thrift Compact", data), data, one)


_DEPTH = [Option("max_depth", "최대 깊이", "int", 64, minimum=1, maximum=1000)]

register(Decoder("thrift_binary", "Thrift Binary 프로토콜", "직렬화",
                 "TBinaryProtocol struct/메시지 (빅 엔디언, 필드 id + 타입).", decode_thrift_binary, _DEPTH, single_root=True))
register(Decoder("thrift_compact", "Thrift Compact 프로토콜", "직렬화",
                 "TCompactProtocol (필드 delta, zigzag varint).", decode_thrift_compact, _DEPTH, single_root=True))
