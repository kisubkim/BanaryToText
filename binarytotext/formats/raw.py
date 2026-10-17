"""Raw views: hex dump and string extraction, as decoders."""

from __future__ import annotations

from ..model import INFO, UNKNOWN, VALUE, Node
from ..reader import ascii_preview, quote
from .base import Decoder, Option, new_root, register


def decode_hexdump(data: bytes, opts: dict) -> Node:
    width = int(opts["width"])
    root = new_root("Hex dump", data)
    for off in range(0, len(data), width):
        b = data[off:off + width]
        hx = b.hex(" ").upper().ljust(width * 3 - 1)
        root.add(Node(off, len(b), VALUE, "", f"{hx}  |{ascii_preview(b, width)}|"))
    return root


def decode_strings(data: bytes, opts: dict) -> Node:
    from ..heuristics import extract_strings
    root = new_root("문자열 추출", data)
    found = extract_strings(data, int(opts["min_len"]), opts["encodings"].split(","))
    pos = 0
    for s in found:
        if s.offset > pos:
            root.add(Node(pos, s.offset - pos, UNKNOWN, f"{s.offset - pos} byte", ""))
        root.add(Node(s.offset, s.length, VALUE, s.encoding, quote(s.text),
                      note="00 종료" if s.terminated else ""))
        pos = max(pos, s.offset + s.length)
    if not found:
        root.add(Node(0, 0, INFO, "문자열을 찾지 못했습니다"))
    return root


register(Decoder("hexdump", "Hex 덤프", "기본", "오프셋 · hex · ASCII 의 고전적인 덤프.", decode_hexdump,
                 [Option("width", "한 줄 byte 수", "choice", 16, [(8, "8"), (16, "16"), (32, "32")])]))

register(Decoder("strings", "문자열 추출", "기본",
                 "ASCII / UTF-8 / UTF-16 / CP949(한글) 문자열을 찾아 위치와 함께 표시.", decode_strings,
                 [Option("min_len", "최소 글자 수", "int", 4, minimum=1, maximum=1000),
                  Option("encodings", "인코딩", "choice", "ascii,utf-8,utf-16-le,cp949",
                         [("ascii,utf-8,utf-16-le,cp949", "ASCII+UTF-8+UTF-16LE+CP949"),
                          ("ascii", "ASCII"), ("ascii,utf-8", "ASCII+UTF-8"),
                          ("ascii,utf-16-le,utf-16-be", "ASCII+UTF-16"), ("ascii,cp949", "ASCII+CP949")])]))
