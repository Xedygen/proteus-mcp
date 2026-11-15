"""Schematic edits on a .pdsprj, written straight into ROOT.DSN.

Every edit re-parses its own output before saving.  Proteus re-derives the netlist and ROOT.CDB when it opens
the project; confirming an edit inside Proteus is done with the `netlist` tool.
Verified 2026-11-15: a wire written this way (lab5 template, latch Q1 to 8255 A0) opens without new warnings
and Proteus's netlist shows the connection.
"""
from __future__ import annotations

import re
import struct
from pathlib import Path

from . import dsn, pdsprj
from .devices import find_pin, pins

GRID = dsn.GRID


def load(project: str | Path) -> dsn.Sheet:
    return dsn.parse(pdsprj.entries(project)["ROOT.DSN"])


def save(project: str | Path, sheet: dsn.Sheet) -> None:
    out = sheet.build()
    dsn.parse(out)  # must still parse, with every pointer on a wire
    pdsprj.replace(project, {"ROOT.DSN": out})


def _props_dict(text: str) -> dict[str, str]:
    return dict(re.findall(r"\{([^=}]+)=([^}]*)\}", text))


def components(project: str | Path, with_pins: bool = True) -> list[dict]:
    sheet = load(project)
    out = []
    for c in sheet.components():
        props = c["props"].decode("latin-1")
        item = {"ref": sheet.name_of(c), "value": c["labels"][1]["text"], "device": c["device"],
                "origin": c["origin"], "grid": [c["origin"][0] / GRID, c["origin"][1] / GRID]}
        p = _props_dict(props)
        if p:
            item["properties"] = p
        if with_pins:
            try:
                item["pins"] = [{"name": q["name"], "number": q["number"], "connected": q["connected"],
                                 "grid": [q["xy"][0] / GRID, q["xy"][1] / GRID]} for q in pins(sheet, c)]
            except dsn.DsnError as e:
                item["pins_error"] = str(e)
        out.append(item)
    return out


def _route(a: list[int], b: list[int], via_x: int | None = None) -> list[list[int]]:
    """Orthogonal path a -> b: horizontal, vertical, horizontal through a grid-aligned column."""
    if a[0] == b[0] or a[1] == b[1]:
        return [a, b]
    x = via_x if via_x is not None else round((a[0] + b[0]) / 2 / GRID) * GRID
    pts = [a, [x, a[1]], [x, b[1]], b]
    return [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]


def _hot_spots(sheet: dsn.Sheet) -> set[tuple[int, int]]:
    """Points where something can connect: every pin end, wire end and junction."""
    spots = set()
    for c in sheet.components():
        try:
            spots |= {tuple(p["xy"]) for p in pins(sheet, c)}
        except dsn.DsnError:
            pass
    for w in sheet.wires():
        spots.add(tuple(w["points"][0]))
        spots.add(tuple(w["points"][-1]))
    spots |= {tuple(o["origin"]) for o in sheet.objects if o["type"] == "junction"}
    return spots


def _on_segment(p, a, b) -> bool:
    if a[0] == b[0] == p[0]:
        return min(a[1], b[1]) <= p[1] <= max(a[1], b[1])
    if a[1] == b[1] == p[1]:
        return min(a[0], b[0]) <= p[0] <= max(a[0], b[0])
    return False


def _check_clear(sheet: dsn.Sheet, points: list[list[int]], allowed: list[list[int]]) -> None:
    """Refuse a new wire that would touch any existing connection point other than its own pins: in Proteus
    that looks like (and after any later edit becomes) a real connection."""
    allow = {tuple(p) for p in allowed}
    for spot in _hot_spots(sheet) - allow:
        for a, b in zip(points, points[1:]):
            if _on_segment(spot, a, b):
                raise dsn.DsnError(f"route {[[q / GRID for q in p] for p in points]} touches an existing "
                                   f"connection point at {[spot[0] / GRID, spot[1] / GRID]}; "
                                   "choose another via_x_grid or connect differently")


def connect(project: str | Path, a: str, b: str, via_x_grid: float | None = None) -> dict:
    """Wire pin `a` to pin `b` ("REF.PIN", pin by name or number).  Both pins must be unconnected; to join a
    pin to an existing net, connect it to another unconnected pin or use a labelled terminal."""
    sheet = load(project)
    ra, pa_name = a.split(".", 1)
    rb, pb_name = b.split(".", 1)
    ca, pa = find_pin(sheet, ra, pa_name)
    cb, pb = find_pin(sheet, rb, pb_name)
    for comp, pin, label in ((ca, pa, a), (cb, pb, b)):
        if pin["connected"]:
            raise dsn.DsnError(f"{label} already has a wire; Proteus allows one wire per pin end")
        if pin["bus"]:
            raise dsn.DsnError(f"{label} is a bus pin; bus connections are not supported yet")
    template = next((w for w in sheet.wires() if w["flags"] == 0), None)
    if template is None:
        raise dsn.DsnError("no existing wire to copy the wire style from")
    key = sheet.new_key()
    via = None if via_x_grid is None else round(via_x_grid * GRID)
    points = _route(pa["xy"], pb["xy"], via)
    _check_clear(sheet, points, [pa["xy"], pb["xy"]])
    sheet.objects.append({"type": "wire", "start": key, "flags": 0, "style": template["style"],
                          "labels": [], "points": points})
    ca["pointers"][pa["slot"]] = key
    cb["pointers"][pb["slot"]] = key
    sheet.reschedule()
    save(project, sheet)
    return {"connected": [a, b], "points_grid": [[p[0] / GRID, p[1] / GRID] for p in points]}


