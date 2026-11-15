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

from . import doctor, firmware, gui, paths, pdsprj, schematic
from .netlist import parse as parse_netlist

PROJECTS = paths.WORK_DIR / "projects"

INSTRUCTIONS = """\
Tools for Proteus 8.6 (Labcenter) on this Windows machine, aimed at the YTU BLM3061 8086 labs.

Typical flow for a lab PDF: lab_list() to find the lab's template (taslak.pdsprj) -> project_open ->
schematic_components (parts, pins, connection state) -> wire it offline with schematic_add_terminal (named
nets, GND/VCC), schematic_connect, schematic_add_part, schematic_add_generator, schematic_rename_label,
schematic_delete / schematic_move (new device types: schematic_place_devices) -> firmware_build(source=...)
(MASM 5.10, errors with line numbers) -> proteus_launch -> net_of / netlist to check wiring -> sim_start ->
sim_log, screenshot, screenshot_part, terminal_screenshot, terminal_mode, sim_click, debug_window -> sim_stop
-> proteus_close -> project_export to hand the result over.
Schematic edits need Proteus closed on that project. Mouse-based tools (sim_click, terminal_mode,
schematic_place_devices) need proteus_background(false).

Read quirks() before writing 8086 code: the assembler is MASM 5.10 (no .IF/INVOKE/@@ labels, 128-char lines)
and the Proteus models have quirks (8251: SHR AL,1 after IN; 8259 EOIs; ADC0804 first read; POT-HG).
Every path argument may be absolute; projects are referred to afterwards by the name project_open returns.
"""

mcp = MCPServer("proteus", instructions=INSTRUCTIONS)
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
    p = PROJECTS / (name if name.lower().endswith(".pdsprj") else name + ".pdsprj")
    if not p.is_file():
        raise ValueError(f"no workspace project {name!r}; open one with project_open. "
                         f"Existing: {[q.stem for q in PROJECTS.glob('*.pdsprj')]}")
    return p


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
    return _exe_load_size(exe) if exe else None


# ----- environment -----------------------------------------------------------------------------
@mcp.tool()
def proteus_doctor() -> dict:
    """Check the environment: Proteus install and version, DOSBox, MASM, running Proteus processes."""
    return doctor.report()


@mcp.tool()
def proteus_background(on: bool | None = None) -> dict:
    """Background mode (default on): Proteus starts without focus and stays off-screen, so the user can keep
    working. Simulation, logs, netlists, screenshots and file edits work; sim_click and
    schematic_place_devices need it off (they move the real mouse). Pass on=false/true to change it, or
    nothing to read it. Applies to the next proteus_launch (and immediately to clicks)."""
    if on is not None:
        gui.BACKGROUND["on"] = bool(on)
        if _session is not None and _session.alive():
            if on:
                _session.hide()
            else:
                gui.win32gui.ShowWindow(_session.main, gui.win32con.SW_MAXIMIZE)
    return {"background": gui.BACKGROUND["on"]}


@mcp.tool()
def quirks() -> str:
    """Rules for writing lab code that builds and simulates here (MASM 5.10 limits, Proteus model quirks)."""
    return paths.QUIRKS_MD.read_text(encoding="utf-8")


@mcp.tool()
def lab_list(filter: str = "") -> list[dict]:
    """Lab folders under labs/ that have an assignment PDF (tanım.pdf) or a template (taslak.pdsprj), with
    absolute paths: open the template with project_open. `filter` matches the folder path, e.g. "2025/lab8".
    Other .pdsprj files in a folder (student solutions) are listed as references, not as starting points."""
    labs = paths.REPO / "labs"
    out = []
    for d in sorted({p.parent for p in labs.glob("**/*") if p.suffix.lower() in (".pdf", ".pdsprj")}):
        rel = d.relative_to(labs).as_posix()
        if filter and filter.lower() not in rel.lower():
            continue
        pdfs = sorted(str(p) for p in d.glob("*.pdf"))
        template = next((str(p) for p in d.glob("taslak*.pdsprj")), None)
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
    dst = PROJECTS / f"{stem}.pdsprj"
    origin = PROJECTS / f"{stem}.source"
    reused = dst.exists() and not overwrite
    if reused and origin.exists() and origin.read_text(encoding="utf-8") != str(src.resolve()):
        raise ValueError(f"workspace project {stem} is a copy of {origin.read_text(encoding='utf-8')}; pass "
                         "another name or overwrite=True")
    copied = []
    if not reused:
        PROJECTS.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        origin.write_text(str(src.resolve()), encoding="utf-8")
        for f in pdsprj.referenced_files(src):       # e.g. ROM images named in FILE properties
            shutil.copyfile(f, PROJECTS / f.name)
            copied.append(f.name)
    return project_info(stem) | {"source": str(src.resolve()), "reused": reused, "files_copied": copied}


