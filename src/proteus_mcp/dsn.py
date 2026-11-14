"""Reader/writer for the Proteus 8.x ISIS schematic file (ROOT.DSN).

The layout was worked out from the Proteus 8.6 lab projects (FILEVER 814).  It is
close to the Proteus 9 layout documented by chen0416ccc-cpu/Proteus-MCP (MIT),
with one-byte string lengths and a separate property block per component.

Coordinates are in units of 10 nm, y grows downwards on screen is NOT assumed
here: values are stored exactly as found.  One 0.1 inch grid step is 254000.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

GRID = 254000
SIGNATURE = b"ISIS CIRCUIT FILE\x1a"
OBJECTS = b"OBJECT DATA\x00"


class DsnError(Exception):
    pass


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
        style = self.read(style_len - 4)
        return {"text": value, "xy": xy, "transform": transform, "style": style}


def pack_text(t: dict) -> bytes:
    raw = t["text"].encode("latin-1")
    if len(raw) > 255:
        raise DsnError("text longer than 255 bytes")
    return (b"\xff" + bytes([len(raw)]) + raw + struct.pack("<ii", *t["xy"])
            + t["transform"] + struct.pack("<I", len(t["style"]) + 4) + t["style"])
