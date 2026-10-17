from pathlib import Path

from binarytotext import batch
from binarytotext.cli import main
from binarytotext.formats import REGISTRY
from binarytotext.render import RenderOptions
from binarytotext.rules import FieldSpec, Rule, RuleSet, example_ruleset


def make_tree(root: Path) -> None:
    (root / "sub").mkdir(parents=True)
    (root / "a.bin").write_bytes(b"\x09alpha\x00")
    (root / "b.dat").write_bytes(b"\xca\xfe\x09cafe\x00")
    (root / "sub" / "a.bin").write_bytes(b"\x04\x2a")
    (root / "note.txt").write_text("not binary", encoding="utf-8")


def test_collect_folders_wildcards_and_exclude(tmp_path):
    make_tree(tmp_path)
    names = lambda fs: sorted(str(rel).replace("\\", "/") for _, rel in fs)  # noqa: E731
    assert names(batch.collect([str(tmp_path)])) == ["a.bin", "b.dat", "note.txt"]
    assert names(batch.collect([str(tmp_path)], recursive=True, include="*.bin;*.DAT")) == \
        ["a.bin", "b.dat", "sub/a.bin"]
    assert names(batch.collect([str(tmp_path / "*.bin"), str(tmp_path / "a.bin")])) == ["a.bin"]
    assert names(batch.collect([str(tmp_path)], recursive=True, exclude_dir=str(tmp_path / "sub"))) == \
        ["a.bin", "b.dat", "note.txt"]


def test_pattern_check_and_names():
    assert batch.check_pattern("{stem}_해석.txt") == ""
    assert "알 수 없는" in batch.check_pattern("{foo}.txt")
    assert batch.check_pattern("{name") != ""
    assert batch.check_pattern("a?.txt") != ""
    assert batch.output_name(Path("x/data.bin"), "{stem}-{ext}.txt") == "data-bin.txt"


def test_run_keeps_structure_and_never_overwrites(tmp_path):
    src = tmp_path / "in"
    make_tree(src)
    out = tmp_path / "out"
    files = batch.collect([str(src)], recursive=True, include="*.bin")
    st = batch.Settings(ruleset=example_ruleset(), out_dir=str(out), render=RenderOptions(offsets=False))
    results, _ = batch.run(files, st)
    assert [r.status for r in results] == [batch.OK, batch.OK]
    assert '"alpha"' in (out / "a.bin.txt").read_text(encoding="utf-8")
    assert "42" in (out / "sub" / "a.bin.txt").read_text(encoding="utf-8")
    # Second run: existing results are kept, new ones get " (2)".
    results, _ = batch.run(files, st)
    assert results[0].out.name == "a.bin (2).txt"
    st.overwrite = True
    assert batch.run(files, st)[0][0].out.name == "a.bin.txt"


def test_run_next_to_source_never_replaces_source(tmp_path):
    (tmp_path / "x.bin").write_bytes(b"\x09hi\x00")
    st = batch.Settings(ruleset=example_ruleset(), pattern="{name}", overwrite=True)
    r = batch.run(batch.collect([str(tmp_path / "x.bin")]), st)[0][0]
    assert r.out == tmp_path / "x (2).bin" and (tmp_path / "x.bin").read_bytes() == b"\x09hi\x00"


def test_library_fallback_skip_and_failure(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    RuleSet(name="카페", match_pattern="CA FE",
            rules=[Rule("09", "카페문자열", fields=[FieldSpec("cstring")])]).save(lib / "cafe.json")
    src = tmp_path / "in"
    make_tree(src)
    files = batch.collect([str(src / "a.bin"), str(src / "b.dat")])
    out = tmp_path / "out"
    st = batch.Settings(fmt="hexdump", library_dir=str(lib), out_dir=str(out))
    res, _ = batch.run(files, st)
    assert res[0].method == REGISTRY["hexdump"].title and res[1].method == "cafe.json (자동)"
    assert '카페문자열: "cafe"' in res[1].out.read_text(encoding="utf-8")
    st.fallback = False
    res, _ = batch.run(files, st)
    assert [r.status for r in res] == [batch.SKIPPED, batch.OK]
    st = batch.Settings(ruleset=example_ruleset(), pre="zlib", out_dir=str(out))
    res, _ = batch.run(files, st)
    assert all(r.status == batch.FAILED and "전처리" in r.message for r in res)
    assert "실패 2개" in batch.summary(res)


def test_cli_batch(tmp_path, capsys):
    src = tmp_path / "in"
    make_tree(src)
    rules = tmp_path / "r.json"
    example_ruleset().save(rules)
    out = tmp_path / "out"
    rc = main(["batch", str(src), "-r", "--include", "*.bin", "--rules", str(rules), "-d", str(out),
               "--name", "{stem}_해석.txt", "--no-offsets"])
    assert rc == 0
    assert '"alpha"' in (out / "a_해석.txt").read_text(encoding="utf-8")
    assert (out / "sub" / "a_해석.txt").exists()
    err = capsys.readouterr().err
    assert "[2/2] 완료" in err and "완료 2개" in err
    rc = main(["batch", str(src / "a.bin"), "--format", "hexdump", "--json", "-d", str(out)])
    assert rc == 0 and (out / "a.bin.json").exists()