@mcp.tool()
def project_info(project: str) -> dict:
    """Entries of a workspace project, its firmware source location and the size of the stored EXE."""
    p = _project(project)
    files = pdsprj.entries(p)
    fp = pdsprj.firmware_paths(files)
    exe = files.get(fp[1]) if fp else None
    return {"project": p.stem, "path": str(p), "entries": {k: len(v) for k, v in files.items()},
            "firmware_source": fp[0] if fp else None,
            "firmware_exe_bytes": len(exe) if exe else 0,
            "firmware_load_bytes": _exe_load_size(exe) if exe else None}


@mcp.tool()
def project_export(project: str, dest: str) -> dict:
    """Copy a workspace project to `dest` (a .pdsprj path), e.g. OgrenciNo_IsimSoyisim.pdsprj for handing in.
    Files the project refers to by relative name (ROM images such as 00.bin) are copied next to it."""
    p = _project(project)
    d = Path(dest)
    if d.suffix.lower() != ".pdsprj":
        raise ValueError("dest must end with .pdsprj")
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(p, d)
    extra = []
    for f in pdsprj.referenced_files(p):                 # ROM images etc. must sit next to the project
        shutil.copyfile(f, d.parent / f.name)
        extra.append(f.name)
    return {"exported": str(d.resolve()), "bytes": d.stat().st_size, "files_copied": extra}


# ----- firmware ----------------------------------------------------------------------------------
@mcp.tool()
def firmware_get_source(project: str) -> str:
    """The 8086 assembly source stored in the project (FIRMWARE/<name>/main.asm)."""
    files = pdsprj.entries(_project(project))
    fp = pdsprj.firmware_paths(files)
    if not fp or fp[0] not in files:
        raise ValueError("project has no firmware source")
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
        raise ValueError("project has no firmware project (FIRMWARE.XML)")
    text = source if source is not None else files[fp[0]].decode("cp437", errors="replace")
    res = firmware.build(text)
    out = res.as_dict()
    if res.ok:
        pdsprj.replace(p, {fp[0]: firmware.normalise_source(text), fp[1]: res.exe})
        out["stored"] = {"source": fp[0], "exe": fp[1], "load_bytes": _exe_load_size(res.exe)}
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
                       value: str | None = None, ref: str | None = None) -> dict:
    """Place a copy of a part already in the design, e.g. like="RES#0010" or like="U5" (a NOT gate), at a
    grid position (0.1 inch units, y up; schematic_components shows where things are). Gets reference `ref`
    (default: same prefix, next free number, e.g. U8) and optional new `value`. Only devices already present
    in the file can be placed. Proteus may re-annotate on load; use the name netlist/net_of report.
    Refuses positions where a pin would land on something."""
    return schematic.add_part(_closed(project), like, x_grid, y_grid, rotation, value, ref)


@mcp.tool()
def schematic_place_devices(project: str, devices: list[dict]) -> list[dict]:
    """Place NEW device types from Proteus's libraries (e.g. a 74HC4078 or 4002 gate the template lacks).

    `devices` is a list of {"device": "74HC4078", "x_grid": 0, "y_grid": -30}. Proteus is launched on the
    project, each device is picked in the Pick Devices dialog and dropped near the given point, the project is
    saved by Proteus (so ROOT.CDB stays consistent) and Proteus is closed. Returns the new parts with their
    actual reference, position and pins (positions land a few grid steps from the request; wire with the
    returned pin positions). Multi-gate packages appear per gate, e.g. U9:A."""
    global _session
    p = _closed(project)
    if _session is not None and _session.alive():
        raise ValueError(f"Proteus is running on {_session.project.stem}; call proteus_close first")
    before = {c["ref"] for c in schematic.components(p, with_pins=False)}
    sheet = schematic.load(p)
    size = sheet.size()
    s = gui.Session.launch(p)
    try:
        todo = list(devices)
        for _attempt in range(3):          # a drop click occasionally misses; check the saved file and retry
            for d in todo:
                frame = s.sheet_frame(size)
                s.place_device(d["device"], frame, size,
                               (round(float(d["x_grid"]) * schematic.GRID),
                                round(float(d["y_grid"]) * schematic.GRID)))
            s.save()
            have = [c["device"].upper() for c in schematic.components(p, with_pins=False) if c["ref"] not in before]
            missing = []
            for d in devices:
                if d["device"].upper() in have:
                    have.remove(d["device"].upper())
                else:
                    missing.append(d)
            todo = missing
            if not todo:
                break
    finally:
        if s.alive():
            try:
                s.close()
            except gui.GuiError:
                s.kill()   # only ever this server's own Proteus process
    placed = [c for c in schematic.components(p) if c["ref"] not in before]
    if todo:
        raise gui.GuiError(f"asked for {[d['device'] for d in devices]} but only "
                           f"{[c['ref'] + ' ' + c['device'] for c in placed]} appeared after saving")
    return placed


