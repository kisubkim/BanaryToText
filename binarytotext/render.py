"""Render a decoded Node tree as human-readable text (or JSON)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .model import ERROR, GROUP, INFO, UNKNOWN, VALUE, Node


@dataclass
class RenderOptions:
    offsets: bool = True
    raw_hex: bool = False
    indent: str = "  "
    max_lines: int = 0  # 0 = unlimited
    labels: bool = True  # False: values only (keys read from the data are kept)
    inline_arrays: bool = False  # [ ] groups of plain values on one line: "[21, 22, 24]"


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

    def item(n: Node) -> str:
        """A value inside a one-line array: labels dropped, keys kept."""
        v = n.value if n.value != "" else n.label
        return f"{n.label} = {_unquote(v)}" if n.key else v

    def inline(n: Node) -> bool:
        kids = n.children
        if not (opts.inline_arrays and n.close == "]" and kids
                and all(c.kind == VALUE and not c.children for c in kids)):
            return False
        # With labels shown, only collapse when the items share one label (a plain list).
        return not opts.labels or all(c.key for c in kids) or len({c.label for c in kids}) == 1

    def emit(n: Node, depth: int) -> bool:
        if opts.max_lines and len(out) >= opts.max_lines:
            return False
        pad = opts.indent * depth
        note = f"  ({n.note})" if n.note else ""
        if n.kind == GROUP:
            if n.key:
                head = f"{n.label} =" + (f" {n.value}" if n.value else "")
            elif opts.labels:
                head = n.label + (f" {n.value}" if n.value else "")
            else:
                head = n.value
            lead = f"{prefix(n)}{pad}{head + ' ' if head else ''}{_opener(n.close)}"
            if inline(n):
                put(n, f"{lead}{', '.join(item(c) for c in n.children)}{n.close}{note}")
                return True
            put(n, f"{lead}{note}".rstrip())
            for c in n.children:
                if not emit(c, depth + 1):
                    return False
            if n.close:
                put(n, f"{' ' * len(prefix(n))}{pad}{n.close}")
            return True
        if n.kind == UNKNOWN:
            line = f"?? {n.label}" + (f": {n.value}" if n.value else "")
        elif n.kind == ERROR:
            line = f"!! 오류: {n.label}" + (f" — {n.value}" if n.value else "")
        elif n.kind == INFO:
            line = f"# {n.label}" + (f": {n.value}" if n.value else "")
        elif n.key:
            line = f"{n.label} = {_unquote(n.value)}"
        elif not opts.labels:
            line = n.value if n.value != "" else n.label  # a value-less rule only has its name to show
        else:
            line = f"{n.label}: {n.value}" if n.value != "" and n.label else (n.label or n.value)
        put(n, f"{prefix(n)}{pad}{line}{note}{raw(n)}")
        for c in n.children:
            if not emit(c, depth + 1):
                return False
        return True

    for c in root.children:
        if not emit(c, 0):
            put(root, f"… 출력이 {opts.max_lines}줄에서 잘렸습니다 (텍스트 저장 시에는 전체가 저장됩니다)")
            break
    return "\n".join(out) + ("\n" if out else "")


_ONE_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')


def _unquote(v: str) -> str:
    """key = "text" -> key = text. Only a single quoted string; escapes stay visible."""
    return v[1:-1] if _ONE_STRING.fullmatch(v) else v


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
