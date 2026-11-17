"""MCP server (stdio) for Proteus 8.6.

Projects are always copied into the workspace (work/projects) before anything touches them; the originals,
for example the lab templates, are never modified.  One Proteus instance at a time is driven, and only one
that this server launched.
"""
from __future__ import annotations

import json
import re
import shutil
import struct
import time
from pathlib import Path

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import doctor, dsn, firmware, gui, library, paths, pdsprj, route, schematic
from .netlist import parse as parse_netlist

PROJECTS = paths.WORK_DIR / "projects"

INSTRUCTIONS = """\
Tools for Proteus 8.6 (Labcenter) on this Windows machine, aimed at the YTU BLM3061 8086 labs.

Typical flow for a lab PDF: lab_list() to find the lab's template (taslak.pdsprj) -> project_open ->
schematic_components (parts, pins, connection state) -> wire it offline with schematic_add_terminals (many
named nets / GND / VCC per call; schematic_add_terminal for one), schematic_connect (two free pins), schematic_tap (free pin onto an existing
net, junction), schematic_delete_wire, schematic_add_part, schematic_add_generator, schematic_rename_label,
schematic_delete / schematic_move (new device types: library_search, then schematic_place_devices) -> firmware_build(source=...)
(MASM 5.10, errors with line numbers) -> schematic_check (missing wires, lone terminals) -> proteus_launch -> net_of / netlist to check wiring -> sim_start ->
sim_log, screenshot, screenshot_part, sheet_image, terminal_screenshot, terminal_mode, sim_click, debug_window,
debug_memory -> sim_stop -> proteus_close -> project_export to hand the result over.
Annotated parts (U1 ...) are edited live: proteus_launch -> proteus_edit_component(ref, {...}, save=True).
If Proteus shows a dialog the tools do not know, dialog_answer reads or answers it.
Schematic edits need Proteus closed on that project. Proteus runs visibly; sim_click, terminal_mode and
schematic_place_devices move the real mouse, so the user should leave it alone while they run.

Read quirks() before writing 8086 code: the assembler is MASM 5.10 (no .IF/INVOKE/@@ labels, 128-char lines)
and the Proteus models have quirks (8251: SHR AL,1 after IN; 8259 EOIs; ADC0804 first read; POT-HG).
Every path argument may be absolute; projects are referred to afterwards by the name project_open returns.
"""

try:
    from importlib.metadata import version as _version
    _VERSION = _version("proteus-mcp")
except Exception:
    _VERSION = ""
mcp = MCPServer("proteus", instructions=INSTRUCTIONS, version=_VERSION)
_session: gui.Session | None = None
_register = mcp.tool


def _tool(*args, **kwargs):
    """mcp.tool, but any exception reaches the client with its message (the SDK reports only
    "Error executing tool X" for anything that is not a ToolError, which leaves the model guessing)."""
    import functools

    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*a, **k):
            try:
                return fn(*a, **k)
            except ToolError:
                raise
            except Exception as e:
                raise ToolError(f"{type(e).__name__}: {e}") from e
        return _register(*args, **kwargs)(wrapper)
    return decorate


mcp.tool = _tool


# ----- helpers ------------------------------------------------------------------------------
def _project(name: str) -> Path:
    stem = name[:-7] if name.lower().endswith(".pdsprj") else name
    for p in (PROJECTS / stem / f"{stem}.pdsprj", PROJECTS / f"{stem}.pdsprj"):   # own folder; flat = older copies
        if p.is_file():
            return p
    raise ValueError(f"no workspace project {name!r}; open one with project_open. Existing: {_workspace_names()}")


def _workspace_names() -> list[str]:
    return sorted({q.stem for q in PROJECTS.glob("*.pdsprj")}
                  | {q.stem for q in PROJECTS.glob("*/*.pdsprj") if q.parent.name == q.stem})


def _require_session() -> gui.Session:
    if _session is None or not _session.alive():
        raise ValueError("Proteus is not running; call proteus_launch first")
    return _session


def _exe_load_size(exe: bytes) -> int:
    """Size of the load image of an MZ executable (what the 8086 model reports as 'Program Loaded')."""
    last, pages, _, header = struct.unpack_from("<HHHH", exe, 2)
    size = pages * 512 - ((512 - last) if last else 0)
    return size - header * 16


def _firmware_check(log: list[dict], expected: int | None) -> dict:
    loaded = None
    for row in log:
        m = re.match(r"Program Loaded: (\d+) bytes", row["message"])
        if m:
            loaded = int(m.group(1))
    out = {"program_loaded_bytes": loaded}
    if expected is not None:
        out["expected_bytes"] = expected
        out["matches_project_exe"] = loaded == expected
    return out


def _expected_load(project: Path) -> int | None:
    files = pdsprj.entries(project)
    fp = pdsprj.firmware_paths(files)
    exe = files.get(fp[1]) if fp else None
    if exe is None and _external_exe(project).exists():
        exe = _external_exe(project).read_bytes()
    return _exe_load_size(exe) if exe else None


def _external_exe(project: Path) -> Path:
    """Program of a project without a firmware project (see firmware_build), kept beside the project."""
    return project.with_name(project.stem + "_8086.exe")


def _external_asm(project: Path) -> Path:
    return project.with_name(project.stem + "_8086.asm")


def _cpu_ref(project: Path) -> str | None:
    return next((c["ref"] for c in schematic.components(project, with_pins=False)
                 if c["device"].upper() in ("8086", "8088")), None)


# ----- environment -----------------------------------------------------------------------------
@mcp.tool()
def proteus_doctor() -> dict:
    """Check the environment: Proteus install and version, DOSBox, MASM, running Proteus processes."""
    return doctor.report()


@mcp.tool()
def proteus_background(on: bool | None = None) -> dict:
    """Background mode (default off; PROTEUS_MCP_BACKGROUND=1 turns it on): Proteus starts without focus and
    stays off-screen. The oscilloscope does not draw there. Simulation, logs, netlists, screenshots and file edits work; sim_click and
    schematic_place_devices need it off (they move the real mouse). Pass on=false/true to change it, or
    nothing to read it. Applies to the next proteus_launch (and immediately to clicks)."""
    if on is not None:
        gui.BACKGROUND["on"] = bool(on)
        if _session is not None and _session.alive():
            if on:
                _session.hide()
            else:
                gui.win32gui.ShowWindow(_session.main, gui.win32con.SW_MAXIMIZE)
                _session.show_popups()
    return {"background": gui.BACKGROUND["on"]}


@mcp.tool()
def quirks() -> str:
    """Rules for writing lab code that builds and simulates here (MASM 5.10 limits, Proteus model quirks)."""
    return paths.QUIRKS_MD.read_text(encoding="utf-8")