# Terminal geometry measured on the samples (all terminals with a wire): connection point relative to the
# terminal origin, and label offset / label transform, per (device, transform).
_TERMINALS = {
    # device, facing: (transform, connection offset, label offset, label transform)
    ("$TERDEFAULT", "left"): ("00000000", (GRID, 0), (-381000, 0), "000001000800"),
    ("$TERDEFAULT", "right"): ("00000100", (-GRID, 0), (381000, 0), "000000000800"),
    # rotated forms (-90 / +90 degrees): connection offsets measured (-90) and derived (+90) by rotation
    ("$TERDEFAULT", "up"): ("7cfc0000", (0, -GRID), (0, 381000), "000001000800"),
    ("$TERDEFAULT", "down"): ("84030000", (0, GRID), (0, -381000), "000001000800"),
    ("$TERGROUND", None): ("00000000", (0, GRID), (-254000, -508000), "000000000800"),
    ("$TERPOWER", None): ("00000000", (0, -GRID), (-254000, 508000), "000000000800"),
}
_ATTRIBUTES = {"$TERDEFAULT": "090000000000", "$TERGROUND": "0d0000000000", "$TERPOWER": "0d0000000000"}


def add_terminal(project: str | Path, pin: str, net: str) -> dict:
    """Attach a terminal to an unconnected pin ("REF.PIN"): `net` = "GND", "VCC"/"POWER", or any net name
    (pins with terminals of the same name are connected, like wire labels).  The terminal is placed two grid
    steps out from the pin, on the side the pin faces."""
    sheet = load(project)
    ref, pin_name = pin.split(".", 1)
    comp, p = find_pin(sheet, ref, pin_name)
    if p["connected"]:
        raise dsn.DsnError(f"{pin} already has a wire")
    kind = {"GND": "$TERGROUND", "VCC": "$TERPOWER", "POWER": "$TERPOWER", "VDD": "$TERPOWER"}.get(
        net.upper(), "$TERDEFAULT")
    library = sheet.data[:sheet.circuit]
    if kind.encode() + b"\0" not in library and bytes([len(kind)]) + kind.encode() not in library:
        raise dsn.DsnError(f"{kind} is not in this file's embedded library; place one in Proteus first")
    out = p["out"]                                   # the side the pin faces, from the pin record
    t = [p["xy"][0] + 2 * GRID * out[0], p["xy"][1] + 2 * GRID * out[1]]
    if kind == "$TERDEFAULT":
        facing = {(-1, 0): "left", (1, 0): "right", (0, 1): "up", (0, -1): "down"}[tuple(out)]
    else:
        facing = None
    transform, conn, label_off, label_tf = _TERMINALS[(kind, facing)]
    origin = [t[0] - conn[0], t[1] - conn[1]]
    _check_clear(sheet, [p["xy"], t, origin], [p["xy"]])
    template = next((o for o in sheet.objects if o["type"] == "terminal"), None)
    if template is None:
        raise dsn.DsnError("no terminal in this file to copy the label style from")
    wire_tpl = next((w for w in sheet.wires() if w["flags"] == 0), None)
    key = sheet.new_key()
    label = {"text": "" if kind != "$TERDEFAULT" else net, "xy": [origin[0] + label_off[0], origin[1] + label_off[1]],
             "transform": bytes.fromhex(label_tf), "style": template["label"]["style"]}
    term = {"type": "terminal", "origin": origin, "transform": bytes.fromhex(transform), "device": kind,
            "attributes": bytes.fromhex(_ATTRIBUTES[kind]), "label": label, "pointers": [key]}
    sheet.objects.append(term)
    sheet.objects.append({"type": "wire", "start": key, "flags": 0, "style": wire_tpl["style"], "labels": [],
                          "points": [p["xy"], t]})
    comp["pointers"][p["slot"]] = key
    sheet.reschedule()
    save(project, sheet)
    return {"pin": pin, "terminal": kind, "net": net, "terminal_grid": [t[0] / GRID, t[1] / GRID]}


def set_property(project: str | Path, ref: str, key: str, value: str) -> dict:
    """Set {KEY=VALUE} in a component's property text (added if missing)."""
    sheet = load(project)
    comp = sheet.component(ref)
    text = comp["props"].decode("latin-1")
    pat = re.compile(r"\{" + re.escape(key) + r"=[^}]*\}")
    new = f"{{{key}={value}}}"
    text = pat.sub(new, text, count=1) if pat.search(text) else text.rstrip("\n") + "\n" + new + "\n"
    raw = text.encode("latin-1")
    shown = comp["labels"][3]["text"]
    if comp["props_inline"]:
        if len(raw) > 255:
            raise dsn.DsnError("property text too long for the inline form")
        comp["labels"][3]["text"] = text
        comp["props"], comp["props_n"] = raw, len(raw)
    else:
        if len(raw) == len(shown.encode("latin-1")):
            raw += b"\n"  # keep the block form distinguishable from the inline form
        comp["props"], comp["props_n"] = raw, len(raw)
    save(project, sheet)
    return {"ref": ref, "properties": _props_dict(text)}


def set_value(project: str | Path, ref: str, value: str) -> dict:
    """Change the value text shown under a component (e.g. a resistor's 10k)."""
    sheet = load(project)
    comp = sheet.component(ref)
    comp["labels"][1]["text"] = value
    save(project, sheet)
    return {"ref": ref, "value": value}
