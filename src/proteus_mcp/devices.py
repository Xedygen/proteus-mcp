"""Device pin tables from the library copy embedded in ROOT.DSN, and pin connection points on the sheet.

Each device used in a design is stored in the file's library section with its pin table.  A pin record is
4 flag bytes, i32 x, i32 y, i16 angle (tenths of a degree), u16 mirror (1 = x, 2 = y), then NUL-terminated
glyph name ($PINDEFAULT ...), pin name and pin number.  The pin's connection point is the anchor moved by the
glyph length along the pin's direction.  Pins appear in the same order as the pointer slots of a component
record.  Verified against every connected pin of the 27 sample projects (3213 of 3213 land exactly on the
attached wire's endpoint).

Format knowledge is shared with chen0416ccc-cpu/Proteus-MCP (MIT), whose Proteus 9 reader uses the same pin
records.
"""
from __future__ import annotations

import struct

from .dsn import DsnError, Sheet

GLYPH_LENGTH = {"$PINSHORT": 254000, "$PINDEFAULT": 508000, "$PININVERT": 508000, "$PINPOSCLK": 508000,
                "$PINNEGCLK": 508000, "$PINBUS": 508000}


def transform(point, angle: int = 0, mirror: int = 0) -> list[int]:
    x, y = point
    if mirror & 1:
        x = -x
    if mirror & 2:
        y = -y
    angle %= 3600
    if angle == 0:
        return [x, y]
    if angle == 900:
        return [-y, x]
    if angle == 1800:
        return [-x, -y]
    if angle == 2700:
        return [y, -x]
    raise DsnError(f"non-orthogonal angle {angle / 10} degrees")


def _cstr(data: bytes, pos: int) -> tuple[str, int]:
    end = data.index(b"\0", pos)
    return data[pos:end].decode("latin-1"), end + 1


def pin_table(sheet: Sheet, device: str, count: int) -> list[dict]:
    """The `count` pins of `device`, in pointer-slot order."""
    data, limit = sheet.data, sheet.circuit
    key = bytes([len(device)]) + device.encode("latin-1")
    start = 0
    while True:
        i = data.find(key, start, limit)
        if i < 0:
            raise DsnError(f"no pin table for device {device!r} in the embedded library")
        start = i + 1
        origin = data.find(b"$MKRORIGIN\0", i, i + 4000)
        p = data.find(b"$PIN", origin if origin > 0 else i, i + 8000)
        if p < 0:
            continue
        pos, pins = p - 16, []
        try:
            for _ in range(count):
                if data[pos + 16:pos + 20] != b"$PIN":
                    raise ValueError
                flags = data[pos:pos + 4]
                x, y, angle, mirror = struct.unpack_from("<iihH", data, pos + 4)
                glyph, q = _cstr(data, pos + 16)
                name, q = _cstr(data, q)
                number, q = _cstr(data, q)
                pins.append({"name": name, "number": number, "glyph": glyph, "anchor": [x, y],
                             "angle": angle, "mirror": mirror, "flags": flags.hex()})
                pos = q
        except (ValueError, struct.error):
            continue
        return pins


def pins(sheet: Sheet, component: dict) -> list[dict]:
    """Pins of a placed component with their sheet coordinates and connection state."""
    table = pin_table(sheet, component["device"], len(component["pointers"]))
    angle, mirror = struct.unpack("<hH", component["transform"])
    out = []
    for slot, (pin, ptr) in enumerate(zip(table, component["pointers"])):
        length = GLYPH_LENGTH.get(pin["glyph"], 0)
        off = transform([-length, 0], pin["angle"], pin["mirror"])
        local = [pin["anchor"][0] + off[0], pin["anchor"][1] + off[1]]
        v = transform(local, angle, mirror)
        # outward direction: from the pin's anchor on the body towards its free end
        d = transform(transform([-1, 0], pin["angle"], pin["mirror"]), angle, mirror)
        out.append({"slot": slot, "name": pin["name"], "number": pin["number"], "glyph": pin["glyph"],
                    "xy": [component["origin"][0] + v[0], component["origin"][1] + v[1]],
                    "out": d, "connected": bool(ptr), "bus": pin["glyph"] == "$PINBUS",
                    # hidden power pins (VCC/GND of logic parts): flag byte 1 has no 0x02 bit
                    "hidden": not (int(pin["flags"][2:4], 16) & 2)})
    return out


def _norm(name: str) -> str:
    return name.replace("$", "").upper()


def find_pin(sheet: Sheet, ref: str, pin: str) -> tuple[dict, dict]:
    """(component, pin) by reference and pin name or number (overbars written as $X$ or plain)."""
    comp = sheet.component(ref)
    table = pins(sheet, comp)
    want = _norm(pin)
    hits = [p for p in table if _norm(p["name"]) == want] or [p for p in table if p["number"] == pin]
    if len(hits) != 1:
        raise DsnError(f"{ref} has no unique pin {pin!r}; pins: {[p['name'] for p in table]}")
    return comp, hits[0]
