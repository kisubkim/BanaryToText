"""Command line: decode / analyze / formats. With no arguments the GUI starts."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import batch, heuristics, library, preprocess
from .formats import REGISTRY
from .reader import float_digits
from .render import LAYOUTS, RenderOptions, iter_lines, render_json, write_json, write_text
from .rules import RuleSet, decode_with_rules


def _opt_value(s: str):
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    try:
        return int(s, 0)
    except ValueError:
        return s


def _render_opts(args) -> RenderOptions:
    return RenderOptions(offsets=not args.no_offsets, raw_hex=args.raw,
                         labels=not args.no_labels, layout=args.layout, commas=args.commas,
                         quotes=not args.no_quotes, float_digits=args.float_digits)


def _opts(pairs: list[str]) -> dict:
    opts = {}
    for kv in pairs:
        k, _, v = kv.partition("=")
        opts[k] = _opt_value(v)
    return opts


def _batch(args) -> int:
    if not (args.rules or args.format or args.library):
        raise SystemExit("--rules, --format, --library 중 하나 이상을 지정하세요")
    if args.format and args.format not in REGISTRY:
        raise SystemExit(f"알 수 없는 포맷 '{args.format}'. 목록: python -m binarytotext formats")
    if args.name and (err := batch.check_pattern(args.name)):
        raise SystemExit(f"--name: {err}")
    fmt_opts = REGISTRY[args.format].defaults() if args.format else {}
    fmt_opts.update(_opts(args.opt))
    st = batch.Settings(
        ruleset=RuleSet.load(args.rules) if args.rules else None, fmt=args.format or "", fmt_opts=fmt_opts,
        library_dir=args.library or "", fallback=bool(args.rules or args.format),
        pre=args.pre, pre_arg=args.pre_arg, as_json=args.json,
        render=_render_opts(args),
        out_dir=args.out_dir, pattern=args.name, overwrite=args.overwrite)
    files = batch.collect(args.inputs, args.recursive, args.include, args.out_dir)
    if not files:
        raise SystemExit("변환할 파일이 없습니다")
    err = sys.stderr

    def show(i: int, r: batch.Result) -> None:
        tail = (f"→ {r.out}  [{r.method}, 해석률 {r.coverage:.0%}]" if r.status == batch.OK
                else f"{r.message}")
        err.write(f"[{i + 1}/{len(files)}] {batch.STATUS_TEXT[r.status]}: {r.src}  {tail}\n")

    results, warnings = batch.run(files, st, show)
    for w in warnings:
        err.write(f"경고: {w}\n")
    err.write(batch.summary(results) + "\n")
    return 0 if all(r.status == batch.OK for r in results) else 1


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
    g.add_argument("--library", metavar="DIR", help="규칙 폴더: 식별 조건이 맞는 규칙 세트를 자동 선택")
    d.add_argument("--opt", action="append", default=[], metavar="KEY=VALUE", help="포맷 옵션")
    d.add_argument("--pre", default="none", choices=list(preprocess.TRANSFORMS), help="전처리")
    d.add_argument("--pre-arg", default="", help="전처리 인자 (skip 의 N, xor 키 hex)")
    d.add_argument("-o", "--output", help="출력 파일 (기본: 표준 출력)")
    d.add_argument("--json", action="store_true", help="JSON 트리로 출력")
    d.add_argument("--no-offsets", action="store_true")
    d.add_argument("--raw", action="store_true", help="각 항목의 원본 hex 함께 표시")
    d.add_argument("--no-labels", action="store_true", help="이름(라벨) 없이 값만 (파일에서 읽은 키는 유지)")
    d.add_argument("--layout", choices=list(LAYOUTS), default="lines",
                   help="그룹 표시: lines 줄마다(기본), arrays 값만 든 배열만 한 줄, inline 모든 그룹 한 줄 (규칙의 그룹 표시 설정이 우선)")
    d.add_argument("--commas", action="store_true", help="그룹 안 항목 뒤에 쉼표 (마지막 제외)")
    d.add_argument("--no-quotes", action="store_true", help="문자열을 따옴표 없이 (빈 문자열은 비워 둠)")
    d.add_argument("--float-digits", type=int, default=0, metavar="N",
                   help="실수(float/double)를 유효 숫자 N자리로 (0 = 자동: 값을 정확히 나타내는 가장 짧은 표기)")

    a = sub.add_parser("analyze", help="자동 분석 보고서")
    a.add_argument("file")
    a.add_argument("--no-rank", action="store_true", help="내장 포맷 적합도 계산 생략")

    b = sub.add_parser("batch", help="여러 파일을 한 번에 변환",
                       description="파일·폴더·와일드카드(*.bin)를 받아 각각 텍스트(또는 JSON) 파일로 저장합니다. "
                                   "--library 와 --rules/--format 을 같이 주면 규칙 폴더에서 맞는 것이 없을 때 "
                                   "--rules/--format 으로 변환합니다.")
    b.add_argument("inputs", nargs="+", metavar="파일또는폴더")
    bg = b.add_mutually_exclusive_group()
    bg.add_argument("--rules", help="규칙 세트 JSON")
    bg.add_argument("--format", help="내장 포맷 키")
    b.add_argument("--library", metavar="DIR", help="규칙 폴더: 파일마다 식별 조건이 맞는 규칙 세트를 자동 선택")
    b.add_argument("-d", "--out-dir", default="", help="출력 폴더 (기본: 각 원본 파일 옆)")
    b.add_argument("--name", default="", help="출력 파일 이름 형식 ({name} {stem} {ext}, 기본 {name}.txt)")
    b.add_argument("-r", "--recursive", action="store_true", help="폴더의 하위 폴더까지 (폴더 구조 유지)")
    b.add_argument("--include", default="*", help="폴더에서 고를 파일 패턴, ; 로 여러 개 (기본 *)")
    b.add_argument("--overwrite", action="store_true", help="같은 이름이 있으면 덮어쓰기 (기본: 이름 (2) 로 저장)")
    b.add_argument("--opt", action="append", default=[], metavar="KEY=VALUE", help="포맷 옵션")
    b.add_argument("--pre", default="none", choices=list(preprocess.TRANSFORMS), help="전처리")
    b.add_argument("--pre-arg", default="")
    b.add_argument("--json", action="store_true", help="JSON 트리로 저장")
    b.add_argument("--no-offsets", action="store_true")
    b.add_argument("--raw", action="store_true", help="각 항목의 원본 hex 함께 표시")
    b.add_argument("--no-labels", action="store_true", help="이름(라벨) 없이 값만 (파일에서 읽은 키는 유지)")
    b.add_argument("--layout", choices=list(LAYOUTS), default="lines",
                   help="그룹 표시: lines 줄마다(기본), arrays 값만 든 배열만 한 줄, inline 모든 그룹 한 줄 (규칙의 그룹 표시 설정이 우선)")
    b.add_argument("--commas", action="store_true", help="그룹 안 항목 뒤에 쉼표 (마지막 제외)")
    b.add_argument("--no-quotes", action="store_true", help="문자열을 따옴표 없이 (빈 문자열은 비워 둠)")
    b.add_argument("--float-digits", type=int, default=0, metavar="N",
                   help="실수(float/double)를 유효 숫자 N자리로 (0 = 자동)")

    sub.add_parser("formats", help="내장 포맷 목록")

    argv = sys.argv[1:] if argv is None else argv
    if gui and len(argv) <= 1 and (not argv or Path(argv[0]).is_file()):
        from .gui.app import run  # no command: GUI, optionally opening a file
        return run(argv[0] if argv else None)
    if gui and argv and argv[0] not in sub.choices and all(Path(a).exists() for a in argv):
        from .gui.app import run  # several files/folders dropped on the exe: batch dialog
        return run(batch_paths=argv)
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

    if args.cmd == "batch":
        return _batch(args)

    data = Path(args.file).read_bytes()
    if args.cmd == "analyze":
        out.write(heuristics.report_text(heuristics.analyze(data, rank=not args.no_rank)))
        return 0

    data = preprocess.apply(data, args.pre, args.pre_arg)
    opts = _opts(args.opt)
    rules = RuleSet.load(args.rules) if args.rules else None
    if args.library:
        entries, errors = library.scan(args.library)
        for e in errors:
            sys.stderr.write(f"경고: {e}\n")
        hits = library.find(data, entries)
        if not hits:
            raise SystemExit(f"규칙 폴더 {args.library} 에 이 파일과 맞는 규칙 세트가 없습니다")
        rules = hits[0].ruleset
        sys.stderr.write(f"규칙 자동 선택: {hits[0].path.name}\n")
    with float_digits(args.float_digits):
        root = decode_bytes(data, rules, args.format, opts)
    if args.output:
        if args.json:
            write_json(args.output, root)
        else:
            write_text(args.output, root, data, _render_opts(args))
    elif args.json:
        out.write(render_json(root))
    else:
        for _, line in iter_lines(root, data, _render_opts(args)):
            out.write(line + "\n")
    return 0
