"""Auto-analysis for files whose encoding is unknown.

- strings in several encodings, and which bytes precede them (tag candidates)
- suggested rules ("byte XX is always followed by a 00-terminated string")
- entropy, magic signatures, repeating record sizes
- every built-in decoder tried and ranked by how much of the file it explains
"""

from __future__ import annotations

import math
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from . import preprocess
from .formats import REGISTRY
from .model import compute_stats
from .rules import FieldSpec, Rule


@dataclass
class FoundString:
    offset: int
    length: int  # bytes
    encoding: str
    text: str
    terminated: bool


_PATTERNS = {
    "ascii": rb"[\x20-\x7E]{%d,}",
    "utf-8": rb"(?:[\x20-\x7E]|[\xC2-\xDF][\x80-\xBF]|[\xE0-\xEF][\x80-\xBF]{2}|[\xF0-\xF4][\x80-\xBF]{3}){%d,}",
    "utf-16-le": rb"(?:[\x20-\x7E]\x00|[\x00-\xFF][\xAC-\xD7]){%d,}",
    "utf-16-be": rb"(?:\x00[\x20-\x7E]|[\xAC-\xD7][\x00-\xFF]){%d,}",
    "cp949": rb"(?:[\x20-\x7E]|[\xB0-\xC8][\xA1-\xFE]){%d,}",
}


def extract_strings(data: bytes, min_len: int = 4, encodings=("ascii", "utf-8", "utf-16-le", "cp949"),
                    limit: int = 200_000) -> list[FoundString]:
    found: list[FoundString] = []
    for enc in encodings:
        enc = enc.strip()
        pat = _PATTERNS.get(enc)
        if pat is None:
            continue
        for m in re.finditer(pat % min_len, data):
            b = m.group()
            if enc in ("utf-16-le", "utf-16-be") and len(b) % 2:
                b = b[:-1]
            try:
                s = b.decode(enc)
            except UnicodeDecodeError:
                continue
            if enc in ("utf-8", "cp949") and s.isascii():
                continue  # plain ASCII is reported by the ascii pass
            if enc.startswith("utf-16") and not any(c.isalnum() for c in s):
                continue
            end = m.start() + len(b)
            term = data[end:end + (2 if enc.startswith("utf-16") else 1)]
            found.append(FoundString(m.start(), len(b), enc, s, bool(term) and not any(term)))
            if len(found) >= limit:
                break
    # Keep the best span among overlaps (greedy). Pure-Hangul UTF-16 is often
    # really CP949, so it loses ties; real UTF-16 text usually has spaces.
    prio = {"ascii": 0, "utf-8": 1, "cp949": 2, "utf-16-le": 3, "utf-16-be": 3}

    def weight(s: FoundString) -> float:
        w = s.length
        if s.encoding.startswith("utf-16") and not any(c.isascii() for c in s.text):
            w *= 0.6
        return w

    found.sort(key=lambda s: (-weight(s), prio.get(s.encoding, 9)))
    taken = bytearray(len(data))
    out: list[FoundString] = []
    for s in found:
        if any(taken[s.offset:s.offset + s.length]):
            continue
        taken[s.offset:s.offset + s.length] = b"\x01" * s.length
        out.append(s)
    out.sort(key=lambda s: s.offset)
    return out


@dataclass
class Suggestion:
    tag: str
    kind: str  # human description
    count: int
    examples: list[str]
    rule: Rule

    @property
    def text(self) -> str:
        return f"{self.tag} → {self.kind} ({self.count}회)"


def suggest_rules(data: bytes, strings: list[FoundString], min_count: int = 2) -> list[Suggestion]:
    """Find bytes that consistently introduce strings."""
    c_term: dict[int, list[FoundString]] = defaultdict(list)
    p1: dict[int, list[FoundString]] = defaultdict(list)
    p2le: dict[int, list[FoundString]] = defaultdict(list)
    p2be: dict[int, list[FoundString]] = defaultdict(list)
    for s in strings:
        o, n = s.offset, s.length
        if s.encoding.startswith("utf-16"):
            continue
        if o >= 1 and s.terminated:
            c_term[data[o - 1]].append(s)
        if o >= 2 and data[o - 1] == n:
            p1[data[o - 2]].append(s)
        if o >= 3:
            v_le = data[o - 2] | data[o - 1] << 8
            v_be = data[o - 1] | data[o - 2] << 8
            if v_le == n and n > 0xFF:
                p2le[data[o - 3]].append(s)
            elif v_be == n and n > 0xFF:
                p2be[data[o - 3]].append(s)
    out: list[Suggestion] = []

    def add(groups, kind, mk_field):
        for tag, items in groups.items():
            if len(items) < min_count:
                continue
            enc = Counter(s.encoding for s in items).most_common(1)[0][0]
            f = mk_field()
            if enc != "ascii":
                f.encoding = enc
            out.append(Suggestion(f"{tag:02X}", kind, len(items), [s.text[:40] for s in items[:3]],
                                  Rule(f"{tag:02X}", f"문자열_{tag:02X}", "value", [f],
                                       comment=f"자동 제안: {kind}, {len(items)}회 관찰")))

    add(c_term, "00 종료 문자열", lambda: FieldSpec("cstring", terminator="00"))
    add(p1, "1 byte 길이 + 문자열", lambda: FieldSpec("pstring", len_size="1"))
    add(p2le, "2 byte(LE) 길이 + 문자열", lambda: FieldSpec("pstring", len_size="2", endian="little"))
    add(p2be, "2 byte(BE) 길이 + 문자열", lambda: FieldSpec("pstring", len_size="2", endian="big"))
    # A byte that precedes many strings but is itself common elsewhere is a weak tag.
    freq = Counter(data)
    out.sort(key=lambda s: (-(s.count / max(1, freq[int(s.tag, 16)])), -s.count))
    return out


