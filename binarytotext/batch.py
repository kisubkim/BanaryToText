"""Batch conversion: many binary files -> text/JSON files with the same settings."""

from __future__ import annotations

import fnmatch
import glob
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import library, preprocess
from .formats import REGISTRY
from .model import compute_stats
from .render import RenderOptions, render_json, render_text
from .rules import RuleEngine, RuleSet

NAME_FIELDS = ("name", "stem", "ext")
OK, SKIPPED, FAILED = "ok", "skipped", "failed"
STATUS_TEXT = {OK: "완료", SKIPPED: "건너뜀", FAILED: "실패"}


@dataclass
class Settings:
    ruleset: RuleSet | None = None   # base rule set (used when fmt is empty)
    fmt: str = ""                    # built-in format key; empty = rules
    fmt_opts: dict = field(default_factory=dict)
    library_dir: str = ""            # rule folder: a matching rule set overrides the base settings
    fallback: bool = True            # library miss -> use base settings (False: skip the file)
    pre: str = "none"
    pre_arg: str = ""
    as_json: bool = False
    render: RenderOptions = field(default_factory=RenderOptions)
    out_dir: str = ""                # empty = next to each source file
    pattern: str = ""                # empty = default_pattern(as_json)
    overwrite: bool = False

    def has_base(self) -> bool:
        return bool(self.fmt) or self.ruleset is not None


@dataclass
class Result:
    src: Path
    status: str
    out: Path | None = None
    method: str = ""        # rule set / format actually used
    message: str = ""
    coverage: float = 0.0
    unknown: int = 0
    errors: int = 0


def default_pattern(as_json: bool) -> str:
    return "{name}.json" if as_json else "{name}.txt"


def check_pattern(pattern: str) -> str:
    """Return an error message for a bad file name pattern, or ''."""
    if not pattern.strip():
        return "파일 이름 형식이 비어 있습니다"
    try:
        keys = [f for _, f, _, _ in string.Formatter().parse(pattern) if f is not None]
    except ValueError as e:
        return f"중괄호가 맞지 않습니다: {e}"
    bad = [k for k in keys if k not in NAME_FIELDS]
    if bad:
        return f"알 수 없는 항목 {{{bad[0]}}} (쓸 수 있는 것: {{name}} {{stem}} {{ext}})"
    if any(c in pattern for c in '<>:"|?*'):
        return '파일 이름에 쓸 수 없는 문자가 있습니다 (< > : " | ? *)'
    return ""


def output_name(src: Path, pattern: str) -> str:
    return pattern.format(name=src.name, stem=src.stem, ext=src.suffix.lstrip("."))


def collect(inputs: list[str], recursive: bool = False, include: str = "*",
            exclude_dir: str = "") -> list[tuple[Path, Path]]:
    """Expand files, folders and wildcards into (source, relative path) pairs.

    The relative path keeps sub-folder structure for files found inside a given folder.
    Files under exclude_dir (the output folder) are left out so reruns don't pick up results.
    """
    pats = [p.strip() for p in (include or "*").replace(",", ";").split(";") if p.strip()] or ["*"]
    skip = Path(exclude_dir).resolve() if exclude_dir else None
    seen: set[Path] = set()
    out: list[tuple[Path, Path]] = []

    def add(p: Path, rel: Path) -> None:
        rp = p.resolve()
        if rp in seen or (skip and (rp == skip or skip in rp.parents)):
            return
        seen.add(rp)
        out.append((p, rel))

    for item in inputs:
        expanded = glob.glob(item) if glob.has_magic(item) else [item]
        for s in expanded:
            p = Path(s)
            if p.is_dir():
                files = p.rglob("*") if recursive else p.glob("*")
                for f in sorted(files):
                    if f.is_file() and any(fnmatch.fnmatch(f.name.lower(), x.lower()) for x in pats):
                        add(f, f.relative_to(p))
            elif p.is_file():
                add(p, Path(p.name))
    return out


def _target(src: Path, rel: Path, st: Settings, taken: set[Path]) -> Path:
    base = Path(st.out_dir) / rel.parent if st.out_dir else src.parent
    t = base / output_name(src, st.pattern or default_pattern(st.as_json))
    stem, suffix, i = t.stem, t.suffix, 2
    while t.resolve() in taken or (not st.overwrite and t.exists()) or t.resolve() == src.resolve():
        t = t.with_name(f"{stem} ({i}){suffix}")
        i += 1
    taken.add(t.resolve())
    return t


def convert_one(src: Path, rel: Path, st: Settings, entries: list[library.Entry],
                taken: set[Path]) -> Result:
    try:
        data = preprocess.apply(src.read_bytes(), st.pre, st.pre_arg)
    except Exception as e:
        return Result(src, FAILED, message=f"읽기/전처리 실패: {e}")
    rules, fmt, method = None, "", ""
    hits = library.find(data, entries) if entries else []
    if hits:
        rules, method = hits[0].ruleset, f"{hits[0].path.name} (자동)"
    elif st.library_dir and not st.fallback:
        return Result(src, SKIPPED, message="규칙 폴더에 맞는 규칙 세트가 없음")
    elif st.fmt:
        fmt, method = st.fmt, REGISTRY[st.fmt].title
    elif st.ruleset is not None:
        rules, method = st.ruleset, f"규칙 '{st.ruleset.name}'"
    else:
        return Result(src, SKIPPED, message="맞는 규칙 세트가 없고 기본 해석 방식도 없음")
    try:
        root = RuleEngine(rules).decode(data) if rules is not None else REGISTRY[fmt].decode(data, st.fmt_opts)
        text = render_json(root) if st.as_json else render_text(root, data, st.render)
        out = _target(src, rel, st, taken)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    except Exception as e:
        return Result(src, FAILED, method=method, message=f"{type(e).__name__}: {e}")
    s = compute_stats(root, len(data))
    return Result(src, OK, out, method, coverage=s.coverage, unknown=s.unknown, errors=s.errors)


def run(files: list[tuple[Path, Path]], st: Settings,
        progress: Callable[[int, Result], None] | None = None,
        cancelled: Callable[[], bool] | None = None) -> tuple[list[Result], list[str]]:
    """Convert every file. Returns (results, warnings). One failure never stops the rest."""
    warnings: list[str] = []
    entries: list[library.Entry] = []
    if st.library_dir:
        entries, warnings = library.scan(st.library_dir)
    if st.fmt and st.fmt not in REGISTRY:
        raise ValueError(f"알 수 없는 포맷 '{st.fmt}'")
    results: list[Result] = []
    taken: set[Path] = set()
    for i, (src, rel) in enumerate(files):
        if cancelled and cancelled():
            break
        r = convert_one(src, rel, st, entries, taken)
        results.append(r)
        if progress:
            progress(i, r)
    return results, warnings


def summary(results: list[Result], total: int | None = None) -> str:
    n = {k: sum(r.status == k for r in results) for k in (OK, SKIPPED, FAILED)}
    s = f"완료 {n[OK]}개, 건너뜀 {n[SKIPPED]}개, 실패 {n[FAILED]}개"
    if total is not None and len(results) < total:
        s += f" (중단: {total - len(results)}개 남음)"
    return s
