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
    sheet.objects.append({"type": "wire", "start": key, "flags": 0, "style": template["style"],
                          "labels": [], "points": points})
    ca["pointers"][pa["slot"]] = key
    cb["pointers"][pb["slot"]] = key
    sheet.reschedule()
    save(project, sheet)
    return {"connected": [a, b], "points_grid": [[p[0] / GRID, p[1] / GRID] for p in points]}


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
