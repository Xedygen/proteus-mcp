"""The .pdsprj container: a ZIP with ROOT.DSN, ROOT.CDB, firmware sources and the built EXE."""
from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path


def entries(path: str | Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as z:
        return {i.filename: z.read(i) for i in z.infolist() if not i.is_dir()}


def write(path: str | Path, files: dict[str, bytes]) -> None:
    """Write a project ZIP atomically (deflate, entry order kept)."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    tmp.replace(path)


def replace(path: str | Path, changes: dict[str, bytes | None]) -> None:
    """Replace, add (bytes) or delete (None) entries in place."""
    files = entries(path)
    for name, data in changes.items():
        if data is None:
            files.pop(name, None)
        else:
            files[name] = data
    write(path, files)


def copy(src: str | Path, dst: str | Path) -> Path:
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    return dst


def firmware_name(files: dict[str, bytes]) -> str | None:
    """Name of the firmware project (FIRMWARE/<name>.XML), e.g. '8086' or '8086_'."""
    xml = files.get("FIRMWARE.XML", b"").decode("utf-8", "replace")
    m = re.search(r"<STRING>([^<]+)</STRING>", xml)
    if m:
        return m.group(1)
    for n in files:
        m = re.fullmatch(r"FIRMWARE/([^/]+)/main\.asm", n)
        if m:
            return m.group(1)
    return None


def firmware_paths(files: dict[str, bytes]) -> tuple[str, str] | None:
    name = firmware_name(files)
    if not name:
        return None
    return f"FIRMWARE/{name}/main.asm", f"FIRMWARE/{name}/Debug/Debug.exe"


def referenced_files(project: str | Path) -> list[Path]:
    """Files next to the project that its parts refer to by relative name, e.g. ROM images in a 27C128's
    FILE property (00.bin).  Proteus resolves those against the project's folder, so they travel with it."""
    project = Path(project)
    blob = b"".join(v for k, v in entries(project).items() if k.upper().endswith((".DSN", ".CDB")))
    blob_lower = blob.lower()          # Windows names are case-insensitive: FILE=00.BIN is 00.bin
    out = []
    for f in sorted(project.parent.iterdir()):
        if f.is_file() and f.suffix.lower() not in (".pdsprj", ".pdf", ".workspace", ".sdf", ".source")                 and f.name.lower().encode() in blob_lower:
            out.append(f)
    return out


_FIRMWARE_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes'?>
<FIRMWARE FAMILY="8086" COMPILER="MASM32" TYPE="8086">
 <PROJECTS>
  <STRING>{name}</STRING>
 </PROJECTS>
</FIRMWARE>
"""

_CONFIG = """ <CONFIGURATION TARGET="{target}" COMPILER="MASM32" NAME="{target}">
  <CONTROLLER FAMILY="8086" TYPE="8086"/>
  <FILE TOOL="LD">
   <FILE TOOL="ASM">
    <FILE NAME="{name}/main.asm"/>
   </FILE>
  </FILE>
  <OPTION DEFAULT="1" TOOL="LD" NAME="/CODEVIEW"/>
  <OPTION DEFAULT="1" TOOL="ASM" NAME="/c"/>
  <OPTION DEFAULT="1" TOOL="ASM" NAME="/Zd"/>
  <OPTION DEFAULT="1" TOOL="ASM" NAME="/Zi"/>
 </CONFIGURATION>
"""

_PROJECT_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<PROJECT IID="5" BUILDPATH="{name}" VERSION="814" TYPE="SOURCE" NAME="{name}" CONFIGURATION="Debug">
 <FILES ATTACH="1">
  <FILE ATTACH="1" GROUP="Source Files" NAME="{name}/main.asm"/>
  <FILE ATTACH="1" GROUP="Firmware Files" NAME="{name}/Debug/Debug.exe" FIRMWARE="1"/>
 </FILES>
{debug}{release}</PROJECT>
"""


def new_firmware_project(name: str = "8086") -> dict[str, bytes]:
    """FIRMWARE.XML and FIRMWARE/<name>.XML for an 8086 MASM firmware project, as in the 2025 lab templates.
    Some templates (2024) ship without one although their 8086 still points at .../8086/Debug/Debug.exe;
    adding these entries plus main.asm and Debug.exe attaches the program again."""
    project = _PROJECT_XML.format(name=name, debug=_CONFIG.format(target="Debug", name=name),
                                  release=_CONFIG.format(target="Release", name=name))
    return {"FIRMWARE.XML": _FIRMWARE_XML.format(name=name).encode(),
            f"FIRMWARE/{name}.XML": project.encode()}
