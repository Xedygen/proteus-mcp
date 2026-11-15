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

from . import cdb, dsn, pdsprj
from .devices import find_pin, pins

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
                    raise dsn.DsnError(f"route {[[q / GRID for q in p] for p in points]} runs along an existing "
                                       f"wire near {[c[0] / GRID, c[1] / GRID]}; choose another via_x_grid")


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
    for comp, pin, label in ((ca, pa, a), (cb, pb, b)):
        if pin["connected"]:
            raise dsn.DsnError(f"{label} already has a wire; Proteus allows one wire per pin end")
        if pin["bus"]:
            raise dsn.DsnError(f"{label} is a bus pin; bus connections are not supported yet")
        if pin["hidden"]:
            raise dsn.DsnError(f"{label} is a hidden power pin; Proteus connects it to its power net itself")
    template = next((w for w in sheet.wires() if w["flags"] == 0), None)
    if template is None:
        raise dsn.DsnError("no existing wire to copy the wire style from")
    key = sheet.new_key()
    if via_x_grid is not None:
        points = _route(pa["xy"], pb["xy"], round(via_x_grid * GRID))
        _check_clear(sheet, points, [pa["xy"], pb["xy"]])
    else:
        mid = round((pa["xy"][0] + pb["xy"][0]) / 2 / GRID)
        lo, hi = sorted((pa["xy"][0] // GRID, pb["xy"][0] // GRID))
        candidates = sorted(range(lo + 1, hi), key=lambda x: abs(x - mid)) or [mid]
        points, last = None, None
        for x in candidates:
            try:
                trial = _route(pa["xy"], pb["xy"], x * GRID)
                _check_clear(sheet, trial, [pa["xy"], pb["xy"]])
                points = trial
                break
            except dsn.DsnError as e:
                last = e
        if points is None:
            raise dsn.DsnError(f"no free column between the pins ({last}); pass via_x_grid")
    # ISIS binds points[0] to the end of whichever object is serialised first (4651/4651 sample wires)
    order = {id(o): i for i, o in enumerate(sheet.objects)}
    first_a = (order[id(ca)], pa["slot"]) <= (order[id(cb)], pb["slot"])
    if not first_a:
        points = points[::-1]
    sheet.objects.append({"type": "wire", "start": key, "flags": 0, "style": template["style"],
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
_ATTRIBUTES = {"$TERDEFAULT": "090000000000", "$TERBUS": "090000000000", "$TERGROUND": "0d0000000000", "$TERPOWER": "0d0000000000"}


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
    if template is None:
        raise dsn.DsnError("no terminal in this file to copy the label style from")
    want_flags = 1 if kind == "$TERBUS" else 0
    wire_tpl = next((w for w in sheet.wires() if w["flags"] == want_flags), None)
    if wire_tpl is None:
        raise dsn.DsnError("no existing wire of that kind (bus/normal) to copy the style from")
    key = sheet.new_key()
    label = {"text": net if kind in ("$TERDEFAULT", "$TERBUS") else "", "xy": [origin[0] + label_off[0], origin[1] + label_off[1]],
             "transform": bytes.fromhex(label_tf), "style": template["label"]["style"]}
    term = {"type": "terminal", "origin": origin, "transform": bytes.fromhex(transform), "device": kind,
            "attributes": bytes.fromhex(_ATTRIBUTES[kind]), "label": label, "pointers": [key]}
    sheet.objects.append(term)
    sheet.objects.append({"type": "wire", "start": key, "flags": want_flags, "style": wire_tpl["style"],
                          "labels": [], "points": [p["xy"], t]})
    comp["pointers"][p["slot"]] = key
    sheet.reschedule()
    save(project, sheet)
    # echo what the pin resolved to: names win over numbers, so on a 74154 "5" is output Y5 (pin 6)
    return {"pin": pin, "resolved": f"{p['name']} (pin {p['number'] or '?'})", "terminal": kind, "net": net,
            "terminal_grid": [t[0] / GRID, t[1] / GRID]}


def _num(x: float) -> str:
    """Plain decimal for a property value (640000, 9600, 1.5): Proteus files never use exponent form."""
    return str(int(x)) if float(x).is_integer() else repr(float(x))


def _next_identity(sheet: dsn.Sheet) -> int:
    """Components and instruments share one identity space (an instrument's u16 field); Proteus derives
    references from it, so a clash shows up as "Duplicate part reference" at simulation start."""
    used = [o["identity"] for o in sheet.components()]
    used += [o["u16"] for o in sheet.objects if o["type"] == "instrument"]
    return max(used + [0]) + 1


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
    wire_tpl = next((w for w in sheet.wires() if w["flags"] == 0), None)
    if wire_tpl is None:
        raise dsn.DsnError("no existing wire to copy the style from")
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
    sheet.objects.append({"type": "wire", "start": key, "flags": 0, "style": wire_tpl["style"],
                          "labels": [], "points": [p["xy"], origin]})
    comp["pointers"][p["slot"]] = key
    sheet.reschedule()
    save(project, sheet)
    return {"pin": pin, "generator": name, "frequency_hz": frequency_hz,
            "generator_grid": [origin[0] / GRID, origin[1] / GRID]}


_ROTATIONS = {0: 0, 90: 900, 180: 1800, 270: 2700}


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
    spots = _hot_spots(sheet)
    sheet.objects.append(new)
    clash = [p for p in pins(sheet, new) if tuple(p["xy"]) in spots and not p["hidden"]]
    if clash:
        raise dsn.DsnError(f"pin {clash[0]['name']} would land on an existing connection point at "
                           f"{[clash[0]['xy'][0] / GRID, clash[0]['xy'][1] / GRID]}")
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
    open_pins = {}
    for comp in sheet.components():
        missing = [p["name"] for p in pins(sheet, comp) if not p["hidden"] and not p["connected"]]
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
    return {"unconnected_pins": open_pins, "single_use_names": singles}


def labels(project: str | Path) -> dict:
    """Net names on the sheet without Proteus: every terminal and wire label, with the pins wired straight to
    them (a terminal's wire usually ends on one pin).  Power/ground terminals appear as GND / POWER.  Pins
    reached only through longer wire chains or junctions are not followed; use net_of for full nets."""
    sheet = load(project)
    owner: dict[int, list[str]] = {}
    for comp in sheet.components():
        for pin, ptr in zip(pins(sheet, comp), comp["pointers"]):
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
    return dict(sorted(out.items()))


def move_part(project: str | Path, ref: str, x_grid: float, y_grid: float) -> dict:
    """Move a part that has no wires yet to a grid position (e.g. right after schematic_place_devices, whose
    drop point is only approximate).  Its labels move with it; refuses if a pin would land on an existing
    connection point."""
    sheet = load(project)
    comp = sheet.component(ref)
    if any(comp["pointers"]):
        raise dsn.DsnError(f"{ref} already has wires; moving wired parts is not supported")
    half = GRID // 2
    origin = [round(x_grid * GRID / half) * half, round(y_grid * GRID / half) * half]
    dx, dy = origin[0] - comp["origin"][0], origin[1] - comp["origin"][1]
    spots = _hot_spots(sheet) - {tuple(p["xy"]) for p in pins(sheet, comp)}
    comp["origin"] = origin
    for t in comp["labels"]:
        t["xy"] = [t["xy"][0] + dx, t["xy"][1] + dy]
    clash = [p for p in pins(sheet, comp) if tuple(p["xy"]) in spots and not p["hidden"]]
    if clash:
        raise dsn.DsnError(f"pin {clash[0]['name']} would land on an existing connection point")
    save(project, sheet)
    return {"part": ref, "grid": [origin[0] / GRID, origin[1] / GRID],
            "pins": [{"name": p["name"], "grid": [p["xy"][0] / GRID, p["xy"][1] / GRID]}
                     for p in pins(sheet, comp) if not p["hidden"]]}


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
    doomed, gone = [target], []
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
    ids = {id(o) for o in doomed}
    sheet.objects = [o for o in sheet.objects if id(o) not in ids]
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
