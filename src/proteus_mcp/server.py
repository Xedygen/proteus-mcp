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

from . import doctor, firmware, gui, paths, pdsprj, schematic
from .netlist import parse as parse_netlist

PROJECTS = paths.WORK_DIR / "projects"

INSTRUCTIONS = """\
Tools for Proteus 8.6 (Labcenter) on this Windows machine, aimed at the YTU BLM3061 8086 labs.

Typical flow: project_open(<template or solution .pdsprj>) -> firmware_get_source / firmware_set_source ->
firmware_build (MASM 5.10, returns errors with line numbers and stores the EXE in the project) ->
proteus_launch -> sim_start -> sim_log / screenshot / debug_window -> sim_stop -> proteus_close.
netlist() returns Proteus's own connectivity and is the way to check wiring.

Read quirks() before writing 8086 code: the assembler is MASM 5.10 (no .IF/INVOKE/@@ labels, 128-char lines)
and the Proteus models have quirks (8251: SHR AL,1 after IN from the data port).
Every path argument may be absolute; projects are referred to afterwards by the name project_open returns.
"""

mcp = MCPServer("proteus", instructions=INSTRUCTIONS)
_session: gui.Session | None = None


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


# ----- projects ----------------------------------------------------------------------------------
@mcp.tool()
def project_open(path: str, name: str | None = None, overwrite: bool = False) -> dict:
    """Copy a .pdsprj into the workspace and return its name and contents.

    The original file is never modified. `name` defaults to the file's stem. If a workspace project with that
    name exists it is kept (pass overwrite=True to replace it with a fresh copy).
    """
    src = Path(path)
    if not src.is_file():
        raise ValueError(f"not a file: {src}")
    stem = re.sub(r"[^A-Za-z0-9_.-]", "_", name or src.stem)  # Proteus 8.6 can fail on non-ASCII paths
    dst = PROJECTS / f"{stem}.pdsprj"
    if not dst.exists() or overwrite:
        PROJECTS.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    return project_info(stem) | {"source": str(src.resolve())}


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
    """Copy a workspace project to `dest` (a .pdsprj path), e.g. OgrenciNo_IsimSoyisim.pdsprj for handing in."""
    p = _project(project)
    d = Path(dest)
    if d.suffix.lower() != ".pdsprj":
        raise ValueError("dest must end with .pdsprj")
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(p, d)
    return {"exported": str(d.resolve()), "bytes": d.stat().st_size}


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
        for d in devices:
            frame = s.sheet_frame()
            s.place_device(d["device"], frame, size,
                           (round(float(d["x_grid"]) * schematic.GRID), round(float(d["y_grid"]) * schematic.GRID)))
        s.save()
    finally:
        if s.alive():
            try:
                s.close()
            except gui.GuiError:
                s.kill()   # only ever this server's own Proteus process
    return [c for c in schematic.components(p) if c["ref"] not in before]


@mcp.tool()
def schematic_add_terminal(project: str, pin: str, net: str, distance_grid: int = 2) -> dict:
    """Attach a terminal to an unconnected pin, e.g. pin="U3.RESET", net="GND".

    net "GND" places a ground symbol, "VCC"/"POWER" a power symbol; any other name places a named terminal,
    and all pins with terminals (or wire labels / bus members) of the same name are connected, e.g. "AD0"
    joins the AD0 line of an AD[0..15] bus. Placed `distance_grid` steps out on the side the pin faces;
    refuses a position that would touch another pin or wire."""
    return schematic.add_terminal(_closed(project), pin, net, distance_grid)


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
def proteus_close() -> dict:
    """Stop any simulation and close the Proteus instance this server started, without saving."""
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
    res = s.close()
    _session = None
    return res


# ----- simulation ----------------------------------------------------------------------------------
@mcp.tool()
def sim_start(wait_seconds: float = 3.0) -> dict:
    """Run (or resume) the simulation, wait, and report state, the simulation log, and whether the program
    the 8086 loaded has the size of the EXE stored in the project."""
    s = _require_session()
    state = s.sim("start", settle=max(wait_seconds, 1.0))
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
    frame = s.sheet_frame()
    size = sheet.size()
    m = margin_grid * schematic.GRID
    corners = [s.sheet_to_screen(frame, size, (x, y))
               for x in (min(p[0] for p in pts) - m, max(p[0] for p in pts) + m)
               for y in (min(p[1] for p in pts) - m, max(p[1] for p in pts) + m)]
    centre = s.sheet_to_screen(frame, size, comp["origin"])
    return centre, (min(c[0] for c in corners), min(c[1] for c in corners),
                    max(c[0] for c in corners), max(c[1] for c in corners))


@mcp.tool()
def sim_click(ref: str, hold_seconds: float = 0.3, settle_seconds: float = 0.8) -> dict:
    """Press a part during a running simulation: a BUTTON is held for `hold_seconds` then released; a
    LOGICSTATE or switch toggles. Uses the real mouse on the Proteus window (zooms to the whole sheet first).
    Follow with screenshot_part to see the effect."""
    s = _require_session()
    if s.sim_state() != "running":
        raise ValueError("the simulation must be running (sim_start)")
    (x, y), _ = _part_screen_box(s, ref)
    s.real_click(x, y, hold=hold_seconds)
    time.sleep(settle_seconds)
    return {"clicked": ref, "screen": [x, y], "state": s.sim_state()}


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
