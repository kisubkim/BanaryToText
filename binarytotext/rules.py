"""User-defined tag rules: "when these bytes appear, read what follows as X".

A RuleSet is a list of Rules. Each rule has a tag pattern (one or more bytes,
`??` = any byte, or an ASCII literal in quotes), an action, and the fields
read after the tag. The engine scans the input left to right; at each
position it applies the longest matching tag, otherwise the byte is
collected into an "unknown" run.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .model import ERROR, GROUP, INFO, UNKNOWN, VALUE, Node
from .reader import DecodeError, Reader, ascii_preview, float_text, hexs, quote, unix_time, zigzag

FIELD_TYPES = {
    "uint": "부호 없는 정수",
    "int": "부호 있는 정수",
    "float": "실수 (2/4/8 byte)",
    "bool": "불리언",
    "cstring": "문자열 (종료 바이트까지)",
    "fstring": "고정 길이 문자열",
    "pstring": "길이 접두 문자열",
    "bytes": "고정 길이 바이트 (hex)",
    "pbytes": "길이 접두 바이트 (hex)",
    "bcd": "BCD 숫자",
    "varint": "varint (LEB128)",
    "svarint": "zigzag varint",
    "unixtime": "Unix 시각",
    "skip": "건너뛰기 (표시 안 함)",
    "rest": "블록/파일 끝까지 전부",
}

ACTIONS = {
    "value": "값 읽기",
    "const": "고정 값 (태그 → 정한 텍스트)",
    "open": "그룹 열기",
    "close": "그룹 닫기 (공통)",
    "toggle": "그룹 열기/닫기 (같은 태그)",
    "len_block": "길이 블록 (첫 필드 = 길이)",
    "count_block": "개수 블록 (첫 필드 = 항목 수)",
    "end": "해석 종료",
}

KEY_ACTIONS = ("value", "const")  # actions whose result can be the key of the next item

# Rule.items choices (group rules): how the items inside the group are shown.
ITEMS = {"": "괄호에 따름", "named": "key = 값", "values": "값만"}
# Rule.commas choices (group rules).
COMMAS = {"": "자동", "on": "쉼표", "off": "쉼표 없음"}

ENCODINGS = ["ascii", "utf-8", "cp949", "euc-kr", "latin-1", "cp1252", "utf-16-le", "utf-16-be",
             "shift_jis", "gbk", "big5"]

UNKNOWN_MODES = {"hex": "hex + ASCII (앞 32 byte)", "full": "hex + ASCII 전체 (16 byte씩 줄바꿈)",
                 "count": "byte 수만 표시", "stop": "해석 중단"}


@dataclass
class FieldSpec:
    type: str = "uint"
    name: str = ""
    size: int = 1
    endian: str = ""  # "" = rule set default
    encoding: str = ""  # "" = rule set default
    terminator: str = "00"
    len_size: str = "1"  # "1" | "2" | "4" | "varint"
    fmt: str = "dec"  # ints: dec | hex | both | bin ; unixtime: s | ms
    map: dict[str, str] = field(default_factory=dict)

    def summary(self) -> str:
        t = self.type
        if t in ("uint", "int"):
            s = f"{t}{self.size * 8}"
            if self.size > 1:
                s += "be" if self.endian == "big" else ("le" if self.endian == "little" else "")
        elif t == "float":
            s = f"float{self.size * 8}"
        elif t == "cstring":
            s = f"cstring(~{self.terminator or '00'})"
        elif t in ("fstring", "bytes", "bcd", "skip"):
            s = f"{t}[{self.size}]"
        elif t in ("pstring", "pbytes"):
            s = f"{t}(len{self.len_size})"
        elif t == "unixtime":
            s = f"unixtime{self.size * 8}{'ms' if self.fmt == 'ms' else ''}"
        else:
            s = t
        if self.encoding and t in ("cstring", "fstring", "pstring", "rest"):
            s += f" {self.encoding}"
        if self.map:
            s += " +이름표"
        return f"{self.name}:{s}" if self.name else s


@dataclass
class Rule:
    tag: str
    name: str = ""
    action: str = "value"
    fields: list[FieldSpec] = field(default_factory=list)
    enabled: bool = True
    comment: str = ""
    bracket: str = "["  # "[" or "{" for groups
    # "open" only: tag that closes this group. It is recognised only while this
    # group is the innermost one, so the same bytes can mean something else elsewhere.
    end_tag: str = ""
    # "value" only: the decoded text is the name (key) of the next item,
    # rendered as "key = value". A key followed by a key-rule item uses it as the value.
    key: bool = False
    # Group rules: "" follows the output option, "lines" one item per line, "inline" one line.
    layout: str = ""
    # "const" only: text shown for this tag instead of a value read from the data.
    text: str = ""
    # Group rules: how items are shown. "" by bracket ([ ] values only, { } key = value),
    # "named" names and key = value, "values" values only (keys are read as plain values).
    items: str = ""
    # Group rules: commas between items. "" automatic (value lists and one-line groups always,
    # otherwise the output option), "on" always, "off" never.
    commas: str = ""

    def fields_summary(self) -> str:
        s = ", ".join(f.summary() for f in self.fields)
        if self.action == "const":
            s = f"= {self.text}" + (f"  (건너뛰기: {s})" if s else "")
        if self.action == "open" and self.end_tag:
            s = f"{s}  닫는 태그 {self.end_tag}".strip()
        if self.action in KEY_ACTIONS and self.key:
            s = f"{s}  → 다음 값의 키".strip()
        if self.action != "value" and self.layout:
            s = f"{s}  [{'한 줄로' if self.layout == 'inline' else '줄마다'}]".strip()
        if self.action not in KEY_ACTIONS and self.items:
            s = f"{s}  [{ITEMS[self.items]}]".strip()
        if self.action not in KEY_ACTIONS and self.commas:
            s = f"{s}  [{COMMAS[self.commas]}]".strip()
        return s


@dataclass
class RuleSet:
    name: str = "새 규칙 세트"
    endian: str = "little"
    encoding: str = "ascii"
    unknown: str = "hex"
    rules: list[Rule] = field(default_factory=list)
    # Position-based structure: fields read from the start of the file before
    # tag scanning, and fixed-size fields at the very end. Tag rules only see
    # the bytes in between.
    header: list[FieldSpec] = field(default_factory=list)
    footer: list[FieldSpec] = field(default_factory=list)
    # Identification for the rule library: this rule set applies to files that
    # have `match_pattern` (tag syntax, ?? allowed) at `match_offset`.
    match_pattern: str = ""
    match_offset: int = 0
    # Groups nested deeper than this stop the decode. A wrong end tag otherwise opens a
    # new group at every opening tag and the tree (and its indented text) explodes.
    max_depth: int = 100

    def matches(self, data: bytes) -> bool:
        if not self.match_pattern.strip():
            return False
        try:
            pat = parse_tag(self.match_pattern)
        except ValueError:
            return False
        o = self.match_offset
        return o >= 0 and o + len(pat) <= len(data) and all(
            b is None or data[o + i] == b for i, b in enumerate(pat))

    # ---- (de)serialization ----
    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, d: dict) -> "RuleSet":
        rs = cls(**_known(cls, d, exclude={"rules", "header", "footer"}))
        for rd in d.get("rules", []):
            r = Rule(**_known(Rule, rd, exclude={"fields"}))
            r.fields = _fields(rd.get("fields", []))
            rs.rules.append(r)
        rs.header = _fields(d.get("header", []))
        rs.footer = _fields(d.get("footer", []))
        return rs

    @classmethod
    def load(cls, path: str | Path) -> "RuleSet":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")


def _fields(items: list[dict]) -> list[FieldSpec]:
    out = [FieldSpec(**_known(FieldSpec, fd)) for fd in items]
    for f in out:
        f.len_size = str(f.len_size)
        f.map = {str(k): str(v) for k, v in (f.map or {}).items()}
    return out


# Field types whose byte size is known up front (allowed in the footer).
FIXED_SIZE_TYPES = {"uint", "int", "float", "bool", "fstring", "bytes", "bcd", "skip", "unixtime"}


def fixed_size(fields_: list[FieldSpec]) -> int | None:
    """Total byte size of the fields, or None if any field is variable-length."""
    if any(f.type not in FIXED_SIZE_TYPES for f in fields_):
        return None
    return sum(f.size for f in fields_)


def _known(cls, d: dict, exclude: set[str] = frozenset()) -> dict:
    names = {f.name for f in fields(cls)} - set(exclude)
    return {k: v for k, v in d.items() if k in names}


# --------------------------------------------------------------------------
# Tag patterns
# --------------------------------------------------------------------------

def parse_tag(tag: str) -> list[int | None]:
    """'0A', '5F 20', '5F??', '"STR"' -> list of byte values (None = wildcard)."""
    t = tag.strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
        b = t[1:-1].encode("latin-1", errors="strict")
        if not b:
            raise ValueError("빈 문자열 태그")
        return list(b)
    t = re.sub(r"(0x|\s|,|-)", "", t, flags=re.I)
    if not t or len(t) % 2:
        raise ValueError(f"태그 '{tag}' 는 hex 두 자리 단위여야 합니다 (예: 09, 5F20, 5F??)")
    out: list[int | None] = []
    for i in range(0, len(t), 2):
        pair = t[i:i + 2]
        if pair == "??":
            out.append(None)
        elif re.fullmatch(r"[0-9A-Fa-f]{2}", pair):
            out.append(int(pair, 16))
        else:
            raise ValueError(f"태그 '{tag}' 에 잘못된 hex '{pair}'")
    if all(b is None for b in out):
        raise ValueError("태그가 전부 ?? 일 수는 없습니다")
    return out


def tag_text(pattern: list[int | None]) -> str:
    return " ".join("??" if b is None else f"{b:02X}" for b in pattern)


# --------------------------------------------------------------------------
# Field reading
# --------------------------------------------------------------------------

class _Ctx:
    def __init__(self, rs: RuleSet):
        self.endian = rs.endian
        self.encoding = rs.encoding


def _decode_text(b: bytes, enc: str) -> str:
    try:
        return b.decode(enc, errors="backslashreplace")
    except LookupError:
        raise DecodeError(f"알 수 없는 인코딩 '{enc}'")


def _len(r: Reader, f: FieldSpec, endian: str) -> int:
    if f.len_size == "varint":
        return r.varint()
    return r.uint(int(f.len_size), endian)


def _int_text(v: int, f: FieldSpec, size: int) -> str:
    if f.map:
        name = f.map.get(str(v)) or f.map.get(f"0x{v:X}") or f.map.get(f"0x{v:0{size * 2}X}")
        if name is not None:
            return f"{name} ({v})"
    if f.fmt == "hex":
        return f"0x{v & ((1 << (size * 8)) - 1):0{size * 2}X}"
    if f.fmt == "both":
        return f"{v} (0x{v & ((1 << (size * 8)) - 1):0{size * 2}X})"
    if f.fmt == "bin":
        return f"0b{v & ((1 << (size * 8)) - 1):0{size * 8}b}"
    return str(v)


def read_field(r: Reader, f: FieldSpec, ctx: _Ctx) -> tuple[str | None, int | None]:
    """Read one field. Returns (display text or None for skip, numeric value or None)."""
    endian = f.endian or ctx.endian
    enc = f.encoding or ctx.encoding
    t = f.type
    if t in ("uint", "int"):
        if not 1 <= f.size <= 16:
            raise DecodeError(f"정수 크기 {f.size} 는 1~16 이어야 합니다")
        v = r.uint(f.size, endian) if t == "uint" else r.sint(f.size, endian)
        return _int_text(v, f, f.size), v
    if t == "float":
        return float_text(r.read(f.size), endian), None
    if t == "bool":
        v = r.uint(f.size or 1, endian)
        return ("true" if v else "false") if not f.map else _int_text(v, f, f.size or 1), v
    if t == "cstring":
        term = bytes.fromhex(f.terminator or "00")
        if len(term) == 2 and enc.startswith("utf-16"):
            start = r.pos
            p = start
            while True:
                r.need(p + 2 - r.pos)
                if r.data[p:p + 2] == term:
                    break
                p += 2
            b = r.data[start:p]
            r.pos = p + 2
        else:
            b, _ = r.cstring(term)
        return quote(_decode_text(b, enc)), None
    if t == "fstring":
        b = r.read(f.size).rstrip(b"\x00")
        return quote(_decode_text(b, enc)), None
    if t == "pstring":
        n = _len(r, f, endian)
        return quote(_decode_text(r.read(n), enc)), None
    if t == "bytes":
        return hexs(r.read(f.size), 256), None
    if t == "pbytes":
        n = _len(r, f, endian)
        return f"<{n} byte> {hexs(r.read(n), 256)}", None
    if t == "bcd":
        digits = r.read(f.size).hex().upper().rstrip("F")
        return digits, int(digits) if digits.isdigit() else None
    if t == "varint":
        v = r.varint()
        return _int_text(v, f, 8), v
    if t == "svarint":
        v = zigzag(r.varint())
        return _int_text(v, f, 8), v
    if t == "unixtime":
        v = r.uint(f.size, endian)
        return unix_time(v / 1000 if f.fmt == "ms" else v), v
    if t == "skip":
        r.read(f.size)
        return None, None
    if t == "rest":
        b = r.read(r.remaining)
        s = _decode_text(b, enc)
        if b and all(0x20 <= c < 0x7F or c in (9, 10, 13) for c in b):
            return quote(s), None
        return f"<{len(b)} byte> {hexs(b, 256)}", None
    raise DecodeError(f"알 수 없는 필드 타입 '{t}'")


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------

@dataclass
class _Frame:
    node: Node
    rule: Rule | None
    limit: int | None = None  # len_block end offset
    remaining: int | None = None  # count_block items left
    end_pat: list[int | None] | None = None  # open rule's own closing tag
    key_pending: bool = False  # last item was a key; the next item is its value

    @property
    def plain(self) -> bool:
        """Closed by a generic "close" rule (not a block, no own end tag)."""
        return self.limit is None and self.remaining is None and self.end_pat is None


def _pat_at(pat: list[int | None], data: bytes, pos: int, end: int) -> bool:
    return pos + len(pat) <= end and all(b is None or data[pos + i] == b for i, b in enumerate(pat))


MAX_NODES = 1_000_000


class RuleEngine:
    def __init__(self, ruleset: RuleSet):
        self.rs = ruleset
        self.ctx = _Ctx(ruleset)
        self.errors: list[str] = []
        compiled = []
        self.end_pats: dict[int, list[int | None]] = {}
        for r in ruleset.rules:
            if not r.enabled:
                continue
            try:
                pat = parse_tag(r.tag)
                if r.action == "open" and r.end_tag.strip():
                    self.end_pats[id(r)] = parse_tag(r.end_tag)
            except ValueError as e:
                self.errors.append(f"규칙 '{r.name or r.tag}': {e}")
                continue
            compiled.append((pat, r))
        # Longest and most specific first.
        compiled.sort(key=lambda pr: (-len(pr[0]), sum(b is None for b in pr[0])))
        self.by_first: dict[int, list] = {}
        self.wild_first: list = []
        for pat, r in compiled:
            if pat[0] is None:
                self.wild_first.append((pat, r))
            else:
                self.by_first.setdefault(pat[0], []).append((pat, r))
        for k in self.by_first:
            self.by_first[k] = sorted(self.by_first[k] + self.wild_first,
                                      key=lambda pr: (-len(pr[0]), sum(b is None for b in pr[0])))
        firsts = bytes(sorted(self.by_first))
        self._next_re = re.compile(b"[" + b"".join(re.escape(bytes([c])) for c in firsts) + b"]") \
            if firsts and not self.wild_first else None

    def match(self, data: bytes, pos: int, end: int):
        cands = self.by_first.get(data[pos], self.wild_first)
        for pat, rule in cands:
            n = len(pat)
            if pos + n > end:
                continue
            if all(b is None or data[pos + i] == b for i, b in enumerate(pat)):
                return rule, n
        return None

    def _next_candidate(self, data: bytes, pos: int, end: int, end_pat=None) -> int:
        """Next position where a rule (or the open group's end tag) could match."""
        if self._next_re is None:
            return pos
        m = self._next_re.search(data, pos, end)
        nxt = m.start() if m else end
        if end_pat is not None:
            if end_pat[0] is None:
                return pos
            e = data.find(bytes([end_pat[0]]), pos, end)
            if 0 <= e < nxt:
                nxt = e
        return nxt

    def decode(self, data: bytes, max_nodes: int = MAX_NODES) -> Node:
        root = Node(0, len(data), GROUP, self.rs.name or "사용자 규칙")
        for e in self.errors:
            root.add(Node(0, 0, ERROR, e))
        if not self.by_first and not self.wild_first and not self.rs.header and not self.rs.footer:
            root.add(Node(0, 0, INFO, "활성화된 규칙이 없습니다. 규칙을 추가하면 바로 해석됩니다."))
        stack = [_Frame(root, None)]
        pos, unk_start, nodes = 0, None, 0
        # Tag rules run over data[pos:n]: after the header, before the footer.
        if self.rs.header:
            pos = self._fixed_part(root, data, 0, len(data), self.rs.header, "헤더")
        n = len(data)
        footer_at = None
        if self.rs.footer:
            size = fixed_size(self.rs.footer)
            if size is None:
                root.add(Node(0, 0, ERROR, "꼬리에는 고정 크기 필드만 쓸 수 있습니다",
                              "정수/실수/불리언/고정 문자열/바이트/BCD/Unix 시각/건너뛰기"))
            elif len(data) - size < pos:
                root.add(Node(0, 0, ERROR, f"파일이 헤더+꼬리({size} byte)보다 짧습니다"))
            else:
                footer_at = n = len(data) - size

        key_nodes: set[int] = set()  # id() of nodes used as keys, paired with their value at the end

        def flush(upto: int) -> None:
            nonlocal unk_start
            if unk_start is not None and upto > unk_start:
                stack[-1].node.add(self._unknown(data, unk_start, upto, f"{upto - unk_start} byte"))
                stack[-1].key_pending = False  # unexplained bytes break a key/value pair
            unk_start = None

        def pop_frame(at: int, note: str = "") -> None:
            fr = stack.pop()
            fr.node.length = at - fr.node.offset
            if note:
                fr.node.note = note

        def item_done(at: int) -> None:
            # One complete item now sits in the top frame: count_block bookkeeping.
            # A finished block is itself an item of its parent, hence the loop.
            while stack[-1].remaining is not None:
                stack[-1].remaining -= 1
                if stack[-1].remaining > 0:
                    return
                pop_frame(at)

        while pos < n and nodes < max_nodes:
            fr = stack[-1]
            end = fr.limit if fr.limit is not None else n
            if pos >= end:
                flush(end)
                pop_frame(pos)
                item_done(pos)
                continue
            if fr.end_pat is not None and _pat_at(fr.end_pat, data, pos, end):
                # The innermost group's own end tag wins over every other rule.
                flush(pos)
                pos += len(fr.end_pat)
                pop_frame(pos)
                item_done(pos)
                continue
            m = self.match(data, pos, end)
            if m is None:
                if self.rs.unknown == "stop":
                    flush(pos)
                    fr.node.add(Node(pos, 0, ERROR, f"규칙에 없는 바이트 0x{data[pos]:02X}", "해석 중단"))
                    break
                if unk_start is None:
                    unk_start = pos
                pos = max(pos + 1, self._next_candidate(data, pos + 1, end, fr.end_pat))
                continue
            flush(pos)
            rule, taglen = m
            nodes += 1
            try:
                new, event = self._apply(rule, data, pos, taglen, end, stack)
            except DecodeError as e:
                fr.node.add(Node(pos, 0, ERROR, f"규칙 '{rule.name or rule.tag}' 적용 실패", str(e)))
                fr.key_pending = False
                unk_start = pos
                pos += 1
                continue
            pos = new
            if event == "open" and len(stack) - 1 > self.rs.max_depth:
                stack[-1].node.note = "최대 깊이 초과"
                fr.node.add(Node(pos, 0, ERROR, f"그룹이 {self.rs.max_depth}단계보다 깊어져 해석을 멈춥니다",
                                 "그룹을 닫는 태그나 그룹 규칙이 맞는지 확인하세요 (규칙 세트의 '최대 그룹 깊이')"))
                break
            if (event == "item" and rule.key and rule.action in KEY_ACTIONS and not fr.key_pending
                    and not fr.node.values_only):  # value-only groups (arrays): keys are plain values
                # A key and its value count as one item (count_block).
                key_nodes.add(id(fr.node.children[-1]))
                fr.key_pending = True
                continue
            fr.key_pending = False
            if event == "item":
                item_done(pos)
            elif isinstance(event, int):  # close the plain group at stack index `event`
                while len(stack) - 1 > event:
                    pop_frame(pos, "그룹 닫기로 함께 닫힘")
                pop_frame(pos)
                item_done(pos)
            elif event == "end":
                break
        flush(min(pos, n))
        if nodes >= max_nodes and pos < n:
            root.add(Node(pos, 0, ERROR, f"항목이 {max_nodes:,}개를 넘어 해석을 멈춥니다",
                          "규칙이 너무 잘게 맞고 있지 않은지 확인하세요"))
        if pos < n:
            root.add(self._unknown(data, pos, n, f"{n - pos} byte (해석 중단 이후)"))
        while len(stack) > 1:
            pop_frame(min(pos, n), "닫히지 않음")
        if footer_at is not None:
            self._fixed_part(root, data, footer_at, len(data), self.rs.footer, "꼬리")
        if key_nodes:
            _pair_keys(root, key_nodes)
        return root

    def _fixed_part(self, root: Node, data: bytes, start: int, end: int,
                    specs: list[FieldSpec], label: str) -> int:
        """Read header/footer fields in order; returns the position after them."""
        g = root.add(Node(start, 0, GROUP, label, close="}", part="header" if label == "헤더" else "footer"))
        r = Reader(data, start, end)
        for i, f in enumerate(specs):
            fpos = r.pos
            try:
                text, _ = read_field(r, f, self.ctx)
            except DecodeError as e:
                g.add(Node(fpos, 0, ERROR, f"{label} 필드 {i + 1} 읽기 실패", str(e)))
                break
            if text is not None:
                g.add(Node(fpos, r.pos - fpos, VALUE, f.name or f"필드 {i + 1}", text))
        g.length = r.pos - start
        return r.pos

    def _unknown(self, data: bytes, start: int, end: int, label: str) -> Node:
        """A run of bytes no rule explained, shown per the rule set's unknown mode."""
        b = data[start:end]
        mode = self.rs.unknown
        if mode == "count":
            return Node(start, len(b), UNKNOWN, label)
        if mode != "full":
            return Node(start, len(b), UNKNOWN, label, f"{hexs(b, 32)}  |{ascii_preview(b, 32)}|")
        # Full dump: one child line per 16 bytes. Children are plain values so
        # the parent's UNKNOWN span is still what counts as unexplained.
        node = Node(start, len(b), UNKNOWN, label)
        for o in range(0, len(b), 16):
            chunk = b[o:o + 16]
            node.add(Node(start + o, len(chunk), VALUE, "",
                          f"{chunk.hex(' ').upper():<47}  |{ascii_preview(chunk, 16)}|"))
        return node

    def _read_fields(self, r: Reader, specs: list[FieldSpec]) -> tuple[str, list[int | None]]:
        parts, nums = [], []
        for f in specs:
            text, num = read_field(r, f, self.ctx)
            nums.append(num)
            if text is not None:
                parts.append(f"{f.name}={text}" if f.name and len(specs) > 1 else text)
        return ", ".join(parts), nums

    def _apply(self, rule: Rule, data: bytes, pos: int, taglen: int, end: int,
               stack: list[_Frame]) -> tuple[int, object]:
        """Apply one matched rule. Returns (new position, event).

        event: "item" (a complete item was added), "open", "end", None, or an
        int stack index of the plain group to close.
        """
        a = rule.action
        r = Reader(data, pos + taglen, end)
        label = rule.name or f"태그 {rule.tag}"
        parent = stack[-1].node
        close = "}" if rule.bracket == "{" else "]"

        if a == "value":
            text, _ = self._read_fields(r, rule.fields)
            parent.add(Node(pos, r.pos - pos, VALUE, label, text))
            return r.pos, "item"
        if a == "const":
            self._read_fields(r, rule.fields)  # fields of a const rule are only skipped
            parent.add(Node(pos, r.pos - pos, VALUE, label, rule.text))
            return r.pos, "item"
        if a == "end":
            parent.add(Node(pos, taglen, INFO, label, "해석 종료"))
            return r.pos, "end"
        if a == "toggle":
            top = stack[-1]
            if top.rule is rule and top.plain:
                return r.pos, len(stack) - 1
            a = "open"
        if a == "open":
            text, _ = self._read_fields(r, rule.fields)
            g = parent.add(Node(pos, r.pos - pos, GROUP, label, text, close=close, layout=rule.layout, items=rule.items,
                                commas=rule.commas))
            stack.append(_Frame(g, rule, end_pat=self.end_pats.get(id(rule)) if rule.action == "open" else None))
            return r.pos, "open"
        if a == "close":
            text, _ = self._read_fields(r, rule.fields)
            for i in range(len(stack) - 1, 0, -1):
                if stack[i].plain:
                    if text:
                        stack[i].node.value = f"{stack[i].node.value} {text}".strip()
                    return r.pos, i
            parent.add(Node(pos, r.pos - pos, ERROR, f"'{label}': 닫을 그룹이 없습니다"))
            return r.pos, None
        if a in ("len_block", "count_block"):
            if not rule.fields or rule.fields[0].type not in ("uint", "varint", "bcd"):
                raise DecodeError("블록 규칙의 첫 필드는 길이/개수(uint/varint/bcd) 여야 합니다")
            _, nums = self._read_fields(r, rule.fields[:1])
            count = nums[0] or 0
            text, _ = self._read_fields(r, rule.fields[1:])
            desc = (f"(길이 {count})" if a == "len_block" else f"({count}개)") + (f" {text}" if text else "")
            g = parent.add(Node(pos, r.pos - pos, GROUP, label, desc, close=close, layout=rule.layout, items=rule.items,
                                commas=rule.commas))
            if count == 0:
                return r.pos, "item"
            if a == "len_block":
                if r.pos + count > end:
                    g.note = "길이가 범위를 벗어납니다"
                stack.append(_Frame(g, rule, limit=min(r.pos + count, end)))
            else:
                stack.append(_Frame(g, rule, remaining=count))
            return r.pos, "open"
        raise DecodeError(f"알 수 없는 동작 '{a}'")


def _pair_keys(root: Node, key_nodes: set[int]) -> None:
    """Merge each key node into the item right after it: label = key, span = key..value."""
    for g in [n for n in root.walk() if n.kind == GROUP]:
        out, kids, i = [], g.children, 0
        while i < len(kids):
            k = kids[i]
            if id(k) in key_nodes and i + 1 < len(kids) and kids[i + 1].kind in (VALUE, GROUP):
                v = kids[i + 1]
                name = k.value
                if len(name) > 2 and name[0] == name[-1] == '"':
                    name = name[1:-1]
                # An empty key stays visible as "" (falling back to the rule name would look like a real key).
                v.label, v.key = name or '""', True
                v.length, v.offset = v.end - k.offset, k.offset
                out.append(v)
                i += 2
                continue
            out.append(k)  # unpaired key (value missing): stays a normal item
            i += 1
        g.children = out


def decode_with_rules(data: bytes, ruleset: RuleSet) -> Node:
    return RuleEngine(ruleset).decode(data)


def example_ruleset() -> RuleSet:
    """The example from the original request."""
    return RuleSet(
        name="예시 규칙",
        rules=[
            Rule("09", "문자열", "value", [FieldSpec("cstring", terminator="00")],
                 comment="09 다음부터 00 까지 ASCII 문자열"),
            Rule("04", "정수", "value", [FieldSpec("uint", size=1)], comment="04 다음 1 byte 정수"),
            Rule("0A", "배열", "toggle", [], comment="0A 로 열고 다시 0A 로 닫음"),
        ],
    )
