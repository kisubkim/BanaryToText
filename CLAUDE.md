# CLAUDE.md

Offline Windows desktop app (PySide6) that turns unknown binary files into human-readable text,
via user-defined tag rules or built-in known formats. No browser, no network. MIT.

## Commands

Run from this folder with the project venv.

- Tests: `.venv/Scripts/python -m pytest -q` (GUI tests run offscreen, settings go to a temp INI via `BINARYTOTEXT_SETTINGS`)
- Single test: `.venv/Scripts/python -m pytest -q tests/test_rules.py::test_example_from_request`
- Lint: `.venv/Scripts/python -m pyflakes binarytotext tests` (unused-import warnings in `formats/__init__.py` are intentional)
- GUI from source: `.venv/Scripts/python -m binarytotext [file]`
- CLI: `.venv/Scripts/python -m binarytotext decode|analyze|formats ...`
- Release build: `powershell -ExecutionPolicy Bypass -File build.ps1` → `dist/BinaryToText.exe` (GUI onefile), `dist/btt.exe` (CLI, built without Qt), `dist/BinaryToText/` (GUI onedir + `btt.exe` + `licenses/`) and `dist/BinaryToText-<ver>-win64.zip`. The zip is the one to hand out: Qt is LGPL-3.0, so its DLLs must stay replaceable and `licenses/` (LGPL/GPL texts, THIRD_PARTY_NOTICES) must ship with it. Flags: `-SkipTests`, `-SkipOneFile`. Exes get version info (`launchers/make_version.py`) and no UPX; they are Authenticode-signed only if `BTT_SIGN_THUMBPRINT` or `BTT_SIGN_PFX` (+`BTT_SIGN_PASSWORD`) is set. `build.ps1` must keep exactly one UTF-8 BOM (Korean text in PowerShell 5.1); a doubled BOM silently breaks `param(...)`

## Architecture

- `model.py`: `Node` tree (offset, length, kind, label, value, children) shared by everything. Kinds: group/value/unknown/error/info.
- `rules.py`: `RuleSet`/`Rule`/`FieldSpec` (JSON via dataclasses) and `RuleEngine`. The engine scans left to right, applies the longest matching tag, groups unmatched bytes into unknown runs. Group handling is a frame stack; `_apply` returns an event and the main loop does all stack bookkeeping (count_block items are counted in `item_done`).
  `RuleSet.header`/`footer` are position-based field lists read before/after the tag scan; the footer must be fixed-size (`fixed_size`). `Rule.end_tag` (open rules) closes only its own group, checked before normal matching.
  `RuleSet.max_depth` (default 100) and `MAX_NODES` stop a runaway decode with an error node: a wrong end tag otherwise nests one group per opening tag, and the indented text grows quadratically. `const` rules output `Rule.text` instead of reading fields (their fields are only skipped); `KEY_ACTIONS` lists actions that can be keys.
- `library.py`: rule library = folder of rule set JSONs; `RuleSet.match_pattern`/`match_offset` identify files, most fixed bytes wins. GUI auto-selects on open and guards unsaved edits (`_dirty` compares to `_saved_json`).
- `batch.py`: batch conversion shared by `btt batch` and `gui/batch_dialog.py`. `Settings` is a snapshot of the main window's method/options; `collect` expands files/folders/wildcards (keeps relative paths, skips the output folder); output names never clobber existing files or the source unless `overwrite`. One failing file never stops the rest.
- `batfile.py`: `BatSpec` → `.bat` text running `btt batch` (inputs fixed or `%*` from files dropped on the .bat; `quote` doubles `%`). Saved as UTF-8 (no BOM); the .bat saves the caller's code page, switches to `chcp 65001` before any non-ASCII line and restores it on every exit, so it works from CP949 consoles and UTF-8 terminals alike. `gui/bat_dialog.py` fills it from the main window (rule sets must be saved JSON files). `tests/test_batfile.py` runs a generated .bat through cmd.exe.
- `formats/`: built-in decoders. Each module calls `register(Decoder(...))`; `Option` lists drive the GUI form; `variants` and `single_root` feed the auto-detector. Decoders read with `reader.Reader` (bounds-checked, raises `DecodeError`) and use `base.run_stream` so a failure leaves the partial tree plus an error node and a trailing unknown node.
- `heuristics.py`: strings (ASCII/UTF-8/UTF-16/CP949), tag/rule suggestions, entropy, magic, record periods, format ranking (`FormatScore.score` penalises errors, 1-byte noise and many roots).
- `preprocess.py`: stdlib-only transforms (decompress, base64, hex text, XOR, skip).
- `render.py`: Node tree → text (optional `line_nodes` maps each line back to a node for GUI linking) or JSON. `iter_lines` is the generator behind everything; files are written with `write_text`/`write_json` (streamed, via `<name>.part` then replace), never by building the whole string. `Node.walk` is iterative.
  Output rules: items of a value-only group (`Node.values_only`: `Rule.items` "values", or "" with a `]` bracket) have no label and no `key =` and are comma-separated (the engine also never pairs keys inside such frames); `Rule.commas`/`Node.commas` "on"/"off" override commas per group, "" = automatic (`commas_in`); with `quotes=False` a string keeps its quotes only if it is empty or holds blanks/separators/quotes/escapes (`_NEEDS_QUOTES`); `header_footer=False` drops root children with `Node.part`; floats keep a 1-3 digit integer part: plain for 1 <= |x| < 1000, else an exponent in steps of 3 (`reader.eng`: `1.2345e3`, `500e-3`); zero and whole numbers get `.0`.
  `RenderOptions.float_digits` is applied at decode time (values are already text in the tree): every decode call site wraps the decode in `reader.float_digits(n)` (a context variable, so threads don't affect each other), and decoders format floats only via `float_text`/`fmt_float`.
- `gui/`: `main_window.py` (decoding and saving run in `QThread`s with a 64 MB stack, never on the GUI thread; decodes use a generation counter and stale results are dropped; start workers only via `_start_job`, never with a lambda that captures the job (that reference cycle kept every finished thread alive); the output options live in `out_panel` above the result tabs), `hexview.py` (table-model hex view + inspector), `field_editor.py` (shared FieldSpec list editor), `rule_dialog.py` (rule editor with live preview), `header_dialog.py` (header/footer), `theme.py` (color themes: one `Theme` color table → Fusion palette + stylesheet; styled combo/spin/check boxes lose native arrows and marks, so `theme.py` draws them as PNGs in the temp folder. Applied by `MainWindow` from the `theme` setting).

## User manual

`docs/manual.md` (Korean) with screenshots in `docs/images/`. `docs/make_screenshots.py` regenerates both the sample files in `examples/manual/` and every screenshot (real Windows windows pop up briefly). After a UI change, rerun it and check that the manual text still matches the images.

## Conventions

- Code comments and docstrings in English; UI strings, docs and commit messages in Korean.
- Stdlib only outside `gui/` (the CLI exe excludes PySide6). Never import PySide6 from non-GUI modules.
- Python with `\x..` escapes or backslashes must be edited with Edit/Write, not bash heredocs.
- New decoder: add a test with a spec test vector in `tests/test_formats.py`; `test_all_decoders_survive_random_bytes` must keep passing.