@mcp.tool()
def schematic_move(project: str, ref: str, x_grid: float, y_grid: float) -> dict:
    """Move an unwired part to a grid position (use after schematic_place_devices, whose drop point is only
    approximate, before wiring it). Returns the new pin positions."""
    return schematic.move_part(_closed(project), ref, x_grid, y_grid)


@mcp.tool()
def schematic_delete(project: str, ref: str) -> dict:
    """Delete a part (by reference, e.g. "RV1") or a generator (by name) together with its wires; terminals
    and generators at the far ends of those wires go too, other pins just become unconnected. Parts that
    have a ROOT.CDB record (annotated and saved by Proteus) are refused."""
    return schematic.delete_part(_closed(project), ref)


@mcp.tool()
def schematic_add_terminal(project: str, pin: str, net: str, distance_grid: int = 2) -> dict:
    """Attach a terminal to an unconnected pin, e.g. pin="U3.RESET", net="GND".

    net "GND" places a ground symbol, "VCC"/"POWER" a power symbol; any other name places a named terminal,
    and all pins with terminals (or wire labels / bus members) of the same name are connected, e.g. "AD0"
    joins the AD0 line of an AD[0..15] bus. Placed `distance_grid` steps out on the side the pin faces;
    refuses a position that would touch another pin or wire."""
    return schematic.add_terminal(_closed(project), pin, net, distance_grid)


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
    """Set a {KEY=VALUE} property of an UNANNOTATED part (e.g. RES#0010, BUTTON#0007).

    Annotated parts (U1, U2 ...) are refused: Proteus reads their properties from ROOT.CDB, which cannot be
    rewritten yet (Proteus hangs on a modified CDB). Change those in Proteus's Edit Component dialog."""
    return schematic.set_property(_closed(project), ref, key, value)


@mcp.tool()
def schematic_set_value(project: str, ref: str, value: str) -> dict:
    """Change the value of an UNANNOTATED part, e.g. a resistor RES#0010 to 330 (verified in Proteus).
    Annotated parts (U1 ...) are refused for the same ROOT.CDB reason as schematic_set_property."""
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
    _session = gui.Session.launch(_project(project))
    return {"project": project, "pid": _session.pid, "startup_warnings": sorted(set(_session.warnings)),
            "title": gui.win32gui.GetWindowText(_session.main)}


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
def debug_window(name: str) -> Image:
    """Open a Debug-menu popup while the simulation runs or is paused and return a screenshot of it.

    Examples: 'Registers - U1', 'Memory Dump - U1', '8255 Internal Status', 'Source Code - U1', 'Watch Window',
    'Simulation Log'. Pause first for a stable picture. The error lists the available items.
    """
    s = _require_session()
    h = s.debug_window(name)
    time.sleep(0.5)
    return Image(data=gui.png_bytes(gui.capture(h)), format="png")


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
    controls, e.g. an unrotated POT-HG: dx_grid=-1.27, dy_grid=1.05 raises the wiper (one step per click),
    dy_grid=-1.05 lowers it; `times` repeats the click."""
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
    img = s.screenshot().crop((box[0] - ml, box[1] - mt, box[2] - ml, box[3] - mt))
    if img.width < 300:
        img = img.resize((img.width * 2, img.height * 2))
    return Image(data=gui.png_bytes(img), format="png")


@mcp.tool()
def netlist(include_single_pin_nets: bool = False) -> dict:
    """Proteus's own netlist of the open project (Netlist Compiler, logical mode): parts and, for every net,
    the pins on it as REF.PIN. Unannotated parts appear as DEVICE#id. The simulation must be stopped."""
    s = _require_session()
    out = PROJECTS / f"{s.project.stem}_{int(time.time())}.sdf"
    path = s.netlist(out)
    n = parse_netlist(path.read_text(encoding="latin-1"))
    return n.summary(include_single_pin_nets) | {"file": str(path)}


@mcp.tool()
def net_of(ref: str, pin: str) -> dict:
    """Which net a pin is on, and every pin on that net. Pin by name as in the netlist (e.g. U3 PA0, U1 WR/LOCK)."""
    s = _require_session()
    out = PROJECTS / f"{s.project.stem}_{int(time.time())}.sdf"
    n = parse_netlist(s.netlist(out).read_text(encoding="latin-1"))
    name = n.net_of(ref, pin)
    return {"net": name, "members": [f"{r}.{p}" for r, p in n.nets.get(name, [])] if name else []}


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
