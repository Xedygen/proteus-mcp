"""Locations of external tools.  Override with environment variables if installed elsewhere."""
from __future__ import annotations

import json
import os
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
REPO = PACKAGE.parents[1]
# Running from a source checkout (src/proteus_mcp next to pyproject.toml) or from an installed wheel (uvx,
# pip): the wheel carries QUIRKS.md inside the package (pyproject force-include).  MASM is not shipped; see
# find_masm().
IN_REPO = (REPO / "pyproject.toml").is_file() and (REPO / "src" / "proteus_mcp").is_dir()


def _data_home() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "proteus-mcp"


PROTEUS_DIR = Path(os.environ.get(
    "PROTEUS_DIR", r"C:\Program Files (x86)\Labcenter Electronics\Proteus 8 Professional"))
PDS_EXE = PROTEUS_DIR / "BIN" / "PDS.EXE"
DOSBOX_EXE = Path(os.environ.get(
    "DOSBOX_EXE", r"C:\Program Files (x86)\DOSBox-0.74-3\DOSBox.exe"))
WORK_DIR = Path(os.environ.get("PROTEUS_MCP_WORK", REPO / "work" if IN_REPO else _data_home() / "work"))
LABS_DIR = Path(os.environ.get("PROTEUS_MCP_LABS", REPO / "labs" if IN_REPO else _data_home() / "labs"))
QUIRKS_MD = REPO / "docs" / "QUIRKS.md" if IN_REPO else PACKAGE / "QUIRKS.md"

CONFIG_FILE = _data_home() / "config.json"

WORK_DIR.mkdir(parents=True, exist_ok=True)

# ---- MASM 5.10 -------------------------------------------------------------------------------------------
# The assembler is not redistributed with this package.  Students get it with the course materials; this finds
# it on the machine, or the user names the folder (masm_location tool, PROTEUS_MCP_MASM).

MASM_FILES = ("MASM.EXE", "LINK.EXE")
_SEARCH_DEPTH = 3               # folder levels searched below the home folder and each drive root


class MasmNotFound(FileNotFoundError):
    pass


def is_masm_dir(path: Path | str) -> bool:
    try:
        d = Path(path)
        return all((d / f).is_file() for f in MASM_FILES)
    except OSError:
        return False


def _load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_config(cfg: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def _search_roots() -> list[Path]:
    home = Path.home()
    roots = [home, home / "Desktop", home / "Downloads", home / "Documents", home / "OneDrive"]
    roots += [Path(f"{c}:\\") for c in "CDEFGH" if Path(f"{c}:\\").exists()]
    return [r for r in roots if r.is_dir()]


def _search_masm() -> Path | None:
    """Common install folders first, then every folder a few levels below the home folder and the drive roots
    (system folders skipped), folders named like *masm* before the rest at each level."""
    fixed = [Path(f"{c}:\\{n}") for c in "CD" for n in ("masm", "MASM510", "MASM5", "masm51")]
    for d in fixed:
        if is_masm_dir(d):
            return d
    skip = {"windows", "program files", "program files (x86)", "programdata", "$recycle.bin", "appdata",
            "system volume information", "node_modules", ".git", ".venv", "__pycache__"}
    seen: set[str] = set()
    for root in _search_roots():
        if is_masm_dir(root):
            return root
        level = [root]
        for _ in range(_SEARCH_DEPTH):
            nxt = []
            for d in level:
                try:
                    children = [c for c in d.iterdir() if c.is_dir()]
                except OSError:
                    continue
                for c in children:
                    name = c.name.lower()
                    if name in skip or name.startswith("."):
                        continue
                    key = str(c).lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    nxt.append(c)
            nxt.sort(key=lambda c: "masm" not in c.name.lower())
            for c in nxt:
                if is_masm_dir(c):
                    return c
            level = nxt
    return None


_masm_cache: dict = {}


def find_masm(search: bool = True) -> tuple[Path | None, str]:
    """(folder, how it was found).  Order: PROTEUS_MCP_MASM, the saved location, tools/masm in a source
    checkout, then a search of the disk (the result is saved so it runs once)."""
    env = os.environ.get("PROTEUS_MCP_MASM")
    if env:
        return (Path(env), "env PROTEUS_MCP_MASM") if is_masm_dir(env) else (None, f"PROTEUS_MCP_MASM={env} "
                                                                             "has no MASM.EXE and LINK.EXE")
    saved = _load_config().get("masm_dir")
    if saved and is_masm_dir(saved):
        return Path(saved), f"saved in {CONFIG_FILE}"
    if IN_REPO and is_masm_dir(REPO / "tools" / "masm"):
        return REPO / "tools" / "masm", "source checkout tools/masm"
    if not search:
        return None, "not configured"
    if "found" not in _masm_cache:
        _masm_cache["found"] = _search_masm()
    found = _masm_cache["found"]
    if found is None:
        return None, "not found on this computer"
    save_masm(found)
    return found, "found by searching the disk"


def save_masm(path: Path | str) -> Path:
    d = Path(path).expanduser()
    if d.is_file():
        d = d.parent
    if not is_masm_dir(d):
        missing = [f for f in MASM_FILES if not (d / f).is_file()]
        raise MasmNotFound(f"{d} does not contain {', '.join(missing)}")
    cfg = _load_config()
    cfg["masm_dir"] = str(d.resolve())
    _save_config(cfg)
    return d.resolve()


def masm_dir() -> Path:
    d, how = find_masm()
    if d is None:
        raise MasmNotFound(
            f"MASM 5.10 not found ({how}).  It is not shipped with proteus-mcp.  Ask the user where the folder "
            "holding MASM.EXE and LINK.EXE is (it comes with the course materials) and pass it to the "
            "masm_location tool, or set PROTEUS_MCP_MASM.")
    return d


def file_version(path: Path) -> str | None:
    try:
        import win32api
        info = win32api.GetFileVersionInfo(str(path), "\\")
        ms, ls = info["FileVersionMS"], info["FileVersionLS"]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return None
