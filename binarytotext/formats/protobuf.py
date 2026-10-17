"""Protocol Buffers wire format without a schema (like `protoc --decode_raw`)."""

from __future__ import annotations

from ..model import ERROR, GROUP, VALUE, Node
from ..reader import DecodeError, Reader, hexs, is_printable_text, quote, unpack_float, zigzag
from .base import Decoder, Option, new_root, register, run_stream

WIRE = {0: "varint", 1: "i64", 2: "len", 3: "group 시작", 4: "group 끝", 5: "i32"}


def _varint_text(v: int) -> str:
    extra = []
    if v >= 1 << 63:
        extra.append(f"int64 {v - (1 << 64)}")
    elif v & 1 and v < 1 << 32:
        extra.append(f"sint {zigzag(v)}")
    return "  ".join([str(v)] + [f"({p})" for p in extra])


def _message(data: bytes, start: int, end: int, parent: Node, opts: dict, depth: int,
             group_field: int | None = None) -> int:
    r = Reader(data, start, end)
    while not r.eof():
        fpos = r.pos
        key = r.varint()
        field, wt = key >> 3, key & 7
        if field == 0 or wt not in WIRE:
            raise DecodeError(f"잘못된 필드 키 0x{key:X} (필드 {field}, wire {wt})", fpos)
        label = f"#{field}"
        if wt == 0:
            v = r.varint()
            parent.add(Node(fpos, r.pos - fpos, VALUE, label, _varint_text(v), note="varint"))
        elif wt == 1:
            b = r.read(8)
            u = int.from_bytes(b, "little")
            parent.add(Node(fpos, r.pos - fpos, VALUE, label,
                            f"{u}  (double {unpack_float(b, 'little')!r})", note="fixed64"))
        elif wt == 5:
            b = r.read(4)
            u = int.from_bytes(b, "little")
            parent.add(Node(fpos, r.pos - fpos, VALUE, label,
                            f"{u}  (float {unpack_float(b, 'little')!r})", note="fixed32"))
        elif wt == 2:
            n = r.varint()
            vstart = r.pos
            r.need(n)
            vend = vstart + n
            r.pos = vend
            _len_value(data, fpos, vstart, vend, parent, label, opts, depth)
        elif wt == 3:
            g = parent.add(Node(fpos, 0, GROUP, label, "(group)", close="}"))
            p = _message(data, r.pos, end, g, opts, depth + 1, group_field=field)
            g.length = p - fpos
            r.pos = p
        else:  # wt == 4
            if group_field != field:
                raise DecodeError(f"짝이 없는 group 끝 (필드 {field})", fpos)
            return r.pos
    if group_field is not None:
        raise DecodeError(f"group {group_field} 이 닫히지 않았습니다", r.pos)
    return r.pos


def _try_message(data: bytes, start: int, end: int, opts: dict, depth: int) -> list[Node] | None:
    if not opts["nested"] or depth >= opts["max_depth"] or end <= start:
        return None
    holder = Node(start, end - start, GROUP, "")
    try:
        if _message(data, start, end, holder, opts, depth + 1) != end:
            return None
    except DecodeError:
        return None
    if any(c.kind == ERROR for c in holder.walk()):
        return None
    return holder.children


def _len_value(data, fpos, vstart, vend, parent, label, opts, depth):
    b = data[vstart:vend]
    text = None
    try:
        s = b.decode("utf-8")
        if is_printable_text(s):
            text = s
    except UnicodeDecodeError:
        pass
    nested = _try_message(data, vstart, vend, opts, depth)
    # Printable text that also happens to parse as a message is almost always text.
    if nested is not None and text is None:
        g = parent.add(Node(fpos, vend - fpos, GROUP, label, f"(메시지 {len(b)} byte)", close="}"))
        g.children.extend(nested)
        return
    if text is not None:
        parent.add(Node(fpos, vend - fpos, VALUE, label, quote(text), note="string"))
        return
    packed = _packed_varints(b) if opts["packed"] else None
    val = hexs(b) if b else '""'
    if packed:
        val += f"  (packed varint: {', '.join(map(str, packed[:16]))}{' …' if len(packed) > 16 else ''})"
    parent.add(Node(fpos, vend - fpos, VALUE, label, f"<{len(b)} byte> {val}", note="bytes"))


def _packed_varints(b: bytes) -> list[int] | None:
    if not b:
        return None
    r = Reader(b)
    out = []
    try:
        while not r.eof():
            out.append(r.varint())
    except DecodeError:
        return None
    return out if len(out) > 1 else None


def decode_protobuf(data: bytes, opts: dict) -> Node:
    root = new_root("Protobuf 메시지", data)
    if opts["length_delimited"]:
        def one(pos, parent):
            r = Reader(data, pos)
            n = r.varint()
            r.need(n)
            g = parent.add(Node(pos, r.pos - pos + n, GROUP, "메시지", f"(길이 {n})", close="}"))
            _message(data, r.pos, r.pos + n, g, opts, 0)
            return r.pos + n
        return run_stream(root, data, one)

    def whole(pos, parent):
        return _message(data, pos, len(data), parent, opts, 0)
    return run_stream(root, data, whole)


register(Decoder(
    "protobuf", "Protocol Buffers (스키마 없이)", "직렬화",
    "필드 번호·wire type 기반 해석. 길이 구분 필드는 중첩 메시지/문자열/바이트를 추정.",
    decode_protobuf,
    [Option("nested", "중첩 메시지 추정", "bool", True),
     Option("packed", "packed varint 표시", "bool", True),
     Option("length_delimited", "varint 길이 접두 메시지 스트림", "bool", False),
     Option("max_depth", "최대 깊이", "int", 16, minimum=0, maximum=128)],
    variants=[{"length_delimited": True}],
))