@mcp.tool()
def lab_list(filter: str = "") -> list[dict]:
    """Lab folders under labs/ that have an assignment PDF (tanım.pdf) or a template (taslak.pdsprj or
    proteus_sablon.pdsprj), with absolute paths: open the template with project_open. `filter` matches the
    folder path, e.g. "2025/lab8". A lab without a template starts from scratch or from a reference.
    Other .pdsprj files in a folder (student solutions) are listed as references, not as starting points.
    The labs folder is PROTEUS_MCP_LABS (default: labs/ in a source checkout, %LOCALAPPDATA%\proteus-mcp\labs
    when installed); empty when it does not exist. Any .pdsprj path also works with project_open directly."""
    labs = paths.LABS_DIR
    if not labs.is_dir():
        return []
    out = []
    for d in sorted({p.parent for p in labs.glob("**/*") if p.suffix.lower() in (".pdf", ".pdsprj")}):
        rel = d.relative_to(labs).as_posix()
        if filter and filter.lower() not in rel.lower():
            continue
        pdfs = sorted(str(p) for p in d.glob("*.pdf"))
        # 2025 templates are taslak.pdsprj, 2024 ones proteus_sablon.pdsprj
        template = next((str(p) for p in sorted(d.glob("*.pdsprj"))
                         if re.search(r"taslak|sablon|şablon|template", p.stem, re.I)), None)
        if not pdfs and not template:
            continue
        others = sorted(str(p) for p in d.glob("**/*.pdsprj") if str(p) != template)
        out.append({"lab": rel, "pdf": pdfs, "template": template, "references": others})
    return out


# ----- projects ----------------------------------------------------------------------------------
@mcp.tool()
def project_open(path: str, name: str | None = None, overwrite: bool = False) -> dict:
    """Copy a .pdsprj into the workspace and return its name and contents.

    The original file is never modified. `name` defaults to "<folder>_<stem>" (e.g. lab8_taslak), since all
    lab templates are called taslak.pdsprj. If a workspace project with that name exists it is kept and the
    result says reused=true (pass overwrite=True for a fresh copy); a name already taken by a copy of a
    different file is refused.
    """
    src = Path(path)
    if not src.is_file():
        raise ValueError(f"not a file: {src}")
    default = f"{src.parent.name}_{src.stem}"
    stem = re.sub(r"[^A-Za-z0-9_.-]", "_", name or default)  # Proteus 8.6 can fail on non-ASCII paths
    stem = re.sub(r"\.pdsprj$", "", stem, flags=re.I).strip(".")
    if not stem:
        raise ValueError(f"unusable project name {name!r}")
    # each project in its own folder, so files it refers to by name (ROM images 00.bin ...) cannot overwrite
    # another project's; a flat copy left by an older version is still used until it is overwritten
    flat = PROJECTS / f"{stem}.pdsprj"
    folder = PROJECTS / stem
    dst = flat if flat.exists() and not overwrite else folder / f"{stem}.pdsprj"
    origin = dst.with_suffix(".source")
    reused = dst.exists() and not overwrite
    if dst.exists() and overwrite and _session is not None and _session.alive() and _session.project.resolve() == dst.resolve():
        raise ValueError(f"{stem} is open in Proteus; call proteus_close before overwriting it")
    if reused and origin.exists() and origin.read_text(encoding="utf-8") != str(src.resolve()):
        raise ValueError(f"workspace project {stem} is a copy of {origin.read_text(encoding='utf-8')}; pass "
                         "another name or overwrite=True")
    copied = []
    if overwrite:                     # a fresh copy: no program, netlist or ROM image left from the last one
        if folder.is_dir():
            shutil.rmtree(folder)
        for f in (flat, flat.with_suffix(".source"), _external_exe(flat), _external_asm(flat)):
            f.unlink(missing_ok=True)
    if not reused:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        origin.write_text(str(src.resolve()), encoding="utf-8")
        for f in pdsprj.referenced_files(src):       # e.g. ROM images named in FILE properties
            shutil.copyfile(f, dst.parent / f.name)
            copied.append(f.name)
    return project_info(stem) | {"source": str(src.resolve()), "reused": reused, "files_copied": copied}


@mcp.tool()
def project_info(project: str) -> dict:
    """Entries of a workspace project, its firmware source location and the size of the stored EXE."""
    p = _project(project)
    files = pdsprj.entries(p)
    fp = pdsprj.firmware_paths(files)
    exe = files.get(fp[1]) if fp else None
    if not fp and _external_exe(p).exists():         # template without a firmware project: program beside it
        exe = _external_exe(p).read_bytes()
    return {"project": p.stem, "path": str(p), "entries": {k: len(v) for k, v in files.items()},
            "firmware_source": fp[0] if fp else (str(_external_asm(p)) if _external_asm(p).exists() else None),
            "firmware_exe_bytes": len(exe) if exe else 0,
            "firmware_load_bytes": _exe_load_size(exe) if exe else None}


@mcp.tool()
def project_export(project: str, dest: str) -> dict:
    """Copy a workspace project to `dest` (a .pdsprj path), e.g. OgrenciNo_IsimSoyisim.pdsprj for handing in.
    Files the project refers to by relative name (ROM images such as 00.bin) are copied next to it.

    A project without a firmware project (the 2024 templates) gets its program copied beside it as
    <dest stem>_8086.exe/.asm, and the 8086's Program File is then set to that file in Proteus: this launches
    Proteus on the exported copy, edits and saves it, then opens it once more to check the program loads
    (about a minute; needs Proteus closed on that file). If that fails, the result has a "note" instead."""
    p = _project(project)
    d = Path(dest)
    if d.suffix.lower() != ".pdsprj":
        raise ValueError("dest must end with .pdsprj")
    d.parent.mkdir(parents=True, exist_ok=True)
    if d.resolve() != p.resolve():
        shutil.copyfile(p, d)
    extra = []
    for f in pdsprj.referenced_files(p):                 # ROM images etc. must sit next to the project
        if (d.parent / f.name).resolve() != Path(f).resolve():
            shutil.copyfile(f, d.parent / f.name)
        extra.append(f.name)
    out = {"exported": str(d.resolve()), "bytes": d.stat().st_size, "files_copied": extra}
    if not pdsprj.firmware_paths(pdsprj.entries(p)) and _external_exe(p).exists():
        # a template without a firmware project (2024): hand the program over beside the project
        for f, suffix in ((_external_exe(p), "_8086.exe"), (_external_asm(p), "_8086.asm")):
            if f.exists():
                shutil.copyfile(f, d.with_name(d.stem + suffix))
                extra.append(d.stem + suffix)
        try:
            out["program_file"] = _bake_program(d, d.stem + "_8086.exe")
        except Exception as e:                       # Proteus busy, non-ASCII path, unexpected dialog ...
            out["note"] = (f"the project has no firmware project; its program is {d.stem}_8086.exe beside it and "
                           f"could not be linked automatically ({e}). In Proteus, open the 8086 (Edit Component), "
                           "set Program File to that file once, then save.")
    return out


