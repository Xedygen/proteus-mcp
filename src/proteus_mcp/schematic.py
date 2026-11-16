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

from . import cdb, dsn, pdsprj, route
from .devices import GLYPH_LENGTH, find_pin, pins

GRID = dsn.GRID


def load(project: str | Path) -> dsn.Sheet:
    return dsn.parse(pdsprj.entries(project)["ROOT.DSN"])


def save(project: str | Path, sheet: dsn.Sheet, cdb_bytes: bytes | None = None) -> None:
    out = sheet.build()
    dsn.parse(out)  # must still parse, with every pointer on a wire
    changes = {"ROOT.DSN": out}
    if cdb_bytes is not None:
        changes["ROOT.CDB"] = cdb_bytes
    pdsprj.replace(project, changes)


def _in_cdb(project, comp: dict) -> bool:
    """Annotated parts also have a record in ROOT.CDB, and Proteus takes their properties from there.
    ROOT.CDB cannot be rewritten yet: any change, even of equal length, makes Proteus hang at
    "Initializing project..." (seen 2026-11-15), so such edits are refused instead of silently ignored."""
    data = pdsprj.entries(project).get("ROOT.CDB")
    ref = comp["labels"][0]["text"]
    return bool(data and ref and cdb.read_part(data, ref, comp["device"]))


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
                                 "grid": [q["xy"][0] / GRID, q["xy"][1] / GRID]}
                                for q in pins(sheet, c) if not q["hidden"]]
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


def _simplify(pts: list[list[int]]) -> list[list[int]]:
    """Drop repeated points and the middle point of three in a line that keeps going the same way.  A path
    that doubles back on itself is refused: it would run over the part it came from."""
    out: list[list[int]] = []
    for q in pts:
        if out and q == out[-1]:
            continue
        if len(out) >= 2:
            a, b = out[-2], out[-1]
            if a[0] == b[0] == q[0] or a[1] == b[1] == q[1]:
                if (b[0] - a[0]) * (q[0] - b[0]) < 0 or (b[1] - a[1]) * (q[1] - b[1]) < 0:
                    raise dsn.DsnError("route would double back on itself")
                out[-1] = q
                continue
        out.append(q)
    return out


def _route_pins(pa: dict, pb: dict, via_x: int | None = None, via_y: int | None = None) -> list[list[int]]:
    """Path between two pins that first leaves each pin one grid step in the direction it faces (so it never
    runs back through its own part), then joins the two stub ends: through column via_x (horizontal,
    vertical, horizontal), or, with via_y, along row via_y (a detour above or below parts)."""
    a, b = pa["xy"], pb["xy"]
    oa, ob = pa.get("out", (0, 0)), pb.get("out", (0, 0))
    facing_h = a[1] == b[1] and oa[0] and ob[0] and (b[0] - a[0]) * oa[0] > 0 and (a[0] - b[0]) * ob[0] > 0
    facing_v = a[0] == b[0] and oa[1] and ob[1] and (b[1] - a[1]) * oa[1] > 0 and (a[1] - b[1]) * ob[1] > 0
    if facing_h or facing_v:
        return [a, b]                    # facing each other on one line: straight wire
    sa = [a[0] + oa[0] * GRID, a[1] + oa[1] * GRID]
    sb = [b[0] + ob[0] * GRID, b[1] + ob[1] * GRID]
    if via_y is not None:
        core = [sa, [sa[0], via_y], [sb[0], via_y], sb]
    else:
        core = _route(sa, sb, via_x)
    return _simplify([a] + core + [b])


def _hot_spots(sheet: dsn.Sheet) -> set[tuple[int, int]]:
    """Points where something can connect: every pin end, wire end and junction."""
    spots = set()
    for c in sheet.components():
        try:
            spots |= {tuple(p["xy"]) for p in pins(sheet, c) if not p["hidden"]}
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


def _overlaps(a, b, c, d) -> bool:
    """Do segments ab and cd run along each other for more than a point?"""
    if a[0] == b[0] == c[0] == d[0]:
        lo, hi = max(min(a[1], b[1]), min(c[1], d[1])), min(max(a[1], b[1]), max(c[1], d[1]))
        return hi > lo
    if a[1] == b[1] == c[1] == d[1]:
        lo, hi = max(min(a[0], b[0]), min(c[0], d[0])), min(max(a[0], b[0]), max(c[0], d[0]))
        return hi > lo
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
    for w in sheet.wires():
        for c, d in zip(w["points"], w["points"][1:]):
            for a, b in zip(points, points[1:]):
                if _overlaps(a, b, c, d):
                    g = lambda p: [p[0] / GRID, p[1] / GRID]
                    raise dsn.DsnError(f"route {[g(p) for p in points]}: its segment {g(a)}-{g(b)} runs along "
                                       f"the existing wire segment {g(c)}-{g(d)}; choose another via_x_grid")


