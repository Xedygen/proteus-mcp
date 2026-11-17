"""Locations of external tools.  Override with environment variables if installed elsewhere."""
from __future__ import annotations

import os
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
REPO = PACKAGE.parents[1]
# Running from a source checkout (src/proteus_mcp next to pyproject.toml) or from an installed wheel (uvx,
# pip): the wheel carries the MASM tools and QUIRKS.md inside the package (pyproject force-include).
IN_REPO = (REPO / "pyproject.toml").is_file() and (REPO / "src" / "proteus_mcp").is_dir()


def _data_home() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "proteus-mcp"


PROTEUS_DIR = Path(os.environ.get(
    "PROTEUS_DIR", r"C:\Program Files (x86)\Labcenter Electronics\Proteus 8 Professional"))
PDS_EXE = PROTEUS_DIR / "BIN" / "PDS.EXE"
DOSBOX_EXE = Path(os.environ.get(
    "DOSBOX_EXE", r"C:\Program Files (x86)\DOSBox-0.74-3\DOSBox.exe"))
MASM_DIR = Path(os.environ.get(
    "PROTEUS_MCP_MASM", REPO / "tools" / "masm" if IN_REPO else PACKAGE / "tools" / "masm"))
WORK_DIR = Path(os.environ.get("PROTEUS_MCP_WORK", REPO / "work" if IN_REPO else _data_home() / "work"))
LABS_DIR = Path(os.environ.get("PROTEUS_MCP_LABS", REPO / "labs" if IN_REPO else _data_home() / "labs"))
QUIRKS_MD = REPO / "docs" / "QUIRKS.md" if IN_REPO else PACKAGE / "QUIRKS.md"

WORK_DIR.mkdir(parents=True, exist_ok=True)


def file_version(path: Path) -> str | None:
    try:
        import win32api
        info = win32api.GetFileVersionInfo(str(path), "\\")
        ms, ls = info["FileVersionMS"], info["FileVersionLS"]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return None