def _bake_program(project: Path, program: str) -> dict:
    """Set the 8086's Program File of an exported project to `program` (a file beside it, by relative name) in
    Proteus's own Edit Component dialog and save with Proteus, then open it again and check that the program
    loads.  Proteus keeps that property in ROOT.CDB, which only Proteus may write."""
    if _session is not None and _session.alive():
        raise ValueError("Proteus is already running (proteus_close first)")
    if not str(project.resolve()).isascii():
        raise ValueError("Proteus 8.6 does not open non-ASCII project paths")
    cpu = _cpu_ref(project)
    want = _exe_load_size((project.parent / program).read_bytes())
    s = gui.Session.launch(project)
    try:
        s.edit_component(cpu, _field_labels(project, ["PROGRAM"]), {"PROGRAM": program})
        _compile_and_save(s)   # the dialog writes ROOT.DSN; Proteus copies it into ROOT.CDB when it compiles
    finally:
        _end(s)
    s = gui.Session.launch(project)                  # a fresh start must now load the program by itself
    try:
        s.sim("start", settle=2.0)
        loaded = _firmware_check(s.sim_log(), want)["program_loaded_bytes"]
        s.sim("stop")
    finally:
        _end(s)
    if loaded != want:
        raise RuntimeError(f"after saving, the 8086 loaded {loaded} bytes instead of {want}")
    return {"cpu": cpu, "program": program, "verified_load_bytes": loaded}


def _end(s: "gui.Session") -> None:
    """Close a Proteus started for a one-off job; end that process if it will not close."""
    try:
        if s.close().get("closed"):
            return
    except Exception:
        pass
    s.kill()


# ----- firmware ----------------------------------------------------------------------------------
@mcp.tool()
def firmware_get_source(project: str) -> str:
    """The 8086 assembly source stored in the project (FIRMWARE/<name>/main.asm), or for a template without a
    firmware project the source firmware_build kept beside it."""
    p = _project(project)
    files = pdsprj.entries(p)
    fp = pdsprj.firmware_paths(files)
    if not fp or fp[0] not in files:
        if _external_asm(p).exists():
            return _external_asm(p).read_text(encoding="cp437", errors="replace")
        raise ValueError("project has no firmware source (and no program built for it yet)")
    return files[fp[0]].decode("cp437", errors="replace")


@mcp.tool()
def firmware_build(project: str, source: str | None = None) -> dict:
    """Assemble and link the project's 8086 firmware with MASM 5.10 and store source and EXE in the project.

    If `source` is given it replaces main.asm first. On errors nothing is stored and the errors are returned
    with line numbers. Proteus must not have this project open (close it first): it keeps its own copy.
    """
    p = _project(project)
    if _session is not None and _session.alive() and _session.project.resolve() == p.resolve():
        raise ValueError("this project is open in Proteus; call proteus_close first")
    files = pdsprj.entries(p)
    fp = pdsprj.firmware_paths(files)
    if not fp:
        # e.g. the 2024 templates: an 8086 whose firmware project was removed.  Proteus only re-links a
        # program through its own dialogs (ROOT.CDB), so keep the EXE next to the project and attach it
        # through the Edit Component dialog each time proteus_launch starts Proteus.
        if _cpu_ref(p) is None:
            raise ValueError("project has no firmware project and no 8086 to run a program on")
        text = source if source is not None else (_external_asm(p).read_text(encoding="cp437")
                                                  if _external_asm(p).exists() else None)
        if text is None:
            raise ValueError("project has no firmware project yet; pass `source`")
        res = firmware.build(text)
        out = res.as_dict()
        if res.ok:
            _external_exe(p).write_bytes(res.exe)
            _external_asm(p).write_bytes(firmware.normalise_source(text))
            out["stored"] = {"exe": str(_external_exe(p)), "source": str(_external_asm(p)),
                             "load_bytes": _exe_load_size(res.exe),
                             "note": "this template has no firmware project: the program is kept next to the "
                                     "project and attached to the 8086 whenever proteus_launch starts Proteus"}
        return out
    text = source if source is not None else files[fp[0]].decode("cp437", errors="replace")
    res = firmware.build(text)
    out = res.as_dict()
    if res.ok:
        reg = pdsprj.register_firmware(files)          # a stub firmware project that lists no files
        pdsprj.replace(p, {fp[0]: firmware.normalise_source(text), fp[1]: res.exe, **reg})
        out["stored"] = {"source": fp[0], "exe": fp[1], "load_bytes": _exe_load_size(res.exe)}
        if reg:
            out["stored"]["registered"] = list(reg)
    return out


# ----- schematic (offline edits of ROOT.DSN) ---------------------------------------------------------
def _closed(project: str) -> Path:
    p = _project(project)
    if _session is not None and _session.alive() and _session.project.resolve() == p.resolve():
        raise ValueError("this project is open in Proteus; call proteus_close first (Proteus keeps its own copy)")
    return p


@mcp.tool()
def schematic_components(project: str, with_pins: bool = True, ref: str | None = None) -> list[dict]:
    """Parts on the schematic: reference (unannotated parts as DEVICE#ID, the same names the netlist uses),
    value, device, position in grid units (0.1 inch, y grows upwards), properties, and each pin's name,
    number, grid position and whether a wire is attached. Pass `ref` to get one part."""
    out = schematic.components(_project(project), with_pins)
    return [c for c in out if c["ref"].upper() == ref.upper()] if ref else out


@mcp.tool()
def schematic_connect(project: str, a: str, b: str, via_x_grid: float | None = None) -> dict:
    """Draw a wire between two unconnected pins, e.g. a="U2.Q1", b="U3.A0" (pin by name or number).

    The wire is routed orthogonally through a vertical column at `via_x_grid` (default: halfway). Written
    straight into the project file; Proteus must not have the project open. Check the result with
    proteus_launch + net_of."""
    return schematic.connect(_closed(project), a, b, via_x_grid)


@mcp.tool()
def schematic_add_part(project: str, like: str, x_grid: float, y_grid: float, rotation: int | None = None,
                       value: str | None = None, ref: str | None = None, mirror: str | None = None) -> dict:
    """Place a copy of a part already in the design, e.g. like="RES#0010" or like="U5" (a NOT gate), at a
    grid position (0.1 inch units, y up; schematic_components shows where things are). Gets reference `ref`
    (default: same prefix, next free number, e.g. U8) and optional new `value`. `rotation` 0/90/180/270
    (counter-clockwise) and `mirror` "x" (left-right) / "y" (top-bottom) / "none" replace the source's.
    Only devices already present in the file can be placed. Proteus may re-annotate on load; use the name
    netlist/net_of report. Refuses positions where a pin would land on something."""
    return schematic.add_part(_closed(project), like, x_grid, y_grid, rotation, value, ref, mirror)


