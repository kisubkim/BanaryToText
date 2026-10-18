"""BSON (https://bsonspec.org/spec.html)."""

from __future__ import annotations

from ..model import GROUP, VALUE, Node
from ..reader import DecodeError, Reader, bytes_value, fmt_float, hexs, quote, unix_time
from .base import Decoder, Option, new_root, register, run_stream

BIN_SUBTYPES = {0: "generic", 1: "function", 2: "binary(old)", 3: "uuid(old)", 4: "uuid", 5: "md5",
                6: "encrypted", 7: "compressed column", 8: "sensitive", 9: "vector", 0x80: "user"}


def decode_bson(data: bytes, opts: dict) -> Node:
    max_depth = opts["max_depth"]

    def cstr(r: Reader) -> str:
        b, _ = r.cstring()
        return b.decode("utf-8", "replace")

    def string(r: Reader) -> str:
        n = r.sint(4, "little")
        if n < 1:
            raise DecodeError(f"BSON 문자열 길이 {n} 오류", r.pos - 4)
        b = r.read(n)
        if b[-1] != 0:
            raise DecodeError("BSON 문자열이 00으로 끝나지 않습니다", r.pos - 1)
        return b[:-1].decode("utf-8", "replace")

    def document(pos: int, parent: Node, label: str, is_array: bool, depth: int) -> int:
        if depth > max_depth:
            raise DecodeError("최대 깊이 초과", pos)
        r = Reader(data, pos)
        size = r.sint(4, "little")
        if size < 5 or pos + size > len(data):
            raise DecodeError(f"문서 크기 {size} 가 범위를 벗어납니다", pos)
        end = pos + size
        if data[end - 1] != 0:
            raise DecodeError("문서가 00으로 끝나지 않습니다", end - 1)
        g = parent.add(Node(pos, size, GROUP, label, close="]" if is_array else "}"))
        r.end = end - 1
        while not r.eof():
            epos = r.pos
            t = r.u8()
            name = cstr(r)

            def val(text: str, note: str = "") -> None:
                g.add(Node(epos, r.pos - epos, VALUE, name, text, note=note))

            if t == 0x01:
                val(fmt_float(r.float(8, "little")), "double")
            elif t in (0x02, 0x0D, 0x0E):
                val(quote(string(r)), {2: "string", 0x0D: "javascript", 0x0E: "symbol"}[t])
            elif t in (0x03, 0x04):
                r.pos = document(r.pos, g, name, t == 0x04, depth + 1)
                g.children[-1].offset = epos
                g.children[-1].length = r.pos - epos
            elif t == 0x05:
                n = r.sint(4, "little")
                st = r.u8()
                b = r.read(n)
                text = bytes_value(b, ())
                if st in (3, 4) and n == 16:
                    h = b.hex()
                    text = f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"
                val(text, f"binary {BIN_SUBTYPES.get(st, hex(st))}")
            elif t == 0x06:
                val("undefined")
            elif t == 0x07:
                val(r.read(12).hex(), "ObjectId")
            elif t == 0x08:
                val("true" if r.u8() else "false")
            elif t == 0x09:
                ms = r.sint(8, "little")
                val(f"{unix_time(ms / 1000)}", "UTC datetime")
            elif t == 0x0A:
                val("null")
            elif t == 0x0B:
                pat = cstr(r)
                flags = cstr(r)
                val(f"/{pat}/{flags}", "regex")
            elif t == 0x0C:
                ns = string(r)
                val(f"{quote(ns)} {r.read(12).hex()}", "DBPointer")
            elif t == 0x0F:
                total = r.sint(4, "little")
                start = r.pos - 4
                code = string(r)
                r.pos = document(r.pos, g, f"{name} scope", False, depth + 1)
                if r.pos != start + total:
                    raise DecodeError("code_w_s 길이 불일치", start)
                g.children.insert(len(g.children) - 1, Node(epos, start - epos + 4, VALUE, name,
                                                            quote(code), note="code w/ scope"))
            elif t == 0x10:
                val(str(r.sint(4, "little")), "int32")
            elif t == 0x11:
                inc = r.uint(4, "little")
                sec = r.uint(4, "little")
                val(f"{unix_time(sec)} inc={inc}", "timestamp")
            elif t == 0x12:
                val(str(r.sint(8, "little")), "int64")
            elif t == 0x13:
                val(hexs(r.read(16)), "decimal128(raw)")
            elif t == 0xFF:
                val("MinKey")
            elif t == 0x7F:
                val("MaxKey")
            else:
                raise DecodeError(f"알 수 없는 BSON 타입 0x{t:02X}", epos)
        return end

    root = new_root("BSON", data)
    idx = [0]

    def one(pos, parent):
        idx[0] += 1
        return document(pos, parent, f"문서 {idx[0]}", False, 0)
    return run_stream(root, data, one)


register(Decoder(
    "bson", "BSON (MongoDB)", "직렬화",
    "리틀 엔디언 BSON 문서 스트림. ObjectId, datetime, binary/UUID, regex 등 전체 타입.",
    decode_bson,
    [Option("max_depth", "최대 깊이", "int", 100, minimum=1, maximum=1000)],
))
