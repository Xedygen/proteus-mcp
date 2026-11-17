"""Environment check."""
from __future__ import annotations

import sys

from . import paths


def report() -> dict:
    masm = paths.MASM_DIR
    return {
        "python": sys.version.split()[0],
        "proteus_dir": str(paths.PROTEUS_DIR),
        "pds_exe": str(paths.PDS_EXE),
        "pds_exe_found": paths.PDS_EXE.is_file(),
        "pds_version": paths.file_version(paths.PDS_EXE),
        "dosbox_exe": str(paths.DOSBOX_EXE),
        "dosbox_found": paths.DOSBOX_EXE.is_file(),
        "masm_dir": str(masm),
        "masm_found": (masm / "MASM.EXE").is_file(),
        "link_found": (masm / "LINK.EXE").is_file(),
        "work_dir": str(paths.WORK_DIR),
        "labs_dir": str(paths.LABS_DIR),
        "labs_found": paths.LABS_DIR.is_dir(),
        "quirks_found": paths.QUIRKS_MD.is_file(),
        "proteus_running_pids": running_proteus(),
    }


def running_proteus() -> list[int]:
    try:
        import win32com.client
        wmi = win32com.client.GetObject("winmgmts:")
        return [p.ProcessId for p in wmi.InstancesOf("Win32_Process") if p.Name.upper() == "PDS.EXE"]
    except Exception:
        return []


def main() -> int:
    r = report()
    width = max(map(len, r))
    for k, v in r.items():
        print(f"{k:<{width}}  {v}")
    ok = r["pds_exe_found"] and r["dosbox_found"] and r["masm_found"] and r["link_found"] and r["quirks_found"]
    return 0 if ok else 1