@mcp.tool()
def library_search(query: str, limit: int = 30, category: str | None = None) -> dict:
    """Find device names in Proteus's installed libraries (offline, about 55,000 devices), e.g. "8255",
    "4-to-16 decoder", "led red", "7seg common anode". Every word must appear in the name, description,
    category, subcategory or manufacturer; exact and prefix name matches come first. `category` narrows it,
    e.g. "Microprocessor ICs", "TTL 74LS series", "Optoelectronics". Use the `device` value with
    schematic_place_devices. Names with a dot (74154.IEC) are symbol variants of the same part."""
    return library.search(query, limit, category)


def _check_device_names(devices: list[dict]) -> None:
    names = {d["device"].lower() for d in library.index()}
    if len(names) < 1000:                  # library folder missing or unreadable: let Proteus decide
        return
    for d in devices:
        if d["device"].lower() not in names:
            near = [x["device"] for x in library.search(d["device"].split()[0], limit=8)["devices"]]
            raise ValueError(f"no device named {d['device']!r} in the Proteus libraries"
                             + (f"; close names: {', '.join(near)}" if near else "; try library_search"))


@mcp.tool()
def schematic_place_devices(project: str, devices: list[dict]) -> list[dict]:
    """Place NEW device types from Proteus's libraries (e.g. a 74HC4078 or 4002 gate the template lacks).
    Find exact names with library_search; unknown names are refused before Proteus starts.

    `devices` is a list of {"device": "74HC4078", "x_grid": 0, "y_grid": -30}. Proteus is launched on the
    project, each device is picked in the Pick Devices dialog and dropped near the given point, the project is
    saved by Proteus (so ROOT.CDB stays consistent) and Proteus is closed. Proteus drops parts some grid steps
    away from the click, so the result is checked: a part that landed on a wire is undone and placed again,
    and each part is then moved to the requested spot if that is free. Returns the new parts with their
    actual reference, position and pins; pick an empty area (schematic_components shows what is where).
    Needs background mode off (real mouse). Multi-gate packages appear per gate, e.g. U9:A."""
    global _session
    p = _closed(project)
    if _session is not None and _session.alive():
        raise ValueError(f"Proteus is running on {_session.project.stem}; call proteus_close first")
    if gui.BACKGROUND["on"]:
        raise ValueError("placing devices needs the real mouse; background mode is on. Ask the owner before "
                         "turning it off with proteus_background(false)")
    _check_device_names(devices)
    comps = schematic.components(p, with_pins=False)
    before = {c["ref"] for c in comps}
    sheet = schematic.load(p)
    size = sheet.size()
    taken = route.Obstacles(sheet, pad=0).nodes
    hw, hh = size[0] / 2 / schematic.GRID, size[1] / 2 / schematic.GRID
    for d in devices:                   # a drop click on a part opens its Edit Component dialog instead
        x, y = round(float(d["x_grid"])), round(float(d["y_grid"]))
        if abs(x) > hw - 2 or abs(y) > hh - 2:
            raise ValueError(f"({x}, {y}) for {d['device']} is outside the sheet border (x within +-{hw - 2:g}, "
                             f"y within +-{hh - 2:g}); the editor shows only the sheet, so the drop would miss")
        if any((x + i, y + j) in taken for i in (-1, 0, 1) for j in (-1, 0, 1)):
            near = min(comps, key=lambda c: abs(c["grid"][0] - x) + abs(c["grid"][1] - y))
            raise ValueError(f"({x}, {y}) for {d['device']} is on or next to an existing part or wire (nearest part "
                             f"{near['ref']} at {near['grid']}); pick an empty area (schematic_components)")
    snapshot = p.read_bytes()           # restored before a retry and on failure: no stray parts are left
    G = schematic.GRID
    want = [(d["device"], float(d["x_grid"]), float(d["y_grid"])) for d in devices]
    shift = [0.0, 0.0]                  # Proteus drops parts some grid steps from the click; learned per call
    problem = "nothing placed"
    for _round in range(3):
        if _round:
            p.write_bytes(snapshot)
        s = gui.Session.launch(p)
        try:
            for dev, x, y in want:
                s.place_device(dev, s.sheet_frame(size), size,
                               (round((x + shift[0]) * G), round((y + shift[1]) * G)))
            s.save()
        except BaseException:
            _end(s)
            p.write_bytes(snapshot)
            raise
        _end(s)
        new = [c for c in schematic.components(p) if c["ref"] not in before]
        pool, matched = list(new), []
        for dev, x, y in want:
            hit = next((c for c in pool if c["device"].upper() == dev.upper()), None)
            if hit is not None:
                pool.remove(hit)
            matched.append(hit)
        found = [(m, x, y) for m, (_, x, y) in zip(matched, want) if m is not None]
        if found:                       # learn where Proteus actually drops relative to the click
            shift[0] -= sum(m["grid"][0] - x for m, x, y in found) / len(found)
            shift[1] -= sum(m["grid"][1] - y for m, x, y in found) / len(found)
        wired = [m["ref"] for m in matched if m is not None and any(q["connected"] for q in m["pins"])]
        if None in matched:
            problem = "a drop click missed"
        elif wired:
            # dropped onto an existing wire: Proteus connected the pins to it; start again further off
            problem = f"{wired} landed on existing wires"
        else:
            out = []
            for m, (_, x, y) in zip(matched, want):
                try:                    # put each part where it was asked for (it has no wires yet)
                    schematic.move_part(p, m["ref"], x, y)
                except Exception:
                    pass                # spot taken: keep it where Proteus dropped it
            moved = {c["ref"]: c for c in schematic.components(p)}
            return [moved[m["ref"]] for m in matched]
    p.write_bytes(snapshot)
    raise gui.GuiError(f"placing {[d['device'] for d in devices]} failed three times ({problem}); the project "
                       "was restored to its state before the call. Try another free area (x_grid/y_grid).")


@mcp.tool()
def schematic_check(project: str) -> dict:
    """Offline check before simulating: every part's visible pins that have no wire, and terminal names that
    appear only once on the sheet (their partner is missing or misspelled, e.g. CS8251 vs CS_8251). Spare pins
    are normal; look for the ones your design needs (clock, chip selects, RD/WR, data lines)."""
    return schematic.check(_project(project))


@mcp.tool()
def schematic_labels(project: str) -> dict:
    """Offline list of net names on the sheet: every terminal / wire label name with how often it appears and
    the pins wired straight to it, e.g. to find a template's placeholder labels (YA1..) or existing nets
    (A0..A19, AD0..AD15, $RD$), and clock generators with their frequency. Use net_of in a running Proteus
    for complete nets."""
    return schematic.labels(_project(project))


@mcp.tool()
def schematic_move(project: str, ref: str, x_grid: float, y_grid: float) -> dict:
    """Move a part to a grid position (e.g. after schematic_place_devices, whose drop point is only
    approximate). Wires on its pins are dragged along and kept orthogonal (connections stay); check the
    look with a screenshot afterwards. Returns the new pin positions."""
    return schematic.move_part(_closed(project), ref, x_grid, y_grid)


