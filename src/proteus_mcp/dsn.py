"""Reader and writer for the object section of the Proteus 8.x ISIS schematic file (ROOT.DSN).

Layout worked out from the Proteus 8.6 lab projects (FILEVER 814); see docs/FORMAT.md.  It is close to the
Proteus 9 layout documented by chen0416ccc-cpu/Proteus-MCP (MIT), with one-byte string lengths.

Coordinates are signed 32-bit integers in units of 10 nm; one 0.1 inch grid step is 254000.  Pointers inside
records are absolute file offsets of wire records.

parse(data) returns a Sheet; Sheet.objects can be edited and Sheet.build() returns new file bytes with every
pointer relocated.  Records are kept with all their raw fields, so an unedited sheet rebuilds byte for byte.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

GRID = 254000
SIGNATURE = b"ISIS CIRCUIT FILE\x1a"
OBJECTS = b"OBJECT DATA\x00"


class DsnError(Exception):
    pass


# ----- primitive reader / writer ------------------------------------------------------------------
class Reader:
    def __init__(self, data: bytes, offset: int = 0):
        self.data, self.offset = data, offset

    def read(self, n: int) -> bytes:
        if n < 0 or self.offset + n > len(self.data):
            raise DsnError(f"truncated read of {n} at {self.offset:#x}")
        start = self.offset
        self.offset += n
        return self.data[start:self.offset]

    def u8(self) -> int:
        return self.read(1)[0]

    def u16(self) -> int:
        return struct.unpack("<H", self.read(2))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.read(4))[0]

    def xy(self) -> list[int]:
        return list(struct.unpack("<ii", self.read(8)))

    def pstr(self) -> str:
        return self.read(self.u8()).decode("latin-1")

    def text(self) -> dict:
        start = self.offset
        if self.u8() != 0xFF:
            raise DsnError(f"expected text marker at {start:#x}")
        value = self.pstr()
        xy = self.xy()
        transform = self.read(6)
        style_len = self.u32()
        if not 4 <= style_len <= 4096:
            raise DsnError(f"bad text style length {style_len} at {start:#x}")
        return {"text": value, "xy": xy, "transform": transform, "style": self.read(style_len - 4)}


def _pstr(s: str) -> bytes:
    raw = s.encode("latin-1")
    if len(raw) > 255:
        raise DsnError(f"string longer than 255 bytes: {s[:40]!r}")
    return bytes([len(raw)]) + raw


def pack_text(t: dict) -> bytes:
    return (b"\xff" + _pstr(t["text"]) + struct.pack("<ii", *t["xy"]) + t["transform"]
            + struct.pack("<I", len(t["style"]) + 4) + t["style"])


def _xy(p) -> bytes:
    return struct.pack("<ii", *p)


def _u32s(values) -> bytes:
    return b"".join(struct.pack("<I", v) for v in values)


# ----- records ---------------------------------------------------------------------------------------
# Every record is a dict with "type", the raw fields needed to rebuild it, and "pointers" (wire offsets,
# 0 = unconnected).  Wires carry "start" (their offset in the file they were read from), which is the key the
# pointers refer to.

def _props(r: Reader, shown: str) -> tuple[int, bytes, bool]:
    """u32 n, then n bytes unless the shown text already has length n (then it is the complete text)."""
    n = r.u32()
    raw = shown.encode("latin-1")
    if len(raw) == n:
        return n, raw, True
    return n, r.read(n), False


def read_component(r: Reader) -> dict:
    o = {"type": "component", "start": r.offset, "kind": r.u8()}
    o["labels"] = [r.text() for _ in range(4)]          # reference, value, sub-circuit name, properties
    o["props_n"], o["props"], o["props_inline"] = _props(r, o["labels"][3]["text"])
    pins = r.u16()
    o["device"] = r.pstr()
    o["origin"] = r.xy()
    o["transform"] = r.read(4)
    o["identity"] = r.u32()
    o["pin_header"] = r.read(9)
    o["pointers"] = [r.u32() for _ in range(pins)]
    return o


def write_component(o: dict, ptr) -> bytes:
    return (bytes([o["kind"]]) + b"".join(pack_text(t) for t in o["labels"])
            + struct.pack("<I", o["props_n"]) + (b"" if o["props_inline"] else o["props"])
            + struct.pack("<H", len(o["pointers"])) + _pstr(o["device"]) + _xy(o["origin"])
            + o["transform"] + struct.pack("<I", o["identity"]) + o["pin_header"]
            + _u32s(ptr(p) for p in o["pointers"]))


def read_wire(r: Reader) -> dict:
    o = {"type": "wire", "start": r.offset, "flags": r.u8()}
    n = r.u32()
    o["style"] = r.read(n - 4)
    o["labels"] = [r.text() for _ in range(r.u16())]
    o["points"] = [r.xy() for _ in range(r.u16())]
    return o


def write_wire(o: dict, ptr) -> bytes:
    return (bytes([o["flags"]]) + struct.pack("<I", len(o["style"]) + 4) + o["style"]
            + struct.pack("<H", len(o["labels"])) + b"".join(pack_text(t) for t in o["labels"])
            + struct.pack("<H", len(o["points"])) + b"".join(_xy(p) for p in o["points"]))


def read_junction(r: Reader) -> dict:
    o = {"type": "junction", "start": r.offset}
    r.u8()
    o["origin"] = r.xy()
    o["pointers"] = [r.u32() for _ in range(4)]
    return o


def write_junction(o: dict, ptr) -> bytes:
    return b"\x01" + _xy(o["origin"]) + _u32s(ptr(p) for p in o["pointers"])


def read_terminal(r: Reader) -> dict:
    o = {"type": "terminal", "start": r.offset}
    r.u8()
    o["origin"] = r.xy()
    o["transform"] = r.read(4)
    o["device"] = r.pstr()
    o["attributes"] = r.read(6)
    o["label"] = r.text()
    o["pointers"] = [r.u32()]
    return o


def write_terminal(o: dict, ptr) -> bytes:
    return (b"\x10" + _xy(o["origin"]) + o["transform"] + _pstr(o["device"]) + o["attributes"]
            + pack_text(o["label"]) + _u32s(ptr(p) for p in o["pointers"]))


def read_instrument(r: Reader) -> dict:
    """Generators ($DCLOCK0, $DCGEN, id 'M...') and probes ($VPROBE, id 'P...').  The kind byte varies."""
    o = {"type": "instrument", "start": r.offset, "kind": r.u8()}
    o["origin"] = r.xy()
    o["transform"] = r.read(4)
    o["u16"] = r.u16()
    o["id"] = r.pstr()
    o["device"] = r.pstr()
    o["labels"] = [r.text(), r.text()]                  # name label, properties
    o["props_n"], o["props"], o["props_inline"] = _props(r, o["labels"][1]["text"])
    o["flags"] = r.u16()
    o["pointers"] = [r.u32()]
    o["tail"] = r.read(4)
    if o["id"].startswith("M"):
        o["tail_c"] = r.u8()
        o["blob_n"] = r.u32()
        o["blob"] = r.read(o["blob_n"] - 4) if o["blob_n"] >= 4 else b""
    return o


def write_instrument(o: dict, ptr) -> bytes:
    out = (bytes([o["kind"]]) + _xy(o["origin"]) + o["transform"] + struct.pack("<H", o["u16"])
           + _pstr(o["id"]) + _pstr(o["device"]) + b"".join(pack_text(t) for t in o["labels"])
           + struct.pack("<I", o["props_n"]) + (b"" if o["props_inline"] else o["props"])
           + struct.pack("<H", o["flags"]) + _u32s(ptr(p) for p in o["pointers"]) + o["tail"])
    if o["id"].startswith("M"):
        out += bytes([o["tail_c"]]) + struct.pack("<I", o["blob_n"]) + o["blob"]
    return out


def read_graphic(r: Reader) -> dict:
    """2D graphic (kind 0x20): kind, xy, 4 bytes, u32 n counting itself, n-4 bytes."""
    o = {"type": "graphic", "start": r.offset, "kind": r.u8()}
    o["origin"] = r.xy()
    o["transform"] = r.read(4)
    o["body"] = r.read(r.u32() - 4)
    o["pointers"] = []
    return o


def write_graphic(o: dict, ptr) -> bytes:
    return (bytes([o["kind"]]) + _xy(o["origin"]) + o["transform"] + struct.pack("<I", len(o["body"]) + 4)
            + o["body"])


def read_script(r: Reader) -> dict:
    """Script / notes block (kind 0x02)."""
    o = {"type": "script", "start": r.offset, "kind": r.u8()}
    o["origin"] = r.xy()
    o["transform"] = r.read(4)
    o["label"] = r.text()
    o["text_n"], o["text"], o["text_inline"] = _props(r, o["label"]["text"])
    o["pointers"] = []
    return o


def write_script(o: dict, ptr) -> bytes:
    return (bytes([o["kind"]]) + _xy(o["origin"]) + o["transform"] + pack_text(o["label"])
            + struct.pack("<I", o["text_n"]) + (b"" if o["text_inline"] else o["text"]))


WRITERS = {"component": write_component, "wire": write_wire, "junction": write_junction,
           "terminal": write_terminal, "instrument": write_instrument, "graphic": write_graphic,
           "script": write_script}


def _is_wire(data: bytes, at: int) -> bool:
    """flag byte 0/1, u32 style length L, style ending in b'WIRE\\x00' (also 'BUS WIRE')."""
    if data[at] not in (0, 1) or at + 5 > len(data):
        return False
    n = struct.unpack_from("<I", data, at + 1)[0]
    return 9 <= n <= 512 and data[at + 1 + n - 5:at + 1 + n] == b"WIRE\x00"


def _is_instrument(data: bytes, at: int) -> bool:
    return (at + 27 < len(data) and data[at + 15] == 9 and 65 <= data[at + 16] <= 90
            and data[at + 25] < 32 and data[at + 26] == ord("$"))


def read_object(data: bytes, r: Reader) -> dict:
    at = r.offset
    if _is_wire(data, at):
        return read_wire(r)
    if _is_instrument(data, at):
        return read_instrument(r)
    k = data[at]
    if k in (0, 8) and data[at + 1] == 0xFF:
        return read_component(r)
    if k == 0x01:
        return read_junction(r)
    if k == 0x10:
        return read_terminal(r)
    if k == 0x20:
        return read_graphic(r)
    if k == 0x02:
        return read_script(r)
    raise DsnError(f"unknown object kind {k:#04x} at {at:#x}")


# ----- the root sheet --------------------------------------------------------------------------------
@dataclass
class Sheet:
    data: bytes                      # the original file
    circuit: int                     # offset of the root sheet's "ISIS CIRCUIT FILE" signature
    begin: int                       # first object byte
    end: int                         # offset of the 0xFF terminator
    objects: list[dict] = field(default_factory=list)

    # -- queries --
    def components(self) -> list[dict]:
        return [o for o in self.objects if o["type"] == "component"]

    def component(self, ref: str) -> dict:
        for o in self.components():
            if o["labels"][0]["text"].upper() == ref.upper():
                return o
        raise DsnError(f"no component {ref!r}")

    def wires(self) -> list[dict]:
        return [o for o in self.objects if o["type"] == "wire"]

    # -- writing --
    def build(self) -> bytes:
        """Serialise the objects, relocate all wire pointers and the sheet directory, return file bytes.

        Wires are matched to pointers through their "start" key.  New wires need a unique key that no
        existing pointer uses (use new_key())."""
        keys = [o["start"] for o in self.objects if o["type"] == "wire"]
        if len(set(keys)) != len(keys):
            raise DsnError("duplicate wire keys")
        # first pass: sizes do not depend on pointer values, so offsets can be computed directly
        offsets, cursor = {}, self.begin
        identity = lambda p: p
        for o in self.objects:
            if o["type"] == "wire":
                offsets[o["start"]] = cursor
            cursor += len(WRITERS[o["type"]](o, identity))

        def ptr(p: int) -> int:
            if p == 0:
                return 0
            if p not in offsets:
                raise DsnError(f"pointer {p:#x} does not refer to a wire")
            return offsets[p]
        payload = b"".join(WRITERS[o["type"]](o, ptr) for o in self.objects)
        delta = len(payload) - (self.end - self.begin)
        out = bytearray(self.data[:self.begin] + payload + self.data[self.end:])
        if delta:
            self._relocate_directory(out, delta)
        return bytes(out)

    def _relocate_directory(self, out: bytearray, delta: int) -> None:
        """The u32 before the root sheet signature points at the sheet directory, which lists each sheet's
        offset.  Everything after the root sheet's objects moves by delta."""
        dir_ptr_at = self.circuit - 4
        directory = struct.unpack_from("<I", self.data, dir_ptr_at)[0]
        if not self.end < directory < len(self.data):
            raise DsnError(f"unexpected sheet directory pointer {directory:#x}")
        struct.pack_into("<I", out, dir_ptr_at, directory + delta)
        r = Reader(self.data, directory)
        count = r.u16()
        for _ in range(count):
            r.u32()
            r.read(r.u16())
            r.u16()
            at = r.offset
            sheet = r.u32()
            if sheet > self.end:
                struct.pack_into("<I", out, at + delta, sheet + delta)

    def new_key(self) -> int:
        """A wire key no pointer or existing wire uses (keys are only identifiers until build())."""
        used = {o["start"] for o in self.objects if o["type"] == "wire"}
        used |= {p for o in self.objects for p in o.get("pointers", [])}
        k = 0x7F000000
        while k in used:
            k += 1
        return k


def parse(data: bytes) -> Sheet:
    circuit = data.find(SIGNATURE)
    if circuit < 0:
        raise DsnError("no ISIS CIRCUIT FILE section")
    begin = data.find(OBJECTS, circuit)
    if begin < 0:
        raise DsnError("no OBJECT DATA section")
    begin += len(OBJECTS)
    r = Reader(data, begin)
    objects = []
    while data[r.offset] != 0xFF:
        objects.append(read_object(data, r))
        if len(objects) > 100000:
            raise DsnError("too many objects")
    end = r.offset
    second = data.find(SIGNATURE, circuit + 1)
    if second != end + 1:
        raise DsnError(f"object terminator at {end:#x} but next sheet at {second:#x}")
    starts = {o["start"] for o in objects if o["type"] == "wire"}
    bad = [p for o in objects for p in o.get("pointers", []) if p and p not in starts]
    if bad:
        raise DsnError(f"pointers to non-wire offsets: {[hex(p) for p in bad[:5]]}")
    return Sheet(data, circuit, begin, end, objects)
