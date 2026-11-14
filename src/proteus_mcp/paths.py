"""Locations of external tools.  Override with environment variables if installed elsewhere."""
from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

PROTEUS_DIR = Path(os.environ.get(
    "PROTEUS_DIR", r"C:\Program Files (x86)\Labcenter Electronics\Proteus 8 Professional"))
PDS_EXE = PROTEUS_DIR / "BIN" / "PDS.EXE"
DOSBOX_EXE = Path(os.environ.get(
    "DOSBOX_EXE", r"C:\Program Files (x86)\DOSBox-0.74-3\DOSBox.exe"))
MASM_DIR = Path(os.environ.get("PROTEUS_MCP_MASM", REPO / "tools" / "masm"))
WORK_DIR = Path(os.environ.get("PROTEUS_MCP_WORK", REPO / "work"))
QUIRKS_MD = REPO / "docs" / "QUIRKS.md"


def file_version(path: Path) -> str | None:
    try:
        import win32api
        info = win32api.GetFileVersionInfo(str(path), "\\")
        ms, ls = info["FileVersionMS"], info["FileVersionLS"]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return None