@mcp.tool()
def schematic_tap(project: str, pin: str, target: str, at_grid: list[float] | None = None) -> dict:
    """Wire an unconnected pin onto a net that already has wires, with a junction dot (a T connection):
    `target` is a wired pin ("U2.Q1") or a terminal / wire-label name ("A0"). The nearest free point on that
    net's drawn wires is used, or `at_grid` [x, y] on one of them. Use it when schematic_connect refuses
    because the other pin already has a wire (e.g. a second gate input on the same signal, a pull-up)."""
    return schematic.tap(_closed(project), pin, target, at_grid)


@mcp.tool()
def schematic_delete_wire(project: str, pin: str | None = None, at_grid: list[float] | None = None) -> dict:
    """Delete one wire: the wire on pin "REF.PIN", or the wire passing through grid point `at_grid` [x, y].
    A terminal at its end goes too; a junction left with two wires is merged back into one wire. Use it to
    undo a wrong connection or free a pin."""
    return schematic.delete_wire(_closed(project), pin, at_grid)


@mcp.tool()
def schematic_add_wire_label(project: str, target: str, name: str, at_grid: list[float] | None = None) -> dict:
    """Put a wire label on a net: `target` is a wired pin ("U3.A0") or an existing terminal / label name.
    Wires and terminals with the same name are one net, so this joins distant nets without drawing a wire
    (e.g. label two wires "CLK"). On a bus wire use a bus name such as AD[0..7]; a single member (AD3) of
    that bus is reached by a plain wire labelled AD3. `at_grid` [x, y] picks the segment."""
    return schematic.add_wire_label(_closed(project), target, name, at_grid)


@mcp.tool()
def schematic_delete(project: str, ref: str) -> dict:
    """Delete a part (by reference, e.g. "RV1") or a generator (by name) together with its wires; terminals
    and generators at the far ends of those wires go too, other pins just become unconnected. Parts that
    have a ROOT.CDB record (annotated and saved by Proteus, e.g. the template's own chips) are deleted by
    Proteus itself instead: it is launched on the project, the part is found with Search & Tag and deleted
    with its wires, the project is saved and Proteus is closed (no mouse), then the terminals, generators and
    junctions left hanging at the far ends are tidied the same way."""
    global _session
    p = _closed(project)
    try:
        return schematic.delete_part(p, ref)
    except dsn.DsnError as e:
        if "ROOT.CDB" not in str(e):
            raise
    if _session is not None and _session.alive():
        raise ValueError(f"Proteus is running on {_session.project.stem}; call proteus_close first")
    wires = len(schematic.load(p).wires())
    ends = schematic.far_ends(p, ref)
    snapshot = p.read_bytes()
    s = gui.Session.launch(p)
    try:
        s.delete_part(ref)
        s.save()
    except BaseException:
        _end(s)
        p.write_bytes(snapshot)
        raise
    _end(s)
    left = {c["ref"] for c in schematic.components(p, with_pins=False)}
    if ref in left:
        p.write_bytes(snapshot)
        raise gui.GuiError(f"{ref} is still in the saved file; the project was restored")
    tidy = schematic.prune_orphans(p, ends)     # Proteus leaves terminals / generators / junctions behind
    return {"deleted": ref, "wires": wires - len(schematic.load(p).wires()), "by": "Proteus (annotated part)",
            **tidy}


@mcp.tool()
def schematic_add_terminal(project: str, pin: str, net: str, distance_grid: int = 2) -> dict:
    """Attach a terminal to an unconnected pin, e.g. pin="U3.RESET", net="GND".

    net "GND" places a ground symbol, "VCC"/"POWER" a power symbol; a supply name such as "+12V", "-12V",
    "VEE" or "V+" a labelled power symbol (Proteus drives that rail at its voltage); any other name a named terminal,
    and all pins with terminals (or wire labels / bus members) of the same name are connected, e.g. "AD0"
    joins the AD0 line of an AD[0..15] bus. Placed `distance_grid` steps out on the side the pin faces;
    refuses a position that would touch another pin or wire."""
    return schematic.add_terminal(_closed(project), pin, net, distance_grid)


@mcp.tool()
def schematic_add_terminals(project: str, terminals: list[dict]) -> dict:
    """Many terminals in one call: `terminals` is a list of {"pin": "ROM00.A0", "net": "A1"} (optional
    "distance_grid"). Each is placed like schematic_add_terminal; one that fails is reported and the rest
    still go in. Use it for buses of address/data lines, e.g. 14 ROM address pins at once."""
    p = _closed(project)
    done, failed = [], []
    for t in terminals:
        try:
            r = schematic.add_terminal(p, t["pin"], t["net"], int(t.get("distance_grid", 2)))
            done.append({"pin": t["pin"], "net": t["net"], "resolved": r.get("resolved")})
        except Exception as e:
            failed.append({"pin": t.get("pin"), "net": t.get("net"), "error": f"{type(e).__name__}: {e}"})
    return {"placed": len(done), "failed": failed, "done": done}


@mcp.tool()
def schematic_add_generator(project: str, pin: str, frequency_hz: float, name: str | None = None,
                            distance_grid: int = 3) -> dict:
    """Attach a digital clock generator (square wave) to an unconnected left/right pin, e.g. the 8251's
    $TxC$ / $RxC$ at 9600 Hz (mode x1 -> 9600 baud). Each generator needs its own name (default "REF(PIN)").
    Not needed for an ADC0804 whose INTCLK property is 1 (internal clock)."""
    return schematic.add_generator(_closed(project), pin, frequency_hz, name, distance_grid)


@mcp.tool()
def schematic_rename_label(project: str, old: str, new: str) -> dict:
    """Rename all terminals / wire labels named `old` to `new`, e.g. YA6 -> $IO1$ (a $..$ name is written
    with an overbar). Same-named terminals and labels are connected, so this changes which net they join."""
    return schematic.rename_label(_closed(project), old, new)


@mcp.tool()
def schematic_set_property(project: str, ref: str, key: str, value: str) -> dict:
    """Set a {KEY=VALUE} property of an UNANNOTATED part (e.g. RES#0010, BUTTON#0007). Also sets the
    Digital Oscilloscope's panel ($IOSCILLOSCOPE#nnnn): CHAACDC=1 (channel A DC; 3 = off), CHAVAL=2 (V/div),
    HORIZVAL=0.5m (time/div); saved designs may have channels off.

    Annotated parts (U1, U2 ...) are refused: Proteus reads their properties from ROOT.CDB, which only Proteus
    may write (it hangs on a modified CDB). For those use proteus_launch, then proteus_edit_component with
    save=True. Keys and values cannot contain braces or line breaks."""
    return schematic.set_property(_closed(project), ref, key, value)


