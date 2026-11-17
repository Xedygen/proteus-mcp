"""MASM location: environment variable, saved config, validation (no disk search)."""
import pytest

from proteus_mcp import paths


def _fake_masm(d):
    d.mkdir(parents=True, exist_ok=True)
    for f in paths.MASM_FILES:
        (d / f).write_bytes(b"MZ")
    return d


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.delenv("PROTEUS_MCP_MASM", raising=False)
    monkeypatch.setattr(paths, "CONFIG_FILE", tmp_path / "cfg" / "config.json")
    monkeypatch.setattr(paths, "IN_REPO", False)
    monkeypatch.setattr(paths, "_masm_cache", {"found": None})     # the search finds nothing
    return tmp_path


def test_not_found_asks_the_user(isolated):
    assert paths.find_masm() == (None, "not found on this computer")
    with pytest.raises(paths.MasmNotFound, match="masm_location"):
        paths.masm_dir()


def test_save_validates_and_remembers(isolated):
    empty = isolated / "empty"
    empty.mkdir()
    with pytest.raises(paths.MasmNotFound, match="MASM.EXE"):
        paths.save_masm(empty)
    good = _fake_masm(isolated / "C MASM")
    assert paths.save_masm(good / "MASM.EXE") == good.resolve()     # a file inside the folder works too
    d, how = paths.find_masm()
    assert d == good.resolve() and how.startswith("saved")


def test_env_wins(isolated, monkeypatch):
    paths.save_masm(_fake_masm(isolated / "saved"))
    env = _fake_masm(isolated / "env")
    monkeypatch.setenv("PROTEUS_MCP_MASM", str(env))
    assert paths.masm_dir() == env
    monkeypatch.setenv("PROTEUS_MCP_MASM", str(isolated / "nowhere"))
    assert paths.find_masm()[0] is None


def test_search_finds_a_masm_folder(isolated, monkeypatch):
    home = isolated / "home"
    target = _fake_masm(home / "Downloads" / "BLM3061" / "masm")
    monkeypatch.setattr(paths, "_search_roots", lambda: [home])
    monkeypatch.setattr(paths, "is_masm_dir", lambda p, real=paths.is_masm_dir: real(p) and
                        str(p).startswith(str(isolated)))          # ignore a real C:\masm on this machine
    assert paths._search_masm() == target
