"""Bounds-checked byte reader and small value-formatting helpers."""

from __future__ import annotations

import contextlib
import contextvars
import struct
from datetime import datetime, timezone
from decimal import Decimal

# Significant digits for float/double text; 0 = shortest exact form. Context-local, so each
# decode thread sets its own (see float_digits()).
_FLOAT_DIGITS: contextvars.ContextVar[int] = contextvars.ContextVar("float_digits", default=0)


@contextlib.contextmanager
def float_digits(n: int):
    """Decode with floats shown to n significant digits (0 = automatic)."""
    token = _FLOAT_DIGITS.set(max(0, int(n or 0)))
    try:
        yield
    finally:
        _FLOAT_DIGITS.reset(token)


class DecodeError(Exception):
    def __init__(self, msg: str, offset: int | None = None):
        super().__init__(msg)
        self.offset = offset


class Reader:
    def __init__(self, data: bytes, pos: int = 0, end: int | None = None):
        self.data = data
        self.pos = pos
        self.end = len(data) if end is None else end

    @property
    def remaining(self) -> int:
        return self.end - self.pos

    def eof(self) -> bool:
        return self.pos >= self.end

    def need(self, n: int) -> None:
        if n < 0 or self.pos + n > self.end:
            raise DecodeError(f"데이터 부족: {n} byte 필요, {self.remaining} byte 남음", self.pos)

    def read(self, n: int) -> bytes:
        self.need(n)
        b = self.data[self.pos:self.pos + n]
        self.pos += n
        return b

    def peek(self, n: int = 1) -> bytes:
        return self.data[self.pos:min(self.pos + n, self.end)]

    def u8(self) -> int:
        self.need(1)
        v = self.data[self.pos]
        self.pos += 1
        return v

    def uint(self, n: int, endian: str = "big") -> int:
        return int.from_bytes(self.read(n), endian)

    def sint(self, n: int, endian: str = "big") -> int:
        return int.from_bytes(self.read(n), endian, signed=True)

    def float(self, n: int, endian: str = "big") -> float:
        return unpack_float(self.read(n), endian)

    def float_text(self, n: int, endian: str = "big") -> str:
        return float_text(self.read(n), endian)

    def varint(self, max_bytes: int = 10) -> int:
        """Unsigned LEB128 (protobuf / thrift compact style)."""
        result = shift = 0
        for _ in range(max_bytes):
            b = self.u8()
            result |= (b & 0x7F) << shift
            if not b & 0x80:
                return result
            shift += 7
        raise DecodeError("varint가 너무 깁니다", self.pos)

    def cstring(self, terminator: bytes = b"\x00", max_len: int | None = None) -> tuple[bytes, bool]:
        """Read up to terminator. Returns (payload, terminated)."""
        limit = self.end if max_len is None else min(self.end, self.pos + max_len + len(terminator))
        idx = self.data.find(terminator, self.pos, limit)
        if idx < 0:
            raise DecodeError(f"종료 바이트 {terminator.hex(' ').upper()} 를 찾지 못했습니다", self.pos)
        b = self.data[self.pos:idx]
        self.pos = idx + len(terminator)
        return b, True


def zigzag(n: int) -> int:
    return (n >> 1) ^ -(n & 1)


def unpack_float(b: bytes, endian: str = "big") -> float:
    e = ">" if endian == "big" else "<"
    if len(b) == 2:
        return struct.unpack(e + "e", b)[0]
    if len(b) == 4:
        return struct.unpack(e + "f", b)[0]
    if len(b) == 8:
        return struct.unpack(e + "d", b)[0]
    raise DecodeError(f"지원하지 않는 float 크기 {len(b)}")


def eng(s: str) -> str:
    """Float text with a 1-3 digit integer part: plain for 1 <= |x| < 1000, otherwise an exponent
    in steps of 3 without padding or "+" (1234.5 -> 1.2345e3, 0.5 -> 500e-3, 1.23e-05 -> 12.3e-6).
    Zero and whole numbers keep a ".0" so they read as floats (0 -> 0.0, 30 -> 30.0).

    The digits are moved, not recomputed, so the number of significant digits stays the same.
    """
    d = Decimal(s)
    if not d.is_finite():
        return s
    if d.is_zero():
        return "-0.0" if d.is_signed() else "0.0"
    d = d.normalize()  # repr's trailing ".0" is not a significant digit: 1000.0 -> 1e3, not 1.0000e3
    e = d.adjusted() - d.adjusted() % 3  # exponent of the leading digit, rounded down to a multiple of 3
    m = f"{d.scaleb(-e):f}"
    if e == 0 and "." not in m:
        m += ".0"
    return m if e == 0 else f"{m}e{e}"


def fmt_float(v: float) -> str:
    """A double as text: repr, or the current float_digits() significant digits."""
    d = _FLOAT_DIGITS.get()
    if v != v or v in (float("inf"), float("-inf")):
        return repr(v)
    return eng(repr(v) if d <= 0 else f"{v:.{d}g}")


def float_text(b: bytes, endian: str = "big") -> str:
    """Shortest decimal that reads back to the same bytes (36.6, not 36.599998474121094 for float32),
    or the current float_digits() significant digits."""
    v = unpack_float(b, endian)
    if len(b) == 8 or _FLOAT_DIGITS.get() > 0 or v != v or v in (float("inf"), float("-inf")):
        return fmt_float(v)
    code = (">" if endian == "big" else "<") + ("e" if len(b) == 2 else "f")
    for digits in range(1, 18):
        s = f"{v:.{digits}g}"
        if struct.pack(code, float(s)) == b:
            return eng(repr(float(s)))
    return eng(repr(v))


def hexs(b: bytes, limit: int = 64) -> str:
    s = b[:limit].hex(" ").upper()
    if len(b) > limit:
        s += f" … (+{len(b) - limit} byte)"
    return s


def ascii_preview(b: bytes, limit: int = 64) -> str:
    return "".join(chr(c) if 0x20 <= c < 0x7F else "." for c in b[:limit])


def is_printable_text(s: str) -> bool:
    if not s:
        return False
    bad = sum(1 for ch in s if (ord(ch) < 0x20 and ch not in "\t\r\n") or 0x7F <= ord(ch) < 0xA0
              or ch == "\ufffd")
    return bad == 0


def quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "\\r") \
        .replace("\n", "\\n").replace("\t", "\\t").replace("\x00", "\\0") + '"'


def bytes_value(b: bytes, encodings: tuple[str, ...] = ("utf-8",)) -> str:
    """Show bytes as quoted text when they decode to printable text, else hex."""
    if b:
        for enc in encodings:
            try:
                s = b.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
            if is_printable_text(s):
                return quote(s)
    return f"<{len(b)} byte> {hexs(b)}" if b else "<0 byte>"


def unix_time(sec: float) -> str:
    try:
        return datetime.fromtimestamp(sec, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):
        return "범위 밖 시각"
