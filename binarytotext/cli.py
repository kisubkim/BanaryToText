"""Command line: decode / analyze / formats. With no arguments the GUI starts."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import heuristics, preprocess
from .formats import REGISTRY
from .render import RenderOptions, render_json, render_text
from .rules import RuleSet, decode_with_rules


def _opt_value(s: str):
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    try:
        return int(s, 0)
    except ValueError:
        return s


def decode_bytes(data: bytes, rules: RuleSet | None = None, fmt: str | None = None, opts: dict | None = None):
    if rules is not None:
        return decode_with_rules(data, rules)
    if fmt not in REGISTRY:
        raise SystemExit(f"알 수 없는 포맷 '{fmt}'. 목록: python -m binarytotext formats")
    return REGISTRY[fmt].decode(data, opts or {})


def main(argv: list[str] | None = None, gui: bool = True) -> int:
    p = argparse.ArgumentParser(prog="binarytotext", description="바이너리 파일을 사람이 읽을 수 있는 텍스트로 변환")
    sub = p.add_subparsers(dest="cmd")

    d = sub.add_parser("decode", help="파일 해석 후 텍스트 출력")
    d.add_argument("file")
    g = d.add_mutually_exclusive_group(required=True)
    g.add_argument("--rules", help="규칙 세트 JSON")
    g.add_argument("--format", help="내장 포맷 키 (formats 명령으로 확인)")
    d.add_argument("--opt", action="append", default=[], metavar="KEY=VALUE", help="포맷 옵션")
    d.add_argument("--pre", default="none", choices=list(preprocess.TRANSFORMS), help="전처리")
    d.add_argument("--pre-arg", default="", help="전처리 인자 (skip 의 N, xor 키 hex)")
    d.add_argument("-o", "--output", help="출력 파일 (기본: 표준 출력)")
    d.add_argument("--json", action="store_true", help="JSON 트리로 출력")
    d.add_argument("--no-offsets", action="store_true")
    d.add_argument("--raw", action="store_true", help="각 항목의 원본 hex 함께 표시")

    a = sub.add_parser("analyze", help="자동 분석 보고서")
    a.add_argument("file")
    a.add_argument("--no-rank", action="store_true", help="내장 포맷 적합도 계산 생략")

    sub.add_parser("formats", help="내장 포맷 목록")

    argv = sys.argv[1:] if argv is None else argv
    if gui and len(argv) <= 1 and (not argv or Path(argv[0]).is_file()):
        from .gui.app import run  # no command: GUI, optionally opening a file
        return run(argv[0] if argv else None)
    args = p.parse_args(argv)
    if args.cmd is None:
        p.print_help()
        return 1

    out = sys.stdout
    if hasattr(out, "reconfigure"):
        out.reconfigure(encoding="utf-8")

    if args.cmd == "formats":
        for dec in REGISTRY.values():
            out.write(f"{dec.key:15} [{dec.family}] {dec.title}\n")
            for o in dec.options:
                ch = "/".join(str(c) for c, _ in o.choices) if o.choices else o.kind
                out.write(f"{'':17}{o.key}={o.default!r}  ({o.label}: {ch})\n")
        return 0

    data = Path(args.file).read_bytes()
    if args.cmd == "analyze":
        out.write(heuristics.report_text(heuristics.analyze(data, rank=not args.no_rank)))
        return 0

    data = preprocess.apply(data, args.pre, args.pre_arg)
    opts = {}
    for kv in args.opt:
        k, _, v = kv.partition("=")
        opts[k] = _opt_value(v)
    rules = RuleSet.load(args.rules) if args.rules else None
    root = decode_bytes(data, rules, args.format, opts)
    text = render_json(root) if args.json else render_text(
        root, data, RenderOptions(offsets=not args.no_offsets, raw_hex=args.raw))
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        out.write(text)
    return 0
