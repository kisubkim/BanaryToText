"""Optional transforms applied before decoding (decompression, base64, XOR, ...)."""

from __future__ import annotations

import base64
import binascii
import bz2
import gzip
import lzma
import re
import zlib

TRANSFORMS = {
    "none": "없음",
    "skip": "앞부분 N byte 건너뛰기",
    "zlib": "zlib 압축 해제",
    "deflate": "raw deflate 압축 해제",
    "gzip": "gzip 압축 해제",
    "bz2": "bzip2 압축 해제",
    "lzma": "xz/lzma 압축 해제",
    "base64": "Base64 텍스트 → 바이트",
    "hextext": "hex 텍스트 → 바이트",
    "xor": "XOR (키 hex)",
}


def apply(data: bytes, kind: str, arg: str = "") -> bytes:
    if kind == "none":
        return data
    if kind == "skip":
        return data[int(arg or "0", 0):]
    if kind == "zlib":
        return zlib.decompress(data)
    if kind == "deflate":
        return zlib.decompress(data, -15)
    if kind == "gzip":
        return gzip.decompress(data)
    if kind == "bz2":
        return bz2.decompress(data)
    if kind == "lzma":
        return lzma.decompress(data)
    if kind == "base64":
        return base64.b64decode(re.sub(rb"[^A-Za-z0-9+/=_-]", b"", data).replace(b"-", b"+").replace(b"_", b"/")
                                + b"==", validate=False)
    if kind == "hextext":
        h = re.sub(rb"(0x|[^0-9A-Fa-f])", b"", data)
        return binascii.unhexlify(h[: len(h) // 2 * 2])
    if kind == "xor":
        key = bytes.fromhex(re.sub(r"\s", "", arg or "00")) or b"\x00"
        return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    raise ValueError(f"알 수 없는 전처리 '{kind}'")


def detect(data: bytes) -> list[str]:
    """Transforms that succeed on this data (cheap checks on a prefix)."""
    found = []
    head = data[:1 << 16]
    candidates = []
    if len(head) > 2 and head[0] & 0x0F == 8 and int.from_bytes(head[:2], "big") % 31 == 0:
        candidates.append(("zlib", zlib.decompressobj()))
    if head[:2] == b"\x1f\x8b":
        candidates.append(("gzip", zlib.decompressobj(31)))
    if head[:3] == b"BZh":
        candidates.append(("bz2", bz2.BZ2Decompressor()))
    if head[:6] == b"\xfd7zXZ\x00":
        candidates.append(("lzma", lzma.LZMADecompressor()))
    for kind, d in candidates:
        try:
            if d.decompress(head, 4096):
                found.append(kind)
        except Exception:
            pass
    sample = head[:4096].strip()
    if sample and re.fullmatch(rb"[A-Za-z0-9+/=\r\n]+", sample) and len(sample) >= 16:
        found.append("base64")
    if sample and re.fullmatch(rb"(?:0x)?[0-9A-Fa-f\s,]+", sample) and len(sample) >= 8:
        found.append("hextext")
    return found
