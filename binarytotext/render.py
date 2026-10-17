"""Render a decoded Node tree as human-readable text (or JSON)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

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
    quotes: bool = True  # False: strings without quotes (an empty string becomes nothing)


def render_text(root: Node, data: bytes, opts: RenderOptions | None = None,
                line_nodes: list[Node] | None = None) -> str:
    """line_nodes, if given, receives the node behind each output line."""
    opts = opts or RenderOptions()
    out: list[str] = []

    def put(n: Node, line: str) -> None:
        out.append(line)
        if line_nodes is not None:
            line_nodes.append(n)
    width = max(4, len(f"{len(data):X}"))

    def prefix(n: Node) -> str:
        return f"[{n.offset:0{width}X}] " if opts.offsets else ""

    def raw(n: Node) -> str:
        if not opts.raw_hex or n.length <= 0:
            return ""
        b = data[n.offset:n.offset + min(n.length, 16)]
        more = " …" if n.length > 16 else ""
        return f"    ; {b.hex(' ').upper()}{more}"

    def plain_list(n: Node) -> bool:
        """Children are leaf values that share one label (or are all keyed): labels add nothing."""
        kids = n.children
        return all(c.kind == VALUE and not c.children for c in kids) and (
            all(c.key for c in kids) or len({c.label for c in kids if not c.key}) <= 1)

    def is_inline(n: Node) -> bool:
        if not n.children:
            return False
        mode = n.layout or opts.layout
        if mode == "inline":
            return True
        # "arrays": the old one-line array rule, only for [ ] lists of plain values.
        return mode == "arrays" and n.close == "]" and (
            all(c.kind == VALUE and not c.children for c in n.children) and (not opts.labels or plain_list(n)))

    def val(n: Node) -> str:
        return n.value if opts.quotes else _strip_quotes(n.value)

    def key_name(n: Node) -> str:
        if opts.quotes:
            return n.label
        return "" if n.label == '""' else _unescape(n.label)

    def joined(left: str, sep: str, right: str) -> str:
        return f"{left}{sep} {right}" if right else f"{left}{sep}"  # empty string: nothing after the separator

    def head_of(n: Node) -> str:
        v = val(n)
        if n.key:
            return joined(key_name(n), " =", v)
        if opts.labels:
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
        if n.key:
            return joined(key_name(n), " =", v)
        if n.value == "":  # a value-less rule only has its name to show
            return n.label
        if not opts.labels or drop_label:
            return v
        return joined(n.label, ":", v) if n.label else v

    def one_line(n: Node, drop_label: bool = False) -> str:
        """Whole node (and everything inside) as one piece of text."""
        if n.kind != GROUP:
            return leaf_text(n, drop_label)
        head = head_of(n)
        drop = n.close == "]" and plain_list(n)  # a list drops repeated labels, an object keeps them
        body = ", ".join(one_line(c, drop) for c in n.children)
        return f"{head + ' ' if head else ''}{_opener(n.close)}{body}{n.close}"

    def emit(n: Node, depth: int, tail: str = "") -> bool:
        """tail: text right after the item (the separating comma)."""
        if opts.max_lines and len(out) >= opts.max_lines:
            return False
        pad = opts.indent * depth
        note = f"  ({n.note})" if n.note else ""
        if n.kind == GROUP:
            if is_inline(n):
                put(n, f"{prefix(n)}{pad}{one_line(n)}{tail}{note}")
                return True
            head = head_of(n)
            opener = _opener(n.close)
            put(n, f"{prefix(n)}{pad}{head + ' ' if head else ''}{opener}{'' if n.close else tail}{note}".rstrip())
            last = len(n.children) - 1
            for i, c in enumerate(n.children):
                if not emit(c, depth + 1, "," if opts.commas and i < last else ""):
                    return False
            if n.close:
                put(n, f"{' ' * len(prefix(n))}{pad}{n.close}{tail}")
            return True
        put(n, f"{prefix(n)}{pad}{leaf_text(n)}{tail}{note}{raw(n)}")
        for c in n.children:
            if not emit(c, depth + 1):
                return False
        return True

    for c in root.children:
        if not emit(c, 0):
            put(root, f"… 출력이 {opts.max_lines}줄에서 잘렸습니다 (텍스트 저장 시에는 전체가 저장됩니다)")
            break
    return "\n".join(out) + ("\n" if out else "")


_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')
_QUOTE_ESCAPE = re.compile(r'\\(["\\])')


def _unescape(s: str) -> str:
    """\\" -> " and \\\\ -> \\. Control escapes (\\n, \\t, \\0) stay visible so an item stays on one line."""
    return _QUOTE_ESCAPE.sub(r"\1", s)


def _strip_quotes(v: str) -> str:
    """Remove the quotes of every quoted string in a value: '"a", 3, ""' -> 'a, 3, '."""
    return _QUOTED.sub(lambda m: _unescape(m.group(1)), v)


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


def render_json(root: Node) -> str:
    return json.dumps(to_dict(root), ensure_ascii=False, indent=2)
