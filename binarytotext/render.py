"""Render a decoded Node tree as human-readable text (or JSON)."""

from __future__ import annotations

import json
from dataclasses import dataclass

from .model import ERROR, GROUP, INFO, UNKNOWN, Node


@dataclass
class RenderOptions:
    offsets: bool = True
    raw_hex: bool = False
    indent: str = "  "
    max_lines: int = 0  # 0 = unlimited


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

    def emit(n: Node, depth: int) -> bool:
        if opts.max_lines and len(out) >= opts.max_lines:
            return False
        pad = opts.indent * depth
        note = f"  ({n.note})" if n.note else ""
        if n.kind == GROUP:
            head = n.label + (f" {n.value}" if n.value else "")
            opener = _opener(n.close)
            put(n, f"{prefix(n)}{pad}{head} {opener}{note}".rstrip())
            for c in n.children:
                if not emit(c, depth + 1):
                    return False
            if n.close:
                put(n, f"{' ' * len(prefix(n))}{pad}{n.close}")
            return True
        if n.kind == UNKNOWN:
            line = f"?? {n.label}: {n.value}"
        elif n.kind == ERROR:
            line = f"!! 오류: {n.label}" + (f" — {n.value}" if n.value else "")
        elif n.kind == INFO:
            line = f"# {n.label}" + (f": {n.value}" if n.value else "")
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


def _opener(close: str) -> str:
    return {"]": "[", "}": "{", ")": "(", ">": "<"}.get(close, "")


def to_dict(n: Node) -> dict:
    d = {"offset": n.offset, "length": n.length, "kind": n.kind, "label": n.label}
    if n.value != "":
        d["value"] = n.value
    if n.note:
        d["note"] = n.note
    if n.children:
        d["children"] = [to_dict(c) for c in n.children]
    return d


def render_json(root: Node) -> str:
    return json.dumps(to_dict(root), ensure_ascii=False, indent=2)