def connect(project: str | Path, a: str, b: str, via_x_grid: float | None = None) -> dict:
    """Wire pin `a` to pin `b` ("REF.PIN", pin by name or number).  Both pins must be unconnected; to join a
    pin to an existing net, connect it to another unconnected pin or use a labelled terminal."""
    sheet = load(project)
    ra, pa_name = a.split(".", 1)
    rb, pb_name = b.split(".", 1)
    ca, pa = find_pin(sheet, ra, pa_name)
    cb, pb = find_pin(sheet, rb, pb_name)
    if pa is pb:
        raise dsn.DsnError(f"{a} and {b} are the same pin")
    if pa["connected"] != pb["connected"] and not (pa["bus"] or pb["bus"]):
        # one end already wired: join the free pin onto that net with a junction
        free, wired = (a, b) if pb["connected"] else (b, a)
        out = tap(project, free, wired)
        out["note"] = f"{wired} already had a wire, so {free} was tapped onto its net with a junction"
        return out
    for comp, pin, label in ((ca, pa, a), (cb, pb, b)):
        if pin["connected"]:
            raise dsn.DsnError(f"{label} already has a wire; Proteus allows one wire per pin end")
        if pin["bus"]:
            raise dsn.DsnError(f"{label} is a bus pin; bus connections are not supported yet")
        if pin["hidden"]:
            raise dsn.DsnError(f"{label} is a hidden power pin; Proteus connects it to its power net itself")
    key = sheet.new_key()
    if via_x_grid is not None:
        points = _route_pins(pa, pb, round(via_x_grid * GRID))
        _check_clear(sheet, points, [pa["xy"], pb["xy"]])
    else:
        points = None
        try:
            points, _ = route.route(sheet, pa, route.pin_goal(pb), {tuple(pa["xy"]), tuple(pb["xy"])})
            points = _simplify(points)
            _check_clear(sheet, points, [pa["xy"], pb["xy"]])
        except dsn.DsnError:
            points = None
    if points is None:
        mid = round((pa["xy"][0] + pb["xy"][0]) / 2 / GRID)
        lo, hi = sorted((pa["xy"][0] // GRID, pb["xy"][0] // GRID))
        # columns between the pins first, then further out (a route may go round a part)
        candidates = sorted(range(lo - 10, hi + 11), key=lambda x: (not lo < x < hi, abs(x - mid)))
        points, last = None, None
        for x in candidates:
            try:
                trial = _route_pins(pa, pb, x * GRID)
                _check_clear(sheet, trial, [pa["xy"], pb["xy"]])
                points = trial
                break
            except dsn.DsnError as e:
                last = e
        if points is None:
            # no column works (e.g. the target lies behind the part): detour along a row above or below
            base = max(pa["xy"][1], pb["xy"][1]), min(pa["xy"][1], pb["xy"][1])
            for k in range(2, 12):
                for y in (base[0] + k * GRID, base[1] - k * GRID):
                    try:
                        trial = _route_pins(pa, pb, via_y=y)
                        _check_clear(sheet, trial, [pa["xy"], pb["xy"]])
                        points = trial
                        break
                    except dsn.DsnError as e:
                        last = e
                if points is not None:
                    break
        if points is None:
            raise dsn.DsnError(f"no free route between the pins ({last}); pass via_x_grid or use terminals")
    # ISIS binds points[0] to the end of whichever object is serialised first (4651/4651 sample wires)
    order = {id(o): i for i, o in enumerate(sheet.objects)}
    first_a = (order[id(ca)], pa["slot"]) <= (order[id(cb)], pb["slot"])
    if not first_a:
        points = points[::-1]
    sheet.objects.append({"type": "wire", "start": key, "flags": 0, "style": _wire_style(sheet, 0),
                          "labels": [], "points": points})
    ca["pointers"][pa["slot"]] = key
    cb["pointers"][pb["slot"]] = key
    sheet.reschedule()
    save(project, sheet)
    return {"connected": [a, b], "resolved": [f"{x['name']} (pin {x['number'] or '?'})" for x in (pa, pb)],
            "points_grid": [[p[0] / GRID, p[1] / GRID] for p in points]}


# Terminal geometry measured on the samples (all terminals with a wire): connection point relative to the
# terminal origin, and label offset / label transform, per (device, transform).
_TERMINALS = {
    # device, facing: (transform, connection offset, label offset, label transform)
    ("$TERDEFAULT", "left"): ("00000000", (GRID, 0), (-381000, 0), "000001000800"),
    ("$TERDEFAULT", "right"): ("00000100", (-GRID, 0), (381000, 0), "000000000800"),
    # rotated forms (-90 / +90 degrees): connection offsets measured (-90) and derived (+90) by rotation
    ("$TERDEFAULT", "up"): ("7cfc0000", (0, -GRID), (0, 381000), "000001000800"),
    ("$TERDEFAULT", "down"): ("84030000", (0, GRID), (0, -381000), "000001000800"),
    ("$TERBUS", "left"): ("00000000", (GRID, 0), (-254000, 0), "000001000800"),
    ("$TERBUS", "right"): ("00000100", (-GRID, 0), (254000, 0), "000000000800"),
    ("$TERGROUND", None): ("00000000", (0, GRID), (-254000, -508000), "000000000800"),
    ("$TERPOWER", None): ("00000000", (0, -GRID), (-254000, 508000), "000000000800"),
}
# Default styles: identical in every sample project (3401 wires, 1751 bus wires, 827 terminal labels, 1687 wire
# labels across 28 files), used when the file has nothing of the kind to copy from.
_WIRE_STYLE = {0: b"\x00\xc0\x9e\x00\x00\x00@\x00\x00\x01\xff\xff\xff\x00\xff\xff\xff\x00\x02\x7fWIRE\x00",
               1: b"\x00PS\x02\x00\x00\x00\xc0\x00\x01\xff\xff\xff\x00\xff\xff\xff\x00\x02\x7fBUS WIRE\x00"}
_TERMINAL_LABEL_STYLE = b"0\xe0\x03\x00\xc0\x19\x03\x00\x00\x00\x00\x00\x01 \xff\x01Default Font\x00TERMINAL LABEL\x00\x00\x00\x00\x00"
_WIRE_LABEL_STYLE = b"0\xe0\x03\x00\xc0\x19\x03\x00\x00\x00\x00\x00\x01 \xff\x01Default Font\x00WIRE LABEL\x00\x00\x00\x00\x00"
_ATTRIBUTES = {"$TERDEFAULT": "090000000000","$TERBUS": "090000000000", "$TERGROUND": "0d0000000000", "$TERPOWER": "0d0000000000"}


def add_terminal(project: str | Path, pin: str, net: str, distance_grid: int = 2) -> dict:
    """Attach a terminal to an unconnected pin ("REF.PIN"): `net` = "GND", "VCC"/"POWER", or any net name
    (pins with terminals of the same name are connected, like wire labels).  The terminal is placed two grid
    steps out from the pin, on the side the pin faces."""
    sheet = load(project)
    ref, pin_name = pin.split(".", 1)
    comp, p = find_pin(sheet, ref, pin_name)
    if p["connected"]:
        raise dsn.DsnError(f"{pin} already has a wire")
    if p["hidden"]:
        raise dsn.DsnError(f"{pin} is a hidden power pin")
    kind = {"GND": "$TERGROUND", "VCC": "$TERPOWER", "POWER": "$TERPOWER", "VDD": "$TERPOWER"}.get(
        net.upper(), "$TERDEFAULT")
    # other supply names (+12V, -5V, VEE, V+): a labelled power terminal, which Proteus drives at that rail's
    # voltage; a plain terminal of that name would only join nets and drive nothing
    rail = kind == "$TERDEFAULT" and bool(re.fullmatch(r"[+-]?\d+(\.\d+)?V|V(CC|DD|EE|SS)\w*|V[+-]", net, re.I))
    if rail:
        kind = "$TERPOWER"
    if p["bus"]:
        # a bus pin (e.g. D[0..7]) takes a bus terminal named like AD[0..7] and a bus wire
        if not re.fullmatch(r".+\[\d+\.\.\d+\]", net):
            raise dsn.DsnError(f"{pin} is a bus pin; give a bus name such as AD[0..7]")
        kind = "$TERBUS"
    library = sheet.data[:sheet.circuit]
    if kind.encode() + b"\0" not in library and bytes([len(kind)]) + kind.encode() not in library:
        raise dsn.DsnError(f"{kind} is not in this file's embedded library; place one in Proteus first")
    out = p["out"]                                   # the side the pin faces, from the pin record
    if kind in ("$TERDEFAULT", "$TERBUS"):
        facing = {(-1, 0): "left", (1, 0): "right", (0, 1): "up", (0, -1): "down"}[tuple(out)]
        if kind == "$TERBUS" and facing not in ("left", "right"):
            raise dsn.DsnError("bus terminals on top/bottom pins are not supported")
    else:
        facing = None
    transform, conn, label_off, label_tf = _TERMINALS[(kind, facing)]
    # a power/ground symbol's body sticks out sideways, so next to a neighbour's terminal step further out
    for distance in (distance_grid, distance_grid + 2, distance_grid + 4):
        t = [p["xy"][0] + distance * GRID * out[0], p["xy"][1] + distance * GRID * out[1]]
        origin = [t[0] - conn[0], t[1] - conn[1]]
        try:
            _check_clear(sheet, [p["xy"], t, origin], [p["xy"]])
            break
        except dsn.DsnError:
            if distance == distance_grid + 4:
                raise
    template = next((o for o in sheet.objects if o["type"] == "terminal"), None)
    label_style = template["label"]["style"] if template else _TERMINAL_LABEL_STYLE
    want_flags = 1 if kind == "$TERBUS" else 0
    key = sheet.new_key()
    label = {"text": net if kind in ("$TERDEFAULT", "$TERBUS") or rail else "", "xy": [origin[0] + label_off[0], origin[1] + label_off[1]],
             "transform": bytes.fromhex(label_tf), "style": label_style}
    term = {"type": "terminal", "origin": origin, "transform": bytes.fromhex(transform), "device": kind,
            "attributes": bytes.fromhex(_ATTRIBUTES[kind]), "label": label, "pointers": [key]}
    sheet.objects.append(term)
    sheet.objects.append({"type": "wire", "start": key, "flags": want_flags, "style": _wire_style(sheet, want_flags),
                          "labels": [], "points": [p["xy"], t]})
    comp["pointers"][p["slot"]] = key
    sheet.reschedule()
    save(project, sheet)
    # echo what the pin resolved to: names win over numbers, so on a 74154 "5" is output Y5 (pin 6)
    return {"pin": pin, "resolved": f"{p['name']} (pin {p['number'] or '?'})", "terminal": kind, "net": net,
            "terminal_grid": [t[0] / GRID, t[1] / GRID]}


# ----- nets made of wires and junctions -----------------------------------------------------------------
def _holders(sheet: dsn.Sheet) -> dict[int, list[tuple[dict, int]]]:
    """wire key -> [(object, pointer slot)] in file order (the first holder owns points[0])."""
    out: dict[int, list[tuple[dict, int]]] = {}
    for o in sheet.objects:
        for slot, p in enumerate(o.get("pointers", [])):
            if p:
                out.setdefault(p, []).append((o, slot))
    return out


def _cluster(sheet: dsn.Sheet, key: int) -> list[dict]:
    """Every wire joined to wire `key` through junctions (one drawn net fragment)."""
    wires = {w["start"]: w for w in sheet.wires()}
    holders = _holders(sheet)
    seen, todo = set(), [key]
    while todo:
        k = todo.pop()
        if k in seen or k not in wires:
            continue
        seen.add(k)
        for o, _ in holders.get(k, []):
            if o["type"] == "junction":
                todo += [p for p in o["pointers"] if p]
    return [wires[k] for k in seen]


def _target_wires(sheet: dsn.Sheet, target: str) -> list[dict]:
    """Wires of the net named by `target`: "REF.PIN" of a wired pin, or a terminal / wire label name."""
    keys = []
    if "." in target:
        ref, pin_name = target.split(".", 1)
        try:
            comp, p = find_pin(sheet, ref, pin_name)
        except dsn.DsnError:
            comp = None
        if comp is not None:
            key = comp["pointers"][p["slot"]]
            if not key:
                raise dsn.DsnError(f"{target} has no wire; connect two free pins with schematic_connect")
            keys.append(key)
    if not keys:
        for o in sheet.objects:
            if o["type"] == "terminal" and o["label"]["text"] == target and o["pointers"][0]:
                keys.append(o["pointers"][0])
            elif o["type"] == "wire" and any(t["text"] == target for t in o["labels"]):
                keys.append(o["start"])
    if not keys:
        raise dsn.DsnError(f"{target!r} is neither a wired pin (REF.PIN) nor a terminal / wire label name")
    out, seen = [], set()
    for k in keys:
        for w in _cluster(sheet, k):
            if w["start"] not in seen:
                seen.add(w["start"])
                out.append(w)
    return out


def _split_wire(sheet: dsn.Sheet, w: dict, at: list[int]) -> tuple[int, int]:
    """Split wire `w` at point `at` (on one of its segments) into w (first holder .. at) and a new wire
    (at .. second holder).  Returns the two keys; the caller ties both to a junction at `at`."""
    holders = _holders(sheet).get(w["start"], [])
    if len(holders) != 2:
        raise dsn.DsnError("that wire is attached at one end only; it cannot be tapped")
    pts = w["points"]
    for i, (a, b) in enumerate(zip(pts, pts[1:])):
        if _on_segment(tuple(at), a, b):
            break
    else:
        raise dsn.DsnError("tap point is not on the wire")
    first = pts[:i + 1] + ([at] if at != pts[i] else [])
    second = ([at] if at != pts[i + 1] else []) + pts[i + 1:]
    if len(first) < 2 or len(second) < 2:
        raise dsn.DsnError("tap point is a wire end; use the object there instead")
    new_key = sheet.new_key()
    w["points"] = first
    # the junction is appended after both holders, so each half's points[0] is at its other holder
    sheet.objects.append({"type": "wire", "start": new_key, "flags": w["flags"], "style": w["style"],
                          "labels": [], "points": second[::-1]})
    obj, slot = holders[1]
    obj["pointers"][slot] = new_key
    return w["start"], new_key


def _segments_points(w: dict):
    """Grid points along a wire's segments."""
    for a, b in zip(w["points"], w["points"][1:]):
        if a[0] == b[0]:
            step = GRID if b[1] > a[1] else -GRID
            for y in range(a[1], b[1] + step, step):
                yield [a[0], y]
        elif a[1] == b[1]:
            step = GRID if b[0] > a[0] else -GRID
            for x in range(a[0], b[0] + step, step):
                yield [x, a[1]]


def tap(project: str | Path, pin: str, target: str, at_grid: list[float] | None = None) -> dict:
    """Wire an unconnected pin onto an existing net: `target` is a wired pin ("U3.A0") or a terminal / wire
    label name.  The nearest free point on that net's wires is found (or `at_grid` [x, y] is used), the wire
    there is split and a junction dot joins the three wires, as Proteus draws a T connection."""
    sheet = load(project)
    ref, pin_name = pin.split(".", 1)
    comp, p = find_pin(sheet, ref, pin_name)
    if p["connected"]:
        raise dsn.DsnError(f"{pin} already has a wire; Proteus allows one wire per pin end")
    if p["hidden"] or p["bus"]:
        raise dsn.DsnError(f"{pin} is a hidden or bus pin")
    net = _target_wires(sheet, target)
    net = [w for w in net if w["flags"] == 0]
    if not net:
        raise dsn.DsnError(f"{target} has no plain (non-bus) wire to tap")
    spots = _hot_spots(sheet)
    junctions = {tuple(o["origin"]): o for o in sheet.objects if o["type"] == "junction"}
    net_keys = {w["start"] for w in net}
    open_j = {k for k, j in junctions.items() if 0 in j["pointers"] and set(j["pointers"]) & net_keys}
    goals = route.wire_goals(net, spots - open_j)
    for k in open_j:                     # a junction with a free slot can take the wire directly
        if k[0] % GRID == 0 and k[1] % GRID == 0:
            w = next(w for w in net if w["start"] in junctions[k]["pointers"])
            goals[(k[0] // GRID, k[1] // GRID)] = ([], (list(k), w))
    if at_grid is not None:
        t = [round(at_grid[0] * GRID), round(at_grid[1] * GRID)]
        goals = {n: g for n, g in goals.items() if g[1][0] == t}
        if not goals:
            raise dsn.DsnError(f"{at_grid} is not a free point on a wire of {target}")
    if not goals:
        raise dsn.DsnError(f"no free point on {target}'s wires to tap")
    try:
        pts, (t, w) = route.route(sheet, p, goals, {tuple(p["xy"])} | open_j | {tuple(g[1][0]) for g in goals.values()})
        pts = _simplify(pts)
        _check_clear(sheet, pts, [p["xy"], t])
    except dsn.DsnError as e:
        raise dsn.DsnError(f"no free route from {pin} to {target} ({e}); pass at_grid or use a terminal")
    new_key = sheet.new_key()
    j = junctions.get(tuple(t))
    if j is not None and 0 in j["pointers"]:
        j["pointers"][j["pointers"].index(0)] = new_key
        # the pin's part may come after the junction in the file: points[0] goes to the first holder
        if sheet.objects.index(comp) > sheet.objects.index(j):
            pts = pts[::-1]
        sheet.objects.append({"type": "wire", "start": new_key, "flags": 0, "style": w["style"],
                              "labels": [], "points": pts})
    else:
        k1, k2 = _split_wire(sheet, w, t)
        new_key = sheet.new_key()
        sheet.objects.append({"type": "wire", "start": new_key, "flags": 0, "style": w["style"],
                              "labels": [], "points": pts})
        sheet.objects.append({"type": "junction", "origin": t, "pointers": [k1, k2, new_key, 0]})
    comp["pointers"][p["slot"]] = new_key
    sheet.reschedule()
    save(project, sheet)
    return {"pin": pin, "resolved": f"{p['name']} (pin {p['number'] or '?'})", "joined": target,
            "junction_grid": [t[0] / GRID, t[1] / GRID], "points_grid": [[q[0] / GRID, q[1] / GRID] for q in pts]}


def _merge_at(sheet: dsn.Sheet, j: dict) -> None:
    """A junction left with exactly two wires: join them into one wire and drop the junction."""
    keys = [k for k in j["pointers"] if k]
    wires = {w["start"]: w for w in sheet.wires()}
    a, b = wires[keys[0]], wires[keys[1]]
    holders = _holders(sheet)
    ha = next(h for h in holders[a["start"]] if h[0] is not j)
    hb = next(h for h in holders[b["start"]] if h[0] is not j)
    pa = a["points"] if a["points"][-1] == j["origin"] else a["points"][::-1]   # holder a .. junction
    pb = b["points"] if b["points"][0] == j["origin"] else b["points"][::-1]    # junction .. holder b
    pts = pa + pb[1:]
    order = {id(o): i for i, o in enumerate(sheet.objects)}
    if (order[id(ha[0])], ha[1]) > (order[id(hb[0])], hb[1]):
        pts = pts[::-1]
    try:
        pts = _simplify(pts)
    except dsn.DsnError:
        pass
    a["points"], a["labels"] = pts, a["labels"] + b["labels"]
    hb[0]["pointers"][hb[1]] = a["start"]
    sheet.objects = [o for o in sheet.objects if o is not b and o is not j]


def _tidy_junctions(sheet: dsn.Sheet, touched: list[dict]) -> int:
    """After wires were taken off junctions: merge those left with two wires, drop those left with none, and
    keep used slots first (as Proteus writes them).  Returns the number merged."""
    merged = 0
    for j in touched:
        if not any(o is j for o in sheet.objects):
            continue
        keys = [k for k in j["pointers"] if k]
        if len(keys) == 2:
            _merge_at(sheet, j)
            merged += 1
        elif not keys:
            sheet.objects = [o for o in sheet.objects if o is not j]
        else:
            j["pointers"] = keys + [0] * (len(j["pointers"]) - len(keys))
    return merged


def delete_wire(project: str | Path, pin: str | None = None, at_grid: list[float] | None = None) -> dict:
    """Delete one wire: the one on pin "REF.PIN", or the one passing through grid point `at_grid`.  A terminal
    left with no wire goes too; a junction left with two wires is merged into one wire."""
    sheet = load(project)
    if pin:
        ref, pin_name = pin.split(".", 1)
        comp, p = find_pin(sheet, ref, pin_name)
        key = comp["pointers"][p["slot"]]
        if not key:
            raise dsn.DsnError(f"{pin} has no wire")
    elif at_grid is not None:
        t = (round(at_grid[0] * GRID), round(at_grid[1] * GRID))
        hits = [w for w in sheet.wires() if any(_on_segment(t, a, b) for a, b in zip(w["points"], w["points"][1:]))]
        if not hits:
            raise dsn.DsnError(f"no wire passes through {at_grid}")
        if len(hits) > 1:
            raise dsn.DsnError(f"{len(hits)} wires meet at {at_grid}; pick a point inside one wire")
        key = hits[0]["start"]
    else:
        raise dsn.DsnError("give pin or at_grid")
    wire = next(w for w in sheet.wires() if w["start"] == key)
    removed_terms, merged = [], 0
    doomed = [wire]
    touched = []
    for o, slot in _holders(sheet).get(key, []):
        o["pointers"][slot] = 0
        if o["type"] == "terminal":
            doomed.append(o)
            removed_terms.append(o["label"]["text"] or o["device"])
        elif o["type"] == "junction":
            touched.append(o)
    ids = {id(o) for o in doomed}
    sheet.objects = [o for o in sheet.objects if id(o) not in ids]
    merged = _tidy_junctions(sheet, touched)
    sheet.reschedule()
    save(project, sheet)
    return {"deleted_wire_points_grid": [[q[0] / GRID, q[1] / GRID] for q in wire["points"]],
            "terminals_removed": removed_terms, "junctions_merged": merged}


def _label_spot(sheet: dsn.Sheet, segs: list, name: str):
    """(wire, a, b, point) for a new wire label: a grid point on an unlabelled segment where the text (running
    right of a horizontal wire, up a vertical one, about 0.7 grid per character) covers no part body or pin,
    preferring long segments and their middles."""
    ob = route.Obstacles(sheet, pad=0)
    n = max(1, -(-len(name) * 7 // 10))
    best = None
    for w, a, b in segs:
        if w["labels"]:
            continue
        horiz = a[1] == b[1]
        length = abs(a[0] - b[0]) + abs(a[1] - b[1])
        ends = {tuple(w["points"][0]), tuple(w["points"][-1])}
        lo, hi = (sorted((a[0], b[0])) if horiz else sorted((a[1], b[1])))
        for v in range(-(-lo // GRID) * GRID, hi + 1, GRID):
            pt = [v, a[1]] if horiz else [a[0], v]
            if tuple(pt) in ends and length > GRID:
                continue
            gx, gy = pt[0] // GRID, pt[1] // GRID
            if pt[0] % GRID or pt[1] % GRID:
                continue
            box = ({(gx + i, gy + j) for i in range(n + 1) for j in (0, 1)} if horiz
                   else {(gx + j, gy + i) for i in range(n + 1) for j in (-1, 0)})
            hit = len(box & ob.nodes)
            key = (hit, -length, abs(2 * v - lo - hi))
            if best is None or key < best[0]:
                best = (key, w, a, b, pt)
    if best is None:
        w, a, b = max(segs, key=lambda s: abs(s[1][0] - s[2][0]) + abs(s[1][1] - s[2][1]))
        if w["labels"]:
            raise dsn.DsnError(f"that wire already carries the label {w['labels'][0]['text']!r}; use "
                               "schematic_rename_label to change it")
        return w, a, b, [(a[0] + b[0]) // 2, (a[1] + b[1]) // 2]
    return best[1:]


def add_wire_label(project: str | Path, target: str, name: str, at_grid: list[float] | None = None) -> dict:
    """Name a net by putting a wire label on one of its wires: `target` is a wired pin ("U3.A0", its own wire
    is labelled) or an existing terminal / label name.  Wires labelled with the same name are one net in
    Proteus, as with terminals.  The label goes at the middle of the longest straight segment, or at the
    segment through `at_grid`.  A bus wire takes a bus name such as AD[0..7]."""
    sheet = load(project)
    if not name or len(name.encode("latin-1", "replace")) > 255:
        raise dsn.DsnError("give a label name of 1..255 characters")
    wires = None
    if "." in target and at_grid is None:
        ref, pin_name = target.split(".", 1)
        try:
            comp, p = find_pin(sheet, ref, pin_name)
        except dsn.DsnError:
            comp = None
        if comp is not None:
            key = comp["pointers"][p["slot"]]
            if not key:
                raise dsn.DsnError(f"{target} has no wire; use schematic_add_terminal to name a free pin")
            wires = [w for w in sheet.wires() if w["start"] == key]
    if wires is None:
        wires = _target_wires(sheet, target)
    segs = [(w, a, b) for w in wires for a, b in zip(w["points"], w["points"][1:]) if a != b]
    if at_grid is not None:
        t = (round(at_grid[0] * GRID), round(at_grid[1] * GRID))
        segs = [(w, a, b) for w, a, b in segs if _on_segment(t, a, b)]
        if not segs:
            raise dsn.DsnError(f"no wire of {target} passes through {at_grid}")
        w, a, b = segs[0]
        mid = list(t)
    else:
        w, a, b, mid = _label_spot(sheet, segs, name)
    if w["labels"]:
        raise dsn.DsnError(f"that wire already carries the label {w['labels'][0]['text']!r}; use "
                           "schematic_rename_label to change it")
    if w["flags"] == 1 and not re.fullmatch(r".+\[\d+\.\.\d+\]", name):
        raise dsn.DsnError("that is a bus wire; give a bus name such as AD[0..7]")
    if w["flags"] == 0 and re.fullmatch(r".+\[\d+\.\.\d+\]", name):
        raise dsn.DsnError("a bus name goes on a bus wire; name a single signal such as AD3 here")
    # as Proteus places them (1650 of 1687 sample labels): just above a horizontal wire, left of a vertical one
    if a[1] == b[1]:
        xy, tf = [mid[0], mid[1] + 20320], "000000000000"
    else:
        xy, tf = [mid[0] - 20320, mid[1]], "840300000000"
    w["labels"].append({"text": name, "xy": xy, "transform": bytes.fromhex(tf), "style": _WIRE_LABEL_STYLE})
    save(project, sheet)
    same = sum(1 for x in sheet.wires() for lab in x["labels"] if lab["text"] == name)
    same += sum(1 for o in sheet.objects if o["type"] == "terminal" and o["label"]["text"] == name)
    return {"label": name, "on": target, "at_grid": [mid[0] / GRID, mid[1] / GRID],
            "places_with_this_name": same}


def _num(x: float) -> str:
    """Plain decimal for a property value (640000, 9600, 1.5): Proteus files never use exponent form."""
    return str(int(x)) if float(x).is_integer() else repr(float(x))


def _next_identity(sheet: dsn.Sheet) -> int:
    """Components and instruments share one identity space (an instrument's u16 field); Proteus derives
    references from it, so a clash shows up as "Duplicate part reference" at simulation start."""
    used = [o["identity"] for o in sheet.components()]
    used += [o["u16"] for o in sheet.objects if o["type"] == "instrument"]
    return max(used + [0]) + 1


def _wire_style(sheet: dsn.Sheet, flags: int) -> bytes:
    """Style record of an existing wire of that kind (0 = wire, 1 = bus wire), else the built-in default."""
    w = next((w for w in sheet.wires() if w["flags"] == flags), None)
    return w["style"] if w else _WIRE_STYLE[flags]


_GEN_STYLE = (b"0\xe0\x03\x00\xc0\x19\x03\x00\x00\x00\xc0\x00\x01 \xff\x01Default Font\x00GENERATOR LABEL\x00\x00\x00\x00\x00",
              b"0\xe0\x03\x00\xc0\x19\x03\x00\x00\x00\x00\x00\x01 \xff\x01Default Font\x00PROPERTIES\x00\x00\x00\x00\x00")


def add_generator(project: str | Path, pin: str, frequency_hz: float, name: str | None = None,
                  distance_grid: int = 3) -> dict:
    """Attach a digital clock generator ($DCLOCK0, square wave starting low) to an unconnected left- or
    right-side pin, e.g. the 8251's TxC/RxC (9600 Hz for 9600 baud at x1) or the ADC0804's CLK IN.
    The generator's name labels its net, so each generator needs a distinct name (default "REF(PIN)").
    Its properties live in ROOT.DSN, so this also works without ROOT.CDB changes."""
    sheet = load(project)
    ref, pin_name = pin.split(".", 1)
    comp, p = find_pin(sheet, ref, pin_name)
    if p["connected"]:
        raise dsn.DsnError(f"{pin} already has a wire")
    if p["hidden"] or p["bus"]:
        raise dsn.DsnError(f"{pin} is a hidden or bus pin")
    out = tuple(p["out"])
    if out not in ((-1, 0), (1, 0)):
        raise dsn.DsnError("generators on top/bottom pins are not supported; use a terminal and a "
                           "generator on a left/right pin elsewhere")
    if not frequency_hz > 0:
        raise dsn.DsnError("frequency_hz must be positive")
    if distance_grid < 1:
        raise dsn.DsnError("distance_grid must be at least 1")
    name = name or f"{sheet.name_of(comp)}({p['name']})"
    used = {o["labels"][0]["text"] for o in sheet.objects if o["type"] == "instrument"}
    used |= {o["label"]["text"] for o in sheet.objects if o["type"] == "terminal"}
    used |= {lab["text"] for w in sheet.wires() for lab in w["labels"]}
    if name in used:
        raise dsn.DsnError(f"the name {name} is already a generator, terminal or wire label; a generator's "
                           "name labels its net, so pick another")
    mirror = 0 if out == (-1, 0) else 1                # mirror 0: body on the left, output to the right
    origin = [p["xy"][0] + distance_grid * GRID * out[0], p["xy"][1]]
    _check_clear(sheet, [p["xy"], origin], [p["xy"]])
    side = 1 if mirror else -1
    label_tf = "000000000" if mirror else "000001000"
    props = (f"{{INIT=LOW}}\n{{START=0}}\n{{COUNT=-1}}\n{{CLOCK={_num(frequency_hz)}}}\n"
             f"{{MANUALEDITS=FALSE}}\n{{HIDDENPROPS=TRUE}}\n").encode()
    ident = _next_identity(sheet)
    ids = {o["id"] for o in sheet.objects if o["type"] == "instrument"}
    n = ident
    while f"M{n:08X}" in ids:
        n += 1
    key = sheet.new_key()
    gen = {"type": "instrument", "kind": 187, "origin": origin, "transform": struct.pack("<hH", 0, mirror),
           "u16": ident, "id": f"M{n:08X}", "device": "$DCLOCK0",
           "labels": [{"text": name, "xy": [origin[0] + side * 482600, origin[1] + 381000],
                       "transform": bytes.fromhex(label_tf + "800"), "style": _GEN_STYLE[0]},
                      {"text": "", "xy": [origin[0] + side * 482600, origin[1] + 254000],
                       "transform": bytes.fromhex(label_tf + "400"), "style": _GEN_STYLE[1]}],
           "props_n": len(props), "props": props, "props_inline": False, "flags": 0, "pointers": [key],
           "tail": b"\0\0\0\0", "tail_c": 0, "blob_n": 5, "blob": b"\0"}
    sheet.objects.append(gen)
    sheet.objects.append({"type": "wire", "start": key, "flags": 0, "style": _wire_style(sheet, 0),
                          "labels": [], "points": [p["xy"], origin]})
    comp["pointers"][p["slot"]] = key
    sheet.reschedule()
    save(project, sheet)
    return {"pin": pin, "generator": name, "frequency_hz": frequency_hz,
            "generator_grid": [origin[0] / GRID, origin[1] / GRID]}


_ROTATIONS = {0: 0, 90: 900, 180: 1800, 270: 2700}


def _placement_check(sheet: dsn.Sheet, comp: dict, ignore: tuple = ()) -> None:
    """Refuse a placement whose visible pins (ends or body anchors) land on an existing connection point,
    on a wire, or inside another part's body.  `comp` must not be in sheet.objects; nor should objects in
    `ignore` (e.g. the part's own wires while it is being moved) matter."""
    skip = {id(o) for o in ignore}
    saved = sheet.objects
    sheet.objects = [o for o in saved if id(o) not in skip and o is not comp]
    try:
        spots = _hot_spots(sheet)
        ob = route.Obstacles(sheet, pad=0)
        wires = sheet.wires()
        sheet.objects = sheet.objects + [comp]
        for p in pins(sheet, comp):
            if p["hidden"]:
                continue
            xy = tuple(p["xy"])
            where = [xy[0] / GRID, xy[1] / GRID]
            if xy in spots:
                raise dsn.DsnError(f"pin {p['name']} would land on an existing connection point at {where}")
            if any(_on_segment(xy, a, b) for w in wires for a, b in zip(w["points"], w["points"][1:])):
                raise dsn.DsnError(f"pin {p['name']} would land on an existing wire at {where}; pick another spot")
            L = GLYPH_LENGTH.get(p["glyph"], 2 * GRID)
            for q in (xy, (xy[0] - p["out"][0] * L, xy[1] - p["out"][1] * L)):
                if q[0] % GRID == 0 and q[1] % GRID == 0 and (q[0] // GRID, q[1] // GRID) in ob.nodes:
                    raise dsn.DsnError(f"pin {p['name']} at {where} would overlap another part; pick another spot")
    finally:
        sheet.objects = saved


def add_part(project: str | Path, like: str, x_grid: float, y_grid: float, rotation: int | None = None,
             value: str | None = None, ref: str | None = None) -> dict:
    """Place a copy of an existing part (e.g. like="RES#0010" or like="U5") at a grid position.

    Only devices already in the file can be placed (their library definition is embedded).
    The copy gets its own reference (`ref`, default: the prefix of an existing part of that device plus the
    next free number, e.g. U8 or R1), because Proteus annotates unreferenced copies itself on load and may
    still re-annotate a reference it considers unsuitable: the netlist name is authoritative.  The copy has
    no wires.  Annotated sources work as well (verified with a NOT gate), the copy simply has no ROOT.CDB
    record, which Proteus accepts."""
    import copy
    sheet = load(project)
    src = sheet.component(like)
    new = copy.deepcopy(src)
    origin = [round(x_grid * GRID / (GRID // 2)) * (GRID // 2), round(y_grid * GRID / (GRID // 2)) * (GRID // 2)]
    dx, dy = origin[0] - src["origin"][0], origin[1] - src["origin"][1]
    new["origin"] = origin
    for t in new["labels"]:
        t["xy"] = [t["xy"][0] + dx, t["xy"][1] + dy]
    if rotation is not None:
        if rotation not in _ROTATIONS:
            raise dsn.DsnError("rotation must be 0, 90, 180 or 270")
        _, mirror = struct.unpack("<hH", new["transform"])
        new["transform"] = struct.pack("<hH", _ROTATIONS[rotation], mirror)
    if value is not None:
        new["labels"][1]["text"] = value
    new["identity"] = _next_identity(sheet)
    used = {o["labels"][0]["text"].upper() for o in sheet.components()}
    used |= {r.split(":")[0] for r in used}      # U4:A..U4:D occupy U4 as well (one package)
    if ref is None:
        # Proteus re-annotates a reference whose prefix does not suit the device (N1 for a NOT gate became
        # U8), so reuse the prefix of an annotated part of the same device when there is one.
        same = [o["labels"][0]["text"] for o in sheet.components()
                if o["device"] == src["device"] and o["labels"][0]["text"]]
        # leading letters only: "U9:A" (a gate of a multi-gate package) gives "U", not "UA"
        prefix = (re.match(r"[A-Za-z]*", same[0]).group() or "U") if same else \
            ("R" if src["device"].upper() == "RES" else "U")
        n = 1
        while f"{prefix}{n}" in used:
            n += 1
        ref = f"{prefix}{n}"
    elif ref.upper() in used:
        raise dsn.DsnError(f"reference {ref} is already used")
    new["labels"][0]["text"] = ref
    new["pointers"] = [0] * len(new["pointers"])
    new["start"] = None
    # the new part's pins must not land on anything existing
    _placement_check(sheet, new)
    sheet.objects.append(new)
    sheet.reschedule()
    save(project, sheet)
    return {"part": ref, "grid": [origin[0] / GRID, origin[1] / GRID],
            "pins": [{"name": p["name"], "grid": [p["xy"][0] / GRID, p["xy"][1] / GRID]}
                     for p in pins(sheet, new) if not p["hidden"]]}


def check(project: str | Path) -> dict:
    """Offline sanity check before simulating: visible pins with no wire (per part) and net names that occur
    only once on the sheet (a terminal whose partner is missing or misspelled; GND/POWER and bus names whose
    members are labelled elsewhere are not reported).  Unused pins are normal (spare decoder outputs, unused
    8259 inputs); look for the ones the design needs."""
    sheet = load(project)
    open_pins, unreadable = {}, []
    for comp in sheet.components():
        try:
            missing = [p["name"] for p in pins(sheet, comp) if not p["hidden"] and not p["connected"]]
        except dsn.DsnError:
            unreadable.append(sheet.name_of(comp))
            continue
        if missing:
            open_pins[sheet.name_of(comp)] = missing
    names = labels(project)
    singles = []
    for name, info in names.items():
        # only terminals: wire labels used once are usually a template's spare address lines
        if name in ("GND", "POWER") or info["terminals"] != 1 or info["wire_labels"]:
            continue
        m = re.fullmatch(r"(.+)\[(\d+)\.\.(\d+)\]", name)
        if m:
            continue                     # bus terminals join through their bus wire, not by name
        if any(re.fullmatch(re.escape(b.split("[")[0]) + r"\d+", name)
               for b in names if b.endswith("]")):
            continue                     # AD5 alone still joins the AD[0..15] bus
        singles.append({"name": name, "pins": info["pins"]})
    out = {"unconnected_pins": open_pins, "single_use_names": singles}
    if unreadable:
        out["pin_table_unreadable"] = unreadable
    return out


def labels(project: str | Path) -> dict:
    """Net names on the sheet without Proteus: every terminal and wire label, with the pins wired straight to
    them (a terminal's wire usually ends on one pin).  Power/ground terminals appear as GND / POWER.  Pins
    reached only through longer wire chains or junctions are not followed; use net_of for full nets."""
    sheet = load(project)
    owner: dict[int, list[str]] = {}
    for comp in sheet.components():
        try:
            comp_pins = pins(sheet, comp)
        except dsn.DsnError:
            continue
        for pin, ptr in zip(comp_pins, comp["pointers"]):
            if ptr:
                owner.setdefault(ptr, []).append(f"{sheet.name_of(comp)}.{pin['name']}")
    out: dict[str, dict] = {}
    for o in sheet.objects:
        if o["type"] == "terminal":
            name = o["label"]["text"] or {"$TERGROUND": "GND", "$TERPOWER": "POWER"}.get(o["device"], o["device"])
            e = out.setdefault(name, {"terminals": 0, "wire_labels": 0, "pins": []})
            e["terminals"] += 1
            e["pins"] += owner.get(o["pointers"][0], [])
        elif o["type"] == "wire":
            for lab in o["labels"]:
                e = out.setdefault(lab["text"], {"terminals": 0, "wire_labels": 0, "pins": []})
                e["wire_labels"] += 1
                e["pins"] += owner.get(o["start"], [])
    for o in sheet.objects:                      # clock / DC generators: their name labels their net
        if o["type"] == "instrument" and o["id"].startswith("M"):
            props = dict(re.findall(r"\{([^=}]+)=([^}]*)\}", o["props"].decode("latin-1")))
            e = out.setdefault(o["labels"][0]["text"] or o["device"], {"terminals": 0, "wire_labels": 0, "pins": []})
            e["generator"] = {"device": o["device"], **{k: v for k, v in props.items()
                                                        if k in ("CLOCK", "VALUE", "INIT", "START")}}
            e["pins"] += owner.get(o["pointers"][0], [])
    return dict(sorted(out.items()))


def generators(project: str | Path) -> list[dict]:
    """Clock / DC generators with their settings and the pins their drawn wires reach (through junctions).
    Proteus's netlist leaves generators out, so this is how net_of reports what drives a clock pin."""
    sheet = load(project)
    holders = _holders(sheet)
    out = []
    for o in sheet.objects:
        if o["type"] != "instrument" or not o["id"].startswith("M"):
            continue
        props = dict(re.findall(r"\{([^=}]+)=([^}]*)\}", o["props"].decode("latin-1")))
        reached = []
        if o["pointers"] and o["pointers"][0]:
            for w in _cluster(sheet, o["pointers"][0]):
                for h, slot in holders.get(w["start"], []):
                    if h["type"] == "component":
                        try:
                            name = pins(sheet, h)[slot]["name"]
                        except dsn.DsnError:
                            name = f"slot{slot}"
                        reached.append(f"{sheet.name_of(h)}.{name}")
        out.append({"name": o["labels"][0]["text"] or o["device"], "device": o["device"],
                    **{k.lower(): v for k, v in props.items() if k in ("CLOCK", "VALUE", "INIT", "START")},
                    "pins": reached})
    return out


def _reroute(sheet: dsn.Sheet, w: dict, pin: dict, far: list[int], comp: dict, slot: int) -> list[list[int]] | None:
    """A fresh maze route for wire w from its far end `far` to `pin` (returned far end first), or None."""
    holder = next(((o, s) for o, s in _holders(sheet)[w["start"]] if not (o is comp and s == slot)), None)
    if holder is None:
        return None
    o, s = holder
    if o["type"] == "component":
        goals = route.pin_goal(pins(sheet, o)[s])
    elif far[0] % GRID == 0 and far[1] % GRID == 0:
        goals = {(far[0] // GRID, far[1] // GRID): ([], None)}
    else:
        return None
    i = sheet.objects.index(w)
    del sheet.objects[i]                     # the wire being replaced is no obstacle
    try:
        pts, _ = route.route(sheet, pin, goals, {tuple(pin["xy"]), tuple(far)})
        pts = _simplify(pts)
        _check_clear(sheet, pts, [pin["xy"], far])
    except dsn.DsnError:
        return None
    finally:
        sheet.objects.insert(i, w)
    return pts[::-1]


def move_part(project: str | Path, ref: str, x_grid: float, y_grid: float) -> dict:
    """Move a part to a grid position.  Its labels move with it and its wires are dragged along (rubber band,
    or a fresh route where the stretched wire would enter a pin through its own part); refuses if a pin would
    land on an existing connection point or wire."""
    sheet = load(project)
    comp = sheet.component(ref)
    half = GRID // 2
    origin = [round(x_grid * GRID / half) * half, round(y_grid * GRID / half) * half]
    dx, dy = origin[0] - comp["origin"][0], origin[1] - comp["origin"][1]
    before = {p["slot"]: p for p in pins(sheet, comp)}
    own_wires = {k for k in comp["pointers"] if k}
    comp["origin"] = origin
    for t in comp["labels"]:
        t["xy"] = [t["xy"][0] + dx, t["xy"][1] + dy]
    after = {p["slot"]: p for p in pins(sheet, comp)}
    _placement_check(sheet, comp, ignore=tuple(w for w in sheet.wires() if w["start"] in own_wires))
    # wired pins: drag each wire's end along (rubber band), keeping the wire orthogonal
    wires = {w["start"]: w for w in sheet.wires()}
    stretched = []
    for slot, key in enumerate(comp["pointers"]):
        if not key:
            continue
        w, old, new, out = wires[key], before[slot]["xy"], after[slot]["xy"], after[slot]["out"]
        pts = [list(q) for q in w["points"]]
        ends = [i for i in (0, len(pts) - 1) if pts[i] == old]
        if not ends:
            continue
        at_end = ends[0] == len(pts) - 1
        seq = pts[:] if at_end else pts[::-1]               # make the pin end last
        seq[-1] = new
        prev = seq[-2]
        if prev[0] != new[0] and prev[1] != new[1]:
            # leave the pin along the way it faces, then turn
            corner = [prev[0], new[1]] if out[0] else [new[0], prev[1]]
            seq.insert(-1, corner)
        sgn = lambda v: (v > 0) - (v < 0)
        try:
            seq = _simplify(seq)
            ok = (sgn(seq[-1][0] - seq[-2][0]), sgn(seq[-1][1] - seq[-2][1])) == (-out[0], -out[1])
        except dsn.DsnError:
            ok = False                       # doubled back on itself
        if ok:
            i = sheet.objects.index(w)
            del sheet.objects[i]
            ok = route.path_clear(route.Obstacles(sheet, {tuple(new), tuple(seq[0])}), seq)
            sheet.objects.insert(i, w)
        if not ok:
            # the stretched wire would cross a part, enter the pin from inside or run along another wire
            seq = _reroute(sheet, w, after[slot], seq[0], comp, slot)
            if seq is None:
                raise dsn.DsnError(f"no clean way to drag the wire on {ref}.{after[slot]['name']} to the new "
                                   f"position; pick another spot or delete the wire first")
        w["points"] = seq if at_end else seq[::-1]
        stretched.append(f"{sheet.name_of(comp)}.{after[slot]['name']}")
    save(project, sheet)
    return {"part": ref, "grid": [origin[0] / GRID, origin[1] / GRID], "wires_stretched": stretched,
            "note": "wires were dragged along; check the layout with a screenshot" if stretched else None,
            "pins": [{"name": p["name"], "grid": [p["xy"][0] / GRID, p["xy"][1] / GRID]}
                     for p in after.values() if not p["hidden"]]}


def delete_part(project: str | Path, ref: str) -> dict:
    """Delete a part (or a generator, by name) with all its wires.  A terminal or generator at the far end of
    one of those wires goes too; a pin or junction at the far end just loses the wire.  Parts with a ROOT.CDB
    record are refused (Proteus hangs on a rewritten CDB and a stale record is untested)."""
    sheet = load(project)
    target = next((o for o in sheet.objects if o["type"] == "instrument" and o["labels"][0]["text"] == ref), None)
    if target is None:
        target = sheet.component(ref)
        if _in_cdb(project, target):
            raise dsn.DsnError(f"{ref} has a ROOT.CDB record; delete it in Proteus instead")
    wires = {o["start"]: o for o in sheet.objects if o["type"] == "wire"}
    doomed, gone, touched = [target], [], []
    for key in [k for k in target["pointers"] if k]:
        doomed.append(wires[key])
        for o in sheet.objects:
            if o is target or o["type"] == "wire" or key not in o.get("pointers", []):
                continue
            if o["type"] in ("terminal", "instrument") and o["pointers"].count(0) + 1 == len(o["pointers"]):
                doomed.append(o)
                gone.append(o["label"]["text"] if o["type"] == "terminal" else o["labels"][0]["text"])
            else:
                o["pointers"] = [0 if k == key else k for k in o["pointers"]]
                if o["type"] == "junction":
                    touched.append(o)
    ids = {id(o) for o in doomed}
    sheet.objects = [o for o in sheet.objects if id(o) not in ids]
    _tidy_junctions(sheet, touched)
    sheet.reschedule()
    save(project, sheet)
    return {"deleted": ref, "wires": len(doomed) - 1 - len(gone), "terminals_removed": gone}


def rename_label(project: str | Path, old: str, new: str) -> dict:
    """Rename every terminal and wire label called `old` to `new` (e.g. the lab7 placeholders YA1..YA6).
    Pins on terminals/labels with the same name are connected, so this moves them onto net `new`."""
    sheet = load(project)
    n = 0
    for o in sheet.objects:
        if o["type"] == "terminal" and o["label"]["text"] == old:
            o["label"]["text"] = new
            n += 1
        elif o["type"] == "wire":
            for t in o["labels"]:
                if t["text"] == old:
                    t["text"] = new
                    n += 1
    if not n:
        raise dsn.DsnError(f"no terminal or wire label named {old!r}")
    save(project, sheet)
    return {"renamed": old, "to": new, "count": n}


def set_property(project: str | Path, ref: str, key: str, value: str) -> dict:
    """Set {KEY=VALUE} in a component's property text (added if missing)."""
    sheet = load(project)
    comp = sheet.component(ref)
    if _in_cdb(project, comp):
        raise dsn.DsnError(f"{ref} is an annotated part whose properties Proteus reads from ROOT.CDB, which this "
                           "tool cannot rewrite yet; change it in Proteus (Edit Component) instead")
    text = comp["props"].decode("latin-1")
    pat = re.compile(r"\{" + re.escape(key) + r"=[^}]*\}")
    new = f"{{{key}={value}}}"
    text = pat.sub(lambda m: new, text, count=1) if pat.search(text) else text.rstrip("\n") + "\n" + new + "\n"
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
    if _in_cdb(project, comp):
        raise dsn.DsnError(f"{ref} is an annotated part whose value Proteus reads from ROOT.CDB, which this "
                           "tool cannot rewrite yet; change it in Proteus (Edit Component) instead")
    comp["labels"][1]["text"] = value
    save(project, sheet)
    return {"ref": ref, "value": value}
