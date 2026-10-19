"""Render a decoded Node tree as human-readable text (or JSON)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .model import ERROR, GROUP, INFO, UNKNOWN, VALUE, Node


LAYOUTS = {"lines": "줄마다", "arrays": "배열만 한 줄", "inline": "한 줄로"}


@dataclass
class RenderOptions:
    offsets: bool = True
    raw_hex: bool = False
    indent: str = "  "
    max_lines: int = 0  # 0 = unlimited
    labels: bool = True  # False: values only (keys read from the data are kept)
    # Group layout default: "lines" one item per line, "arrays" [ ] groups of plain values
    # on one line, "inline" every group on one line. A group's own Node.layout overrides it.
    layout: str = "lines"
    commas: bool = False  # "," after every item but the last inside a multi-line group
    quotes: bool = True  # False: strings without quotes, unless empty or holding blanks/special characters
    # Significant digits of float/double values; 0 = shortest exact form. Applied while
    # decoding (values are text in the tree): wrap the decode in reader.float_digits().
    float_digits: int = 0
    header_footer: bool = True  # False: leave out the rule set's header/footer groups (Node.part)


def render_text(root: Node, data: bytes, opts: RenderOptions | None = None,
                line_nodes: list[Node] | None = None) -> str:
    """line_nodes, if given, receives the node behind each output line."""
    out: list[str] = []
    for n, line in iter_lines(root, data, opts):
        out.append(line)
        if line_nodes is not None:
            line_nodes.append(n)
    return "\n".join(out) + ("\n" if out else "")


def write_text(path: str | Path, root: Node, data: bytes, opts: RenderOptions | None = None) -> int:
    """Stream the text to a file (no giant string in memory). Returns the number of lines."""
    count = 0
    with _atomic(path) as f:
        for _, line in iter_lines(root, data, opts):
            f.write(line)
            f.write("\n")
            count += 1
    return count


def write_json(path: str | Path, root: Node, opts: RenderOptions | None = None) -> None:
    with _atomic(path) as f:
        for chunk in json.JSONEncoder(ensure_ascii=False, indent=2).iterencode(_json_tree(root, opts)):
            f.write(chunk)


class _atomic:
    """Write to "<path>.part" and replace the target only on success: no half-written output."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.tmp = self.path.with_name(self.path.name + ".part")

    def __enter__(self):
        self.f = open(self.tmp, "w", encoding="utf-8")
        return self.f

    def __exit__(self, exc_type, *_):
        self.f.close()
        if exc_type is None:
            os.replace(self.tmp, self.path)
        else:
            self.tmp.unlink(missing_ok=True)
        return False