def entropy(b: bytes) -> float:
    if not b:
        return 0.0
    n = len(b)
    return -sum(c / n * math.log2(c / n) for c in Counter(b).values())


def block_entropy(data: bytes, block: int = 4096) -> list[tuple[int, float]]:
    return [(o, entropy(data[o:o + block])) for o in range(0, len(data), block)]


MAGIC = [
    (b"\x89PNG\r\n\x1a\n", "PNG 이미지"), (b"\xff\xd8\xff", "JPEG 이미지"), (b"GIF8", "GIF 이미지"),
    (b"BM", "BMP 이미지(추정)"), (b"%PDF", "PDF 문서"), (b"PK\x03\x04", "ZIP (docx/xlsx/jar/apk 포함)"),
    (b"\x1f\x8b", "gzip"), (b"BZh", "bzip2"), (b"\xfd7zXZ\x00", "xz"), (b"7z\xbc\xaf\x27\x1c", "7-Zip"),
    (b"Rar!", "RAR"), (b"\x7fELF", "ELF 실행 파일"), (b"MZ", "Windows PE/DOS 실행 파일"),
    (b"SQLite format 3\x00", "SQLite DB"), (b"\xca\xfe\xba\xbe", "Java class / Mach-O fat"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "MS OLE2 (doc/xls/msg)"), (b"RIFF", "RIFF (WAV/AVI/WebP)"),
    (b"OggS", "Ogg"), (b"fLaC", "FLAC"), (b"ID3", "MP3 (ID3)"), (b"\x00\x00\x01\x00", "ICO"),
    (b"\xac\xed\x00\x05", "Java 직렬화 스트림"), (b"\x80\x04\x95", "Python pickle (protocol 4)"),
    (b"\x80\x05\x95", "Python pickle (protocol 5)"), (b"bplist00", "Apple binary plist"),
    (b"\x28\xb5\x2f\xfd", "Zstandard"), (b"\x04\x22\x4d\x18", "LZ4 frame"), (b"wOFF", "WOFF 폰트"),
    (b"\x00\x01\x00\x00", "TrueType 폰트(추정)"), (b"OTTO", "OpenType 폰트"), (b"-----BEGIN", "PEM (ASN.1)"),
    (b"\x30\x82", "DER ASN.1 SEQUENCE(추정)"), (b"Obj\x01", "Avro 컨테이너"), (b"PAR1", "Parquet"),
    (b"ORC", "ORC"), (b"\x93NUMPY", "NumPy .npy"), (b"\x89HDF\r\n\x1a\n", "HDF5"),
    (b"\x80\x01\x00", "Thrift Binary 메시지(추정)"), (b"\x82\x21", "Thrift Compact 메시지(추정)"),
]


def magic(data: bytes) -> list[str]:
    return [name for sig, name in MAGIC if data.startswith(sig)]