@mcp.tool()
def schematic_set_value(project: str, ref: str, value: str) -> dict:
    """Change the value of an UNANNOTATED part, e.g. a resistor RES#0010 to 330 (verified in Proteus).
    Annotated parts (U1 ...) are refused for the same ROOT.CDB reason as schematic_set_property: use
    proteus_edit_component(ref, {"VALUE": ...}, save=True) for those."""
    return schematic.set_value(_closed(project), ref, value)


# ----- Proteus process ---------------------------------------------------------------------------
@mcp.tool()
def proteus_launch(project: str) -> dict:
    """Start Proteus on a workspace project and wait for its window.

    Known harmless start-up warnings ('Symbol "$MKR..." used but not found in libraries') are answered and
    listed; any other dialog stops the launch and is reported.
    """
    global _session
    if _session is not None and _session.alive():
        raise ValueError(f"Proteus is already running on {_session.project.stem}; call proteus_close first")
    p = _project(project)
    _session = gui.Session.launch(p)
    out = {"project": project, "pid": _session.pid, "startup_warnings": sorted(set(_session.warnings)),
           "title": gui.win32gui.GetWindowText(_session.main)}
    if not pdsprj.firmware_paths(pdsprj.entries(p)) and _external_exe(p).exists() and _cpu_ref(p):
        # set for this Proteus session only (Proteus keeps the stored path in ROOT.CDB)
        _session.edit_component(_cpu_ref(p), _field_labels(p, ["PROGRAM"]), {"PROGRAM": str(_external_exe(p))})
        out["attached_program"] = str(_external_exe(p))
    return out


@mcp.tool()
def proteus_close(force: bool = False) -> dict:
    """Stop any simulation and close the Proteus instance this server started, without saving.
    If it does not close (e.g. an unknown dialog), the session is kept and the result says closed=false;
    force=True then ends that process (only ever the one this server started)."""
    global _session
    if _session is None or not _session.alive():
        _session = None
        return {"closed": True, "note": "nothing was running"}
    s = _session
    try:
        if s.sim_state() in ("running", "paused"):
            s.sim("stop")
    except gui.GuiError:
        pass
    try:
        res = s.close()
    except gui.GuiError as e:
        res = {"closed": False, "error": str(e)}
    if not res.get("closed") and force:
        s.kill()
        res = {"closed": True, "forced": True}
    if res.get("closed") or not s.alive():
        _session = None
    return res


def _shutdown() -> None:
    """When the client stops the server, do not leave an (off-screen) Proteus of ours running."""
    if _session is not None and _session.alive():
        try:
            _session.kill()
        except Exception:
            pass


import atexit  # noqa: E402

atexit.register(_shutdown)


# ----- simulation ----------------------------------------------------------------------------------
@mcp.tool()
def sim_start(wait_seconds: float = 3.0) -> dict:
    """Run (or resume) the simulation, wait, and report state, the simulation log, and whether the program
    the 8086 loaded has the size of the EXE stored in the project."""
    s = _require_session()
    if s.sim_state() == "running":           # already running: nothing to start, just wait and report
        time.sleep(max(wait_seconds, 0))
        state = "running"
    else:
        state = s.sim("start", settle=max(wait_seconds, 1.0))
    if s.sim_state() not in ("running", "paused"):
        errors = s.compiler_errors()
        raise ToolError("simulation did not start" + (": " + "; ".join(
            f"{r['message']} ({r['source']})" for r in errors) if errors else
            " (no compiler errors found; check screenshot)"))
    log = s.sim_log()
    return {"state": s.sim_state(), "requested_state": state, "log": log,
            "firmware": _firmware_check(log, _expected_load(s.project))}


@mcp.tool()
def sim_pause() -> dict:
    """Pause the simulation (debug popups such as registers and memory become readable)."""
    s = _require_session()
    return {"state": s.sim("pause")}


@mcp.tool()
def sim_stop() -> dict:
    """Stop the simulation."""
    s = _require_session()
    return {"state": s.sim("stop")}


@mcp.tool()
def sim_status() -> dict:
    """Simulation state (running, paused, stopped) and any open dialogs."""
    s = _require_session()
    return {"state": s.sim_state(), "dialogs": s.dialogs()}


@mcp.tool()
def proteus_edit_component(ref: str, properties: dict[str, str], save: bool = False) -> dict:
    """Change properties of any part, annotated ones included (e.g. {"CLOCK": "5MHz"} on the 8086,
    {"FILE": "00.bin"} on a ROM, {"PROGRAM": "C:/path/prog.exe"}, the value {"VALUE": "4.7k"} on a resistor or
    any part), through Proteus's own Edit Component dialog
    in the running Proteus. The simulation must be stopped.
    save=False: this Proteus session only. save=True: Proteus compiles the design once (a short simulation run,
    which copies the change into ROOT.CDB, where annotated parts' values live) and saves the project, so the
    change lasts. For unannotated parts schematic_set_property also makes a lasting change offline."""
    s = _require_session()
    if s.sim_state() in ("running", "paused"):
        raise ValueError("stop the simulation first (sim_stop)")
    values = {k: str(v) for k, v in properties.items()}
    res = s.edit_component(ref, _field_labels(s.project, properties), values)
    refused = {k: res["after"][k] for k, v in values.items() if res["after"].get(k) != v}
    if refused:                           # e.g. a drop-down list that only takes its own entries
        raise ToolError(f"the dialog did not take {sorted(refused)} (it shows {refused}); nothing was saved")
    if save:
        _compile_and_save(s)
    return {"ref": ref, "session_only": not save, **res}


def _compile_and_save(s: "gui.Session") -> None:
    """Run the design once, so Proteus copies dialog edits into ROOT.CDB, then save.  A design that does not
    start is not saved: Proteus would write ROOT.DSN only, which the next load ignores (docs/QUIRKS.md)."""
    state = s.sim("start", settle=2.0)
    if state not in ("running", "paused"):
        errors = s.compiler_errors()
        raise ToolError("the change is in this Proteus session but was not saved: the design did not start, so "
                        "Proteus would not store it" + (": " + "; ".join(
                            f"{r['message']} ({r['source']})" for r in errors) if errors else ""))
    s.sim("stop")
    s.save()


def _field_labels(project: Path, keys) -> dict[str, list[str]]:
    """Dialog labels of property keys, from the device definitions embedded in ROOT.DSN
    ({PROGRAM="Program File",FILENAME,...}); a key may have different labels on different devices."""
    data = pdsprj.entries(project)["ROOT.DSN"]
    common = {"VALUE": {"Part Value"}, "REF": {"Part Reference"}}   # fixed fields every dialog shows
    return {k: sorted({m.decode("latin-1") for m in re.findall(rb"\{" + re.escape(k.encode()) + rb'="([^"]+)"', data)}
                      | common.get(k.upper(), set()))
            for k in keys}


