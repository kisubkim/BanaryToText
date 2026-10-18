"""Write a Windows .bat file that runs `btt batch` with fixed settings.

The GUI fills a BatSpec from its current method and output options; the .bat can
then be double-clicked (fixed inputs) or have files/folders dropped on it (%*).
"""

from __future__ import annotations

import datetime as _dt
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from .render import RenderOptions

# Characters that make cmd.exe split or interpret an argument.
_NEEDS_QUOTES = re.compile(r'[\s&|<>^(),;=!"]')


@dataclass
class BatSpec:
    btt: str = ""                    # path of btt.exe; "" with python set = run from source
    btt_beside: bool = False         # use btt.exe from the .bat's own folder (%~dp0btt.exe)
    python: str = ""                 # source checkout: python.exe ...
    source_dir: str = ""             # ... and the folder that holds the binarytotext package
    inputs: list[str] = field(default_factory=list)  # empty = files/folders dropped on the .bat (%*)
    recursive: bool = False
    include: str = "*"
    rules: str = ""                  # rule set JSON
    fmt: str = ""                    # built-in format key
    fmt_opts: dict = field(default_factory=dict)
    library: str = ""                # rule folder (auto selection per file)
    pre: str = "none"
    pre_arg: str = ""
    render: RenderOptions = field(default_factory=RenderOptions)
    as_json: bool = False
    out_dir: str = ""
    pattern: str = ""
    overwrite: bool = False
    pause: bool = True               # keep the window open at the end (double-click use)
    description: str = ""            # what the settings mean, written as a comment


def quote(arg: str) -> str:
    """One argument for a .bat line: quoted when needed, % doubled (a .bat expands %)."""
    arg = arg.replace("%", "%%")
    if arg == "" or _NEEDS_QUOTES.search(arg):
        return '"' + arg.replace('"', '""') + '"'
    return arg


def _opt_text(v) -> str:
    return ("true" if v else "false") if isinstance(v, bool) else str(v)


def batch_args(spec: BatSpec) -> list[str]:
    """`btt batch` arguments after the inputs (not quoted)."""
    a: list[str] = []
    if spec.library:
        a += ["--library", spec.library]
    if spec.rules:
        a += ["--rules", spec.rules]
    elif spec.fmt:
        a += ["--format", spec.fmt]
        for k, v in spec.fmt_opts.items():
            a += ["--opt", f"{k}={_opt_text(v)}"]
    if spec.recursive:
        a.append("-r")
    if spec.include and spec.include != "*":
        a += ["--include", spec.include]
    if spec.pre and spec.pre != "none":
        a += ["--pre", spec.pre]
        if spec.pre_arg:
            a += ["--pre-arg", spec.pre_arg]
    if spec.out_dir:
        a += ["-d", spec.out_dir]
    if spec.pattern:
        a += ["--name", spec.pattern]
    if spec.overwrite:
        a.append("--overwrite")
    if spec.as_json:
        a.append("--json")
    r = spec.render
    if not r.offsets:
        a.append("--no-offsets")
    if r.raw_hex:
        a.append("--raw")
    if not r.labels:
        a.append("--no-labels")
    if not r.quotes:
        a.append("--no-quotes")
    if r.commas:
        a.append("--commas")
    if r.layout != "lines":
        a += ["--layout", r.layout]
    if r.float_digits:
        a += ["--float-digits", str(r.float_digits)]
    return a


def check(spec: BatSpec) -> str:
    """Empty when the spec can produce a working .bat, otherwise the reason (Korean, for the UI)."""
    if not (spec.rules or spec.fmt or spec.library):
        return "규칙 JSON, 내장 포맷, 규칙 폴더 중 하나가 필요합니다"
    if not (spec.btt or spec.btt_beside or spec.python):
        return "btt.exe 경로가 필요합니다"
    return ""


def bat_text(spec: BatSpec) -> str:
    if spec.btt_beside:
        cmd = ['"%~dp0btt.exe"']
    elif spec.btt:
        cmd = [quote(spec.btt)]
    else:
        cmd = [quote(spec.python), "-m", "binarytotext"]
    inputs = " ".join(quote(i) for i in spec.inputs) if spec.inputs else "%*"
    line = " ".join(cmd + ["batch", inputs] + [quote(x) for x in batch_args(spec)])
    stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    out = ["@echo off", "setlocal",
           f"rem BinaryToText {__version__} 에서 만든 변환 스크립트 ({stamp})"]
    if spec.description:
        out.append(f"rem 해석 방식: {spec.description}")
    if not spec.inputs:
        out.append("rem 사용법: 변환할 파일이나 폴더를 이 bat 파일 아이콘에 끌어다 놓으세요.")
        out += ['if "%~1"=="" (',
                "  echo 변환할 파일이나 폴더를 이 bat 파일에 끌어다 놓으세요.",
                "  pause",
                "  exit /b 1",
                ")"]
    if spec.python and not spec.btt and not spec.btt_beside:
        out.append(f'set "PYTHONPATH={spec.source_dir.replace("%", "%%")}"')
    out += [line, "set ERR=%ERRORLEVEL%"]
    out.append("if not %ERR%==0 echo 일부 파일을 건너뛰었거나 변환하지 못했습니다 (종료 코드 %ERR%).")
    if spec.pause:
        out.append("pause")
    out.append("exit /b %ERR%")
    return "\r\n".join(out) + "\r\n"


def console_encoding() -> str:
    """cmd.exe reads a .bat in the console (OEM) code page: cp949 on Korean Windows."""
    if sys.platform == "win32":
        try:
            import ctypes
            return f"cp{ctypes.windll.kernel32.GetOEMCP()}"
        except Exception:
            pass
    return "utf-8"


def write_bat(path: str | Path, text: str, encoding: str | None = None) -> str:
    """Save in the console code page; if a path in it can't be encoded there, UTF-8 + chcp 65001.

    Returns the encoding used.
    """
    enc = encoding or console_encoding()
    try:
        data = text.encode(enc)
    except (UnicodeEncodeError, LookupError):
        enc = "utf-8"
        head, _, rest = text.partition("\r\n")
        data = (head + "\r\nchcp 65001 >nul\r\n" + rest).encode(enc)
    Path(path).write_bytes(data)
    return enc