def record_periods(data: bytes, max_period: int = 512, sample: int = 1 << 16, top: int = 5) -> list[tuple[int, float]]:
    """Self-similarity at shift k: fraction of bytes equal to the byte k later."""
    d = data[:sample]
    if len(d) < 64:
        return []
    res = []
    for k in range(2, min(max_period, len(d) // 4) + 1):
        a, b = d[:-k], d[k:]
        x = (int.from_bytes(a, "big") ^ int.from_bytes(b, "big")).to_bytes(len(a), "big")
        res.append((k, x.count(0) / len(a)))
    if not res:
        return []
    base = sorted(r for _, r in res)[len(res) // 2]
    # Prefer the smallest period among near-equal scores (multiples score alike).
    scored = sorted(res, key=lambda kv: -kv[1])
    out: list[tuple[int, float]] = []
    for k, r in scored:
        if r <= base * 1.5 or r < 0.2:
            break
        if any(k % p == 0 for p, _ in out):
            continue
        out.append((k, r))
        if len(out) >= top:
            break
    return out


@dataclass
class FormatScore:
    key: str
    title: str
    options: dict
    coverage: float
    errors: int
    nodes: int
    seconds: float
    first_error: str = ""
    top_items: int = 1
    single_root: bool = False
    avg_leaf: float = 0.0

    @property
    def score(self) -> float:
        s = self.coverage
        if self.errors:  # a parse that breaks is far less likely to be the format
            s *= 0.5
        if self.nodes <= 2:  # one blob explaining everything says little
            s *= 0.5
        if self.single_root and self.top_items > 1:
            s /= 1 + 0.5 * math.log2(self.top_items)
        if self.avg_leaf < 1.5:  # mostly 1-byte items: noise accepted as e.g. fixints
            s *= 0.5
        return s


def rank_formats(data: bytes, sample: int = 256 * 1024, time_budget: float = 10.0) -> list[FormatScore]:
    d = data[:sample]
    out: list[FormatScore] = []
    t_end = time.monotonic() + time_budget
    for dec in REGISTRY.values():
        if dec.family == "기본":
            continue
        for variant in [{}] + dec.variants:
            if time.monotonic() > t_end:
                break
            opts = dec.defaults()
            opts.update(variant)
            t0 = time.monotonic()
            try:
                root = dec.decode(d, opts)
            except Exception as e:  # a decoder bug must not break the ranking
                out.append(FormatScore(dec.key, dec.title, variant, 0.0, 1, 0, 0.0, f"내부 오류: {e}"))
                continue
            st = compute_stats(root, len(d))
            err = next((n.label for n in root.walk() if n.kind == "error"), "")
            leaves = [n.length for n in root.walk() if n.kind == "value" and not n.children]
            top = sum(1 for c in root.children if c.kind not in ("error", "unknown", "info"))
            out.append(FormatScore(dec.key, dec.title, variant, st.coverage, st.errors, st.nodes,
                                   time.monotonic() - t0, err, top, dec.single_root,
                                   sum(leaves) / len(leaves) if leaves else 0.0))
    out.sort(key=lambda s: -s.score)
    return out


@dataclass
class Report:
    size: int
    entropy: float
    entropy_blocks: list[tuple[int, float]]
    magic: list[str]
    transforms: list[str]
    strings: list[FoundString]
    suggestions: list[Suggestion]
    periods: list[tuple[int, float]]
    formats: list[FormatScore]
    top_bytes: list[tuple[int, int]] = field(default_factory=list)


def analyze(data: bytes, rank: bool = True) -> Report:
    strings = extract_strings(data[:4 << 20], 3)
    return Report(
        size=len(data),
        entropy=entropy(data[:4 << 20]),
        entropy_blocks=block_entropy(data[:1 << 20]),
        magic=magic(data),
        transforms=preprocess.detect(data),
        strings=strings,
        suggestions=suggest_rules(data, strings),
        periods=record_periods(data),
        formats=rank_formats(data) if rank else [],
        top_bytes=Counter(data[:4 << 20]).most_common(16),
    )


def report_text(rep: Report) -> str:
    lines = [f"크기: {rep.size:,} byte", f"엔트로피: {rep.entropy:.3f} bit/byte"
             + ("  → 압축/암호화 가능성 높음" if rep.entropy > 7.5 else
                "  → 텍스트 위주" if rep.entropy < 5 else "")]
    if rep.magic:
        lines.append("파일 시그니처: " + ", ".join(rep.magic))
    if rep.transforms:
        lines.append("가능한 전처리: " + ", ".join(preprocess.TRANSFORMS[t] for t in rep.transforms))
    if rep.periods:
        lines.append("반복 주기(고정 길이 레코드 후보): "
                     + ", ".join(f"{k} byte ({r:.0%})" for k, r in rep.periods))
    lines.append("자주 나오는 byte: " + ", ".join(f"{b:02X}×{c}" for b, c in rep.top_bytes[:10]))
    lines.append("")
    if rep.formats:
        lines.append("== 내장 포맷 적합도 (앞 256KB 기준) ==")
        for f in rep.formats[:12]:
            opt = f" {f.options}" if f.options else ""
            err = f"  첫 오류: {f.first_error}" if f.first_error else ""
            lines.append(f"{f.score:6.1%}  {f.title}{opt}  (해석 {f.coverage:.1%}, 항목 {f.nodes}){err}")
        lines.append("")
    if rep.suggestions:
        lines.append("== 태그 후보 (문자열 앞에 반복해서 나오는 byte) ==")
        for s in rep.suggestions[:20]:
            lines.append(f"{s.text}  예: {', '.join(repr(e) for e in s.examples)}")
        lines.append("")
    lines.append(f"== 문자열 {len(rep.strings)}개 (처음 50개) ==")
    for s in rep.strings[:50]:
        lines.append(f"[{s.offset:08X}] {s.encoding:9} {s.text[:80]!r}" + ("  (00 종료)" if s.terminated else ""))
    return "\n".join(lines) + "\n"