@mcp.tool()
def dialog_answer(button: str, title: str | None = None) -> dict:
    """Click a button (e.g. "OK", "No", "Cancel") in an open Proteus dialog, for dialogs the tools do not
    answer themselves (an UnknownDialog error names them; sim_status lists them). `title` picks the dialog
    when several are open."""
    s = _require_session()
    ds = [d for d in s.dialogs() if title is None or d["title"] == title]
    if not ds:
        raise ValueError(f"no open dialog{' titled ' + repr(title) if title else ''}; open: {s.dialogs()}")
    d = ds[0]
    if button not in [b.replace("&", "") for b in d["buttons"]]:
        raise ValueError(f"dialog {d['title']!r} has no button {button!r}; buttons: {d['buttons']}")
    gui._click_button(d["hwnd"], button, post=True)
    time.sleep(0.8)
    return {"clicked": button, "dialog": d["title"], "still_open": s.dialogs()}


@mcp.tool()
def sim_log() -> list[dict]:
    """Rows of the Simulation Log (messages and the part that produced them)."""
    return _require_session().sim_log()


# ----- observation --------------------------------------------------------------------------------
@mcp.tool()
def screenshot(window: str | None = None, max_width: int = 1600) -> Image:
    """Screenshot of the Proteus main window, or of a top-level window whose title matches `window`
    (regex, e.g. 'Oscilloscope', 'Virtual Terminal')."""
    s = _require_session()
    return Image(data=gui.png_bytes(s.screenshot(window), max_width), format="png")


@mcp.tool()
def sheet_image(dpi: int = 200, crop: list[float] | None = None, max_width: int = 2000,
                save_to: str | None = None) -> Image:
    """The whole current schematic sheet rendered by Proteus's Export Bitmap at `dpi` (100/200/300/400/600),
    much sharper than `screenshot` and independent of the zoom.  `crop` = [x0, y0, x1, y1] as fractions of
    the sheet (0..1) keeps one region; `save_to` also writes that (cropped) image there at full resolution, before
    `max_width` scales the returned copy."""
    s = _require_session()
    img = s.export_sheet(dpi)
    if crop:
        if len(crop) != 4 or not all(0 <= f <= 1 for f in crop) or crop[0] >= crop[2] or crop[1] >= crop[3]:
            raise ValueError("crop must be [x0, y0, x1, y1] fractions with x0 < x1 and y0 < y1")
        w, h = img.size
        img = img.crop((round(crop[0] * w), round(crop[1] * h), round(crop[2] * w), round(crop[3] * h)))
    if save_to:
        img.save(save_to, "PNG")
    return Image(data=gui.png_bytes(img, max_width), format="png")


def _address(a: int | str) -> int:
    if isinstance(a, int):
        return a
    t = a.strip().upper()
    if t.startswith("0X"):
        return int(t[2:], 16)
    if t.endswith("H"):
        return int(t[:-1], 16)
    return int(t, 16)                  # addresses written as text are hex, as in Proteus and the lab PDFs


@mcp.tool()
def debug_window(name: str, address: int | str | None = None) -> Image:
    """Open a Debug-menu popup while the simulation runs or is paused and return a screenshot of it.

    Examples: 'Registers - U1', 'Memory Dump - U1', '8255 Internal Status', 'Source Code - U1', 'Watch Window',
    'Simulation Log'. Pause first for a stable picture. The error lists the available items.
    `address` scrolls a memory popup there first (int, '0x6000' or '6000H'); debug_memory reads it as text.
    """
    s = _require_session()
    h = s.debug_window(name)
    time.sleep(0.5)
    if address is not None:
        s.memory_goto(h, _address(address))
    return Image(data=gui.png_bytes(gui.capture(h)), format="png")


@mcp.tool()
def debug_memory(name: str = "Memory Dump", address: int | str = 0, length: int = 256) -> dict:
    """Read a memory popup (CPU 'Memory Dump - U1', or a RAM/ROM part's 'Memory Contents - U3') as hex text,
    `length` bytes (max 4096) from `address` (an int, or text read as hex: '0x6000', '6000H', '6000'), 16 per row. Simulation running or
    paused; pause for a consistent picture. `range` gives the addresses the popup holds (the 8086 model's
    own memory is its internal RAM, not RAM/ROM chips on the bus: read those through their own popup)."""
    s = _require_session()
    length = max(1, min(int(length), 4096))
    start = _address(address)
    h = s.debug_window(name)
    mem = s.parse_memory_text(s.memory_text(h))
    if not mem:
        raise ToolError(f"{gui.win32gui.GetWindowText(h)!r} copied no memory rows")
    lo, hi = min(mem), max(mem)
    rows = []
    for base in range(start - start % 16, start + length, 16):
        cells = [f"{mem[a]:02X}" if a in mem and start <= a < start + length else "  "
                 for a in range(base, base + 16)]
        if any(c.strip() for c in cells):
            rows.append(f"{base:05X}: {' '.join(cells)}".rstrip())
    out = {"window": gui.win32gui.GetWindowText(h), "range": [f"{lo:05X}", f"{hi:05X}"], "rows": rows}
    if not rows:
        out["note"] = f"{start:05X}..{start + length - 1:05X} is outside what this popup holds"
    return out


def _part_screen_box(s: gui.Session, ref: str, margin_grid: float = 2.0):
    sheet = schematic.load(s.project)
    comp = sheet.component(ref)
    from .devices import pins as part_pins
    pts = [comp["origin"]] + [p["xy"] for p in part_pins(sheet, comp) if not p["hidden"]]
    size = sheet.size()
    frame = s.sheet_frame(size)
    m = margin_grid * schematic.GRID
    corners = [s.sheet_to_screen(frame, size, (x, y))
               for x in (min(p[0] for p in pts) - m, max(p[0] for p in pts) + m)
               for y in (min(p[1] for p in pts) - m, max(p[1] for p in pts) + m)]
    centre = s.sheet_to_screen(frame, size, comp["origin"])
    return centre, (min(c[0] for c in corners), min(c[1] for c in corners),
                    max(c[0] for c in corners), max(c[1] for c in corners))


@mcp.tool()
def sim_click(ref: str, hold_seconds: float = 0.3, settle_seconds: float = 0.8, dx_grid: float = 0.0,
              dy_grid: float = 0.0, times: int = 1) -> dict:
    """Press a part during a running simulation: a BUTTON is held for `hold_seconds` then released; a
    LOGICSTATE or switch toggles. Uses the real mouse on the Proteus window (zooms to the whole sheet first);
    background mode must be off. Follow with screenshot_part to see the effect.

    dx_grid/dy_grid shift the click from the part's origin (sheet grid units, y up) to hit a part's own
    controls, e.g. an unrotated POT-HG: dx_grid=-1.27, dy_grid=1.05 raises the wiper, dy_grid=-1.05 lowers it,
    about one step per 0.2 s of hold_seconds; `times` repeats the click."""
    s = _require_session()
    if s.sim_state() != "running":
        raise ValueError("the simulation must be running (sim_start)")
    if dx_grid or dy_grid:
        comp = schematic.load(s.project).component(ref)
        size = schematic.load(s.project).size()
        frame = s.sheet_frame(size)
        x, y = s.sheet_to_screen(frame, size, (comp["origin"][0] + dx_grid * schematic.GRID,
                                               comp["origin"][1] + dy_grid * schematic.GRID))
    else:
        (x, y), _ = _part_screen_box(s, ref)
    if not getattr(s, "_canvas_primed", False):
        # the first press after launch was seen to get lost (focus goes to the canvas first): click an
        # empty corner of the editing canvas, outside the sheet, once per session
        l, t, r, b = gui.win32gui.GetWindowRect(s.edit_window())
        s.real_click(l + 4, t + 4, hold=0.05)
        time.sleep(0.3)
        s._canvas_primed = True
    for _ in range(max(times, 1)):
        s.real_click(x, y, hold=hold_seconds)
        time.sleep(0.15)
    time.sleep(settle_seconds)
    return {"clicked": ref, "screen": [x, y], "times": times, "state": s.sim_state()}


