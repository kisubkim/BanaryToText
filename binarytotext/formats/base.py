"""Decoder interface and registry for built-in (known) formats."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..model import ERROR, GROUP, Node
from ..reader import DecodeError


@dataclass
class Option:
    key: str
    label: str
    kind: str  # "choice" | "int" | "bool"
    default: Any
    choices: list[tuple[Any, str]] = field(default_factory=list)
    minimum: int = 0
    maximum: int = 1 << 20


@dataclass
class Decoder:
    key: str
    title: str
    family: str
    description: str
    func: Callable[[bytes, dict], Node]
    options: list[Option] = field(default_factory=list)
    # Option variants the auto-detector also tries (besides the defaults).
    variants: list[dict] = field(default_factory=list)
    # A file normally holds one top-level object; many top-level items then
    # mean the decoder is just accepting noise (used by the auto-detector).
    single_root: bool = False

    def defaults(self) -> dict:
        return {o.key: o.default for o in self.options}

    def decode(self, data: bytes, opts: dict | None = None) -> Node:
        merged = self.defaults()
        merged.update(opts or {})
        return self.func(data, merged)


REGISTRY: dict[str, Decoder] = {}


def register(dec: Decoder) -> Decoder:
    REGISTRY[dec.key] = dec
    return dec


ENDIAN = Option("endian", "엔디언", "choice", "big", [("big", "빅 엔디언"), ("little", "리틀 엔디언")])


def new_root(title: str, data: bytes) -> Node:
    return Node(0, len(data), GROUP, title)


def run_stream(root: Node, data: bytes, one: Callable[[int, Node], int], max_items: int = 1_000_000) -> Node:
    """Decode consecutive top-level items until EOF; record the first error.

    `one(pos, parent)` decodes a single item at pos, appends it to parent and
    returns the new position.
    """
    pos, count = 0, 0
    while pos < len(data) and count < max_items:
        try:
            new = one(pos, root)
        except (DecodeError, RecursionError) as e:
            off = getattr(e, "offset", None)
            off = pos if off is None else off
            msg = "중첩이 너무 깊습니다" if isinstance(e, RecursionError) else str(e)
            root.add(Node(off, 0, ERROR, msg, note=f"0x{pos:X} 에서 시작한 항목"))
            _trailing(root, data, max(pos, off))
            break
        if new <= pos:
            break
        pos, count = new, count + 1
    return root


def map_entry(parent: Node, decode: Callable[[int, Node, str], int], kpos: int, idx: int) -> int:
    """Decode one key/value pair; scalar keys become the label of the value.

    `decode(pos, parent, label)` decodes one object, appends it and returns
    the new position.
    """
    from ..model import VALUE
    holder = Node(kpos, 0, GROUP, "")
    p = decode(kpos, holder, "키")
    k = holder.children[-1]
    if k.kind == VALUE:
        key = k.value
        if len(key) >= 2 and key[0] == key[-1] == '"' and '"' not in key[1:-1]:
            key = key[1:-1]
        end = decode(p, parent, key)
        v = parent.children[-1]
        v.offset, v.length = kpos, end - kpos
        return end
    e = parent.add(Node(kpos, 0, GROUP, f"항목 {idx}", close="}"))
    e.add(k)
    end = decode(p, e, "값")
    e.length = end - kpos
    return end


def _trailing(root: Node, data: bytes, pos: int) -> None:
    from ..model import UNKNOWN
    from ..reader import ascii_preview, hexs
    if pos < len(data):
        rest = data[pos:]
        root.add(Node(pos, len(rest), UNKNOWN, f"{len(rest)} byte 미해석",
                      f"{hexs(rest, 32)}  |{ascii_preview(rest, 32)}|"))
