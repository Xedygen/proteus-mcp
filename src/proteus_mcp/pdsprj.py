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


def register_firmware(files: dict[str, bytes]) -> dict[str, bytes]:
    """Changes that list main.asm and Debug.exe in the firmware project's XML when it lists no files (the 2024
    lab 5 template ships `<FILES ATTACH="1"/>`, so Proteus ignores a stored Debug.exe and falls back to the
    8086's stale PROGRAM path).  Written the way Proteus writes it in the labs' solutions; {} if not needed."""
    name = firmware_name(files)
    key = f"FIRMWARE/{name}.XML" if name else None
    if not key or key not in files:
        return {}
    xml = files[key].decode("utf-8")
    if not re.search(r"<FILES ATTACH=\"1\"\s*/>", xml):
        return {}
    nl = "\r\n" if "\r\n" in xml else "\n"
    asm, exe, obj = f"{name}/main.asm", f"{name}/Debug/Debug.exe", f"{name}/Debug/main.obj"
    file_list = nl.join([' <FILES ATTACH="1">',
                         f'  <FILE ATTACH="1" GROUP="Source Files" NAME="{asm}"/>',
                         f'  <FILE ATTACH="1" GROUP="Firmware Files" NAME="{exe}" FIRMWARE="1"/>',
                         ' </FILES>'])
    xml = re.sub(r" <FILES ATTACH=\"1\"\s*/>", lambda m: file_list, xml, count=1)

    def tree(target: str) -> str:
        if target == "Debug":
            lines = [f'  <FILE TOOL="LD" NAME="{exe}">', f'   <FILE TOOL="ASM" NAME="{obj}">']
        else:
            lines = ['  <FILE TOOL="LD">', '   <FILE TOOL="ASM">']
        return nl.join(lines + [f'    <FILE NAME="{asm}"/>', "   </FILE>", "  </FILE>"]) + nl

    def add_tree(m: re.Match) -> str:
        return m.group(0) + tree(m.group("target"))
    xml = re.sub(r"<CONFIGURATION [^>]*TARGET=\"(?P<target>\w+)\"[^>]*>\s*<CONTROLLER [^>]*/>" + re.escape(nl),
                 add_tree, xml)
    return {key: xml.encode("utf-8")}


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
