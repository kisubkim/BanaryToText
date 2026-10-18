"""Decoded tree model shared by every decoder, the renderer and the GUI."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

# Node kinds. "group" nodes have children; "unknown" and "error" mark bytes
# that no rule or decoder could explain.
GROUP = "group"
VALUE = "value"
UNKNOWN = "unknown"
ERROR = "error"
INFO = "info"


@dataclass(slots=True)  # slots: large trees use far less memory
class Node:
    offset: int
    length: int
    kind: str
    label: str
    value: str = ""
    children: list["Node"] = field(default_factory=list)
    note: str = ""
    # Closing bracket text for groups ("]" / "}" / ")" ...).
    close: str = ""
    # The label is a key read from the data (rendered "key = value"), not a rule name.
    key: bool = False
    # Groups: "lines" / "inline" overrides the renderer's layout option; "" follows it.
    layout: str = ""

    @property
    def end(self) -> int:
        return self.offset + self.length

    def add(self, child: "Node") -> "Node":
        self.children.append(child)
        return child

    def walk(self) -> Iterator["Node"]:
        """Pre-order, without recursion (trees can be deep)."""
        stack = [self]
        while stack:
            n = stack.pop()
            yield n
            stack.extend(reversed(n.children))

    def fit_children(self) -> None:
        """Extend this node so it spans all its children."""
        if not self.children:
            return
        start = min(self.offset, min(c.offset for c in self.children))
        end = max(self.end, max(c.end for c in self.children))
        self.offset, self.length = start, end - start


@dataclass
class Stats:
    total: int
    explained: int
    unknown: int
    errors: int
    nodes: int

    @property
    def coverage(self) -> float:
        return self.explained / self.total if self.total else 0.0


def compute_stats(root: Node, total: int) -> Stats:
    """Count how many input bytes are explained by leaf values.

    Leaves are counted once each; groups only contribute their header bytes
    implicitly through the gap between children, which is treated as explained.
    """
    unknown = errors = nodes = 0
    for n in root.walk():
        nodes += 1
        if n.kind == UNKNOWN:
            unknown += n.length
        elif n.kind == ERROR:
            errors += 1
    covered = _covered_bytes(root, total)
    explained = max(0, covered - unknown)
    return Stats(total, explained, unknown, errors, nodes)


def _covered_bytes(root: Node, total: int) -> int:
    spans = []
    for n in root.walk():
        if n is root or n.kind in (ERROR, INFO):
            continue
        if n.length > 0:
            spans.append((n.offset, min(n.end, total)))
    spans.sort()
    covered, cur_s, cur_e = 0, -1, -1
    for s, e in spans:
        if s > cur_e:
            if cur_e > cur_s:
                covered += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e > cur_s:
        covered += cur_e - cur_s
    return covered


def deepest_at(root: Node, offset: int) -> Node | None:
    """Return the most specific node whose span contains offset."""
    best = None
    stack = [root]
    while stack:
        n = stack.pop()
        if n.offset <= offset < n.end or (n.length == 0 and n.offset == offset):
            best = n
            stack = list(n.children)
    return best
