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
- Release build: `powershell -ExecutionPolicy Bypass -File build.ps1` → `dist/BinaryToText.exe` (GUI onefile), `dist/btt.exe` (CLI, built without Qt), `dist/BinaryToText/` (GUI onedir + `btt.exe` + `licenses/`) and `dist/BinaryToText-<ver>-win64.zip`. The zip is the one to hand out: Qt is LGPL-3.0, so its DLLs must stay replaceable and `licenses/` (LGPL/GPL texts, THIRD_PARTY_NOTICES) must ship with it. Flags: `-SkipTests`, `-SkipOneFile`

## Architecture

- `model.py`: `Node` tree (offset, length, kind, label, value, children) shared by everything. Kinds: group/value/unknown/error/info.
- `rules.py`: `RuleSet`/`Rule`/`FieldSpec` (JSON via dataclasses) and `RuleEngine`. The engine scans left to right, applies the longest matching tag, groups unmatched bytes into unknown runs. Group handling is a frame stack; `_apply` returns an event and the main loop does all stack bookkeeping (count_block items are counted in `item_done`).
  `RuleSet.header`/`footer` are position-based field lists read before/after the tag scan; the footer must be fixed-size (`fixed_size`). `Rule.end_tag` (open rules) closes only its own group, checked before normal matching.
- `library.py`: rule library = folder of rule set JSONs; `RuleSet.match_pattern`/`match_offset` identify files, most fixed bytes wins. GUI auto-selects on open and guards unsaved edits (`_dirty` compares to `_saved_json`).
- `batch.py`: batch conversion shared by `btt batch` and `gui/batch_dialog.py`. `Settings` is a snapshot of the main window's method/options; `collect` expands files/folders/wildcards (keeps relative paths, skips the output folder); output names never clobber existing files or the source unless `overwrite`. One failing file never stops the rest.
- `formats/`: built-in decoders. Each module calls `register(Decoder(...))`; `Option` lists drive the GUI form; `variants` and `single_root` feed the auto-detector. Decoders read with `reader.Reader` (bounds-checked, raises `DecodeError`) and use `base.run_stream` so a failure leaves the partial tree plus an error node and a trailing unknown node.
- `heuristics.py`: strings (ASCII/UTF-8/UTF-16/CP949), tag/rule suggestions, entropy, magic, record periods, format ranking (`FormatScore.score` penalises errors, 1-byte noise and many roots).
- `preprocess.py`: stdlib-only transforms (decompress, base64, hex text, XOR, skip).
- `render.py`: Node tree → text (optional `line_nodes` maps each line back to a node for GUI linking) or JSON.
- `gui/`: `main_window.py` (decoding runs in `QThread`s with a generation counter; stale results are dropped), `hexview.py` (table-model hex view + inspector), `field_editor.py` (shared FieldSpec list editor), `rule_dialog.py` (rule editor with live preview), `header_dialog.py` (header/footer), `theme.py` (color themes: one `Theme` color table → Fusion palette + stylesheet; styled combo/spin/check boxes lose native arrows and marks, so `theme.py` draws them as PNGs in the temp folder. Applied by `MainWindow` from the `theme` setting).

## User manual

`docs/manual.md` (Korean) with screenshots in `docs/images/`. `docs/make_screenshots.py` regenerates both the sample files in `examples/manual/` and every screenshot (real Windows windows pop up briefly). After a UI change, rerun it and check that the manual text still matches the images.

## Conventions

- Code comments and docstrings in English; UI strings, docs and commit messages in Korean.
- Stdlib only outside `gui/` (the CLI exe excludes PySide6). Never import PySide6 from non-GUI modules.
- Python with `\x..` escapes or backslashes must be edited with Edit/Write, not bash heredocs.
- New decoder: add a test with a spec test vector in `tests/test_formats.py`; `test_all_decoders_survive_random_bytes` must keep passing.