@mcp.tool()
def terminal_type(text: str) -> dict:
    """Type text into the Virtual Terminal(s) during a running simulation (works in background mode).
    The text goes to every Virtual Terminal window; only a terminal whose TXD pin is wired passes it on.
    Read the result with terminal_screenshot."""
    s = _require_session()
    if s.sim_state() != "running":
        raise ValueError("the simulation must be running (sim_start)")
    n = s.terminal_type(text)
    if not n:
        raise ValueError("no Virtual Terminal window is open")
    return {"typed": text, "terminals": n}


@mcp.tool()
def terminal_mode(item: str = "Hex Display Mode", on: bool = True, terminal: int = 0) -> dict:
    """Switch a Virtual Terminal right-click option during a running simulation: "Hex Display Mode" (shows
    received bytes as hex, as labs ask for raw data), "Echo Typed Characters", "Pause". `terminal` is the
    index into the open terminal windows. Uses the real mouse (background mode must be off)."""
    s = _require_session()
    if s.sim_state() != "running":
        raise ValueError("the simulation must be running (sim_start)")
    windows = s.terminal_windows()
    if terminal >= len(windows):
        raise ValueError(f"only {len(windows)} Virtual Terminal window(s) open")
    return s.terminal_mode(item, on, windows[terminal])


@mcp.tool()
def terminal_screenshot() -> list[Image]:
    """Screenshots of every Virtual Terminal window (in window order), to read what was printed."""
    s = _require_session()
    hs = s.terminal_windows()
    if not hs:
        raise ValueError("no Virtual Terminal window is open")
    return [Image(data=gui.png_bytes(gui.capture(h)), format="png") for h in hs]


@mcp.tool()
def screenshot_part(ref: str, margin_grid: float = 3.0) -> Image:
    """Close-up screenshot of one part on the schematic (e.g. the 7-segment display, an LED, a probe),
    during or outside a simulation. Zooms to the whole sheet first."""
    s = _require_session()
    _, box = _part_screen_box(s, ref, margin_grid)
    ml, mt, _, _ = gui.win32gui.GetWindowRect(s.main)
    # only the editing canvas shows the sheet; outside it the crop would come back black
    el, et, er, eb = gui.win32gui.GetWindowRect(s.edit_window())
    vis = (max(box[0], el), max(box[1], et), min(box[2], er), min(box[3], eb))
    if vis[2] - vis[0] < 20 or vis[3] - vis[1] < 20:
        raise ValueError(f"{ref} is outside the visible area: the view is zoomed to the sheet border and {ref} "
                         "was placed beyond it. Use screenshot for the whole window, or a part inside the border")
    img = s.screenshot().crop((vis[0] - ml, vis[1] - mt, vis[2] - ml, vis[3] - mt))
    if img.width < 300:
        img = img.resize((img.width * 2, img.height * 2))
    return Image(data=gui.png_bytes(img), format="png")


@mcp.tool()
def netlist(include_single_pin_nets: bool = False) -> dict:
    """Proteus's own netlist of the open project (Netlist Compiler, logical mode): parts and, for every net,
    the pins on it as REF.PIN. Unannotated parts appear as DEVICE#id. The simulation must be stopped the first
    time; the result is kept for the rest of this Proteus session (the design cannot change while it is open).
    Proteus leaves clock/DC generators out of its netlist; they are listed under "generators" with the pins
    and nets their wires reach (read from the drawing)."""
    s = _require_session()
    n, path = _session_netlist(s)
    out = n.summary(include_single_pin_nets) | {"file": str(path)}
    try:
        gens = schematic.generators(s.project)
    except Exception:
        gens = []
    if gens:
        # Proteus leaves generators out of its netlist; they are read from the drawing instead
        for g in gens:
            g["nets"] = sorted({n.net_of(*p.split(".", 1)) for p in g["pins"] if "." in p} - {None})
        out["generators"] = gens
    return out


_NETLIST: dict = {}


def _session_netlist(s: gui.Session):
    if _NETLIST.get("session") is not s:
        path = s.netlist(s.project.with_suffix(".sdf"))  # one file per project, replaced each session
        _NETLIST.update(session=s, netlist=parse_netlist(path.read_text(encoding="latin-1")), path=path)
    return _NETLIST["netlist"], _NETLIST["path"]


@mcp.tool()
def net_of(ref: str, pin: str) -> dict:
    """Which net a pin is on, and every pin on that net. Pin by name as in the netlist (e.g. U3 PA0, U1 WR/LOCK;
    overbars are dropped: $CS$ -> CS). An unconnected pin has a net of its own with just that pin; an unknown
    part or pin is an error that lists the valid names."""
    s = _require_session()
    n, _ = _session_netlist(s)
    refs = set(n.parts) | {r for members in n.nets.values() for r, _ in members}
    if ref.upper() not in {r.upper() for r in refs}:
        raise ValueError(f"no part {ref!r} in the netlist; parts: {sorted(refs)}. Parts added offline can be "
                         "renumbered by Proteus when it loads the project (U14 may appear as U3): find the new name in "
                         "netlist() by device and pins")
    name = n.net_of(ref, pin)
    if name is None:
        raise ValueError(f"{ref} has no pin {pin!r}; its pins: {sorted(n.pins_of(ref))}")
    members = [f"{r}.{p}" for r, p in n.nets.get(name, [])]
    out = {"net": name, "members": members}
    gens = _generators_on(s, members)
    if gens:
        out["generators"] = gens
    return out


def _norm_pin(m: str) -> str:
    return m.replace("$", "").upper()


def _generators_on(s: gui.Session, members: list[str]) -> list[dict]:
    """Generators (absent from Proteus's netlist) whose wires reach any of these pins, from ROOT.DSN."""
    try:
        gens = schematic.generators(s.project)
    except Exception:
        return []
    want = {_norm_pin(m) for m in members}
    return [g for g in gens if want & {_norm_pin(p) for p in g["pins"]}]


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