def iter_lines(root: Node, data: bytes, opts: RenderOptions | None = None) -> Iterator[tuple[Node, str]]:
    """(node, line) for every output line; stops after opts.max_lines with a note line."""
    opts = opts or RenderOptions()
    lines = 0
    width = max(4, len(f"{len(data):X}"))

    def prefix(n: Node) -> str:
        return f"[{n.offset:0{width}X}] " if opts.offsets else ""

    def raw(n: Node) -> str:
        if not opts.raw_hex or n.length <= 0:
            return ""
        b = data[n.offset:n.offset + min(n.length, 16)]
        more = " …" if n.length > 16 else ""
        return f"    ; {b.hex(' ').upper()}{more}"

    def is_inline(n: Node) -> bool:
        if not n.children:
            return False
        mode = n.layout or opts.layout
        if mode == "inline":
            return True
        # "arrays": value-only groups ([ ] unless the rule says otherwise) of plain values on one line.
        return mode == "arrays" and n.values_only and all(c.kind == VALUE and not c.children for c in n.children)

    def val(n: Node) -> str:
        return n.value if opts.quotes else _strip_quotes(n.value)

    def key_name(n: Node) -> str:
        if opts.quotes:
            return n.label
        return n.label if n.label == '""' else _bare(n.label)  # an empty key stays ""

    def joined(left: str, sep: str, right: str) -> str:
        return f"{left}{sep} {right}" if right else f"{left}{sep}"  # empty string: nothing after the separator

    def head_of(n: Node, drop_label: bool = False) -> str:
        v = val(n)
        if n.key and not drop_label:
            return joined(key_name(n), " =", v)
        if opts.labels and not drop_label:
            return n.label + (f" {v}" if v else "")
        return v

    def leaf_text(n: Node, drop_label: bool = False) -> str:
        if n.kind == UNKNOWN:
            return f"?? {n.label}" + (f": {n.value}" if n.value else "")
        if n.kind == ERROR:
            return f"!! 오류: {n.label}" + (f" — {n.value}" if n.value else "")
        if n.kind == INFO:
            return f"# {n.label}" + (f": {n.value}" if n.value else "")
        v = val(n)
        if n.key and not drop_label:
            return joined(key_name(n), " =", v)
        if n.value == "":  # a value-less rule only has its name to show
            return n.label
        if not opts.labels or drop_label:
            return v
        return joined(n.label, ":", v) if n.label else v

    def one_line(n: Node, drop_label: bool = False) -> str:
        """Whole node (and everything inside) as one piece of text. drop_label: an array item."""
        if n.kind != GROUP:
            return leaf_text(n, drop_label)
        head = head_of(n, drop_label)
        body = ", ".join(one_line(c, n.values_only) for c in n.children)
        return f"{head + ' ' if head else ''}{_opener(n.close)}{body}{n.close}"

    def emit(n: Node, depth: int, tail: str = "", item: bool = False):
        """Yields (node, line); returns False once max_lines is reached.

        tail: the separating comma. item: n is an element of a [ ] array (value only, no name or key).
        """
        nonlocal lines
        if opts.max_lines and lines >= opts.max_lines:
            return False
        pad = opts.indent * depth
        note = f"  ({n.note})" if n.note else ""
        lines += 1
        if n.kind == GROUP:
            if is_inline(n):
                yield n, f"{prefix(n)}{pad}{one_line(n, item)}{tail}{note}"
                return True
            head = head_of(n, item)
            opener = _opener(n.close)
            yield n, f"{prefix(n)}{pad}{head + ' ' if head else ''}{opener}{'' if n.close else tail}{note}".rstrip()
            array = n.values_only
            last = len(n.children) - 1
            for i, c in enumerate(n.children):
                comma = "," if (opts.commas or array) and i < last else ""  # value lists always separate items
                if not (yield from emit(c, depth + 1, comma, array)):
                    return False
            if n.close:
                lines += 1
                yield n, f"{' ' * len(prefix(n))}{pad}{n.close}{tail}"
            return True
        yield n, f"{prefix(n)}{pad}{leaf_text(n, item)}{tail}{note}{raw(n)}"
        for c in n.children:
            if not (yield from emit(c, depth + 1)):
                return False
        return True

    for c in root.children:
        if c.part and not opts.header_footer:
            continue
        if not (yield from emit(c, 0)):
            yield root, f"… 출력이 {opts.max_lines:,}줄에서 잘렸습니다 (결과 위 '화면 줄 수' 로 늘릴 수 있고, 저장 파일에는 전체가 들어갑니다)"
            break


_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')

# Text that would be ambiguous without quotes: blanks, the output's own separators, quotes and escapes.
_NEEDS_QUOTES = re.compile(r"""[\s,=:;\[\]{}()<>#"'\\]""")


def _bare(escaped: str) -> str:
    """Quoted string content -> bare text, or kept in quotes if empty or blanks/special characters make it
    ambiguous."""
    return f'"{escaped}"' if not escaped or _NEEDS_QUOTES.search(escaped) else escaped


def _strip_quotes(v: str) -> str:
    """Drop the quotes of every plain quoted string in a value: '"a", 3, "", "b c"' -> 'a, 3, , "b c"'."""
    return _QUOTED.sub(lambda m: _bare(m.group(1)), v)


def _opener(close: str) -> str:
    return {"]": "[", "}": "{", ")": "(", ">": "<"}.get(close, "")


def to_dict(n: Node) -> dict:
    d = {"offset": n.offset, "length": n.length, "kind": n.kind, "label": n.label}
    if n.value != "":
        d["value"] = n.value
    if n.note:
        d["note"] = n.note
    if n.key:
        d["key"] = True
    if n.children:
        d["children"] = [to_dict(c) for c in n.children]
    return d


def _json_tree(root: Node, opts: RenderOptions | None) -> dict:
    d = to_dict(root)
    if opts is not None and not opts.header_footer and "children" in d:
        d["children"] = [cd for c, cd in zip(root.children, d["children"]) if not c.part]
    return d


def render_json(root: Node, opts: RenderOptions | None = None) -> str:
    return json.dumps(_json_tree(root, opts), ensure_ascii=False, indent=2)
