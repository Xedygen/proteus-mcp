"""Read-only index of the Proteus device libraries (LIBRARY/*.LIB + *.IDX), for finding device names.

LIB: "DEVICE LIBRARY\\x1a\\x00", u32, u32 slots, u32 count (low 16 bits; high bits are flags), then a
directory at offset 32 of 80-byte entries: name[64], u32 offset, u32 time, u32 size, u32 flags. Only the
first `count` entries are in use. Names with a dot (74LS00.IEC, 8039.BUS) are variants and placeable too.
IDX: "DEVICE LIBRARY INDEX\\x1a" + zeros, u32 version at 32, u32 count at 36, then one record per directory
entry in the same order: u32 time, six NUL-terminated strings (category, ?, subcategory, manufacturer, ?,
description), u32. Checked on all 121 pairs installed with Proteus 8.6 (2026-11-16)."""
from __future__ import annotations

import struct
from functools import lru_cache
from pathlib import Path

from . import paths


def _directory(lib: Path) -> list[str]:
    with lib.open("rb") as f:
        head = f.read(32)
        if not head.startswith(b"DEVICE LIBRARY\x1a"):
            return []
        _, slots, count = struct.unpack_from("<III", head, 16)
        count &= 0xFFFF
        if count > slots:
            return []
        data = f.read(80 * count)
    return [data[i * 80:i * 80 + 64].split(b"\0")[0].decode("latin-1") for i in range(count)]


def _records(idx: Path) -> list[list[str]]:
    b = idx.read_bytes()
    if not b.startswith(b"DEVICE LIBRARY INDEX"):
        return []
    (n,) = struct.unpack_from("<I", b, 36)
    at, out = 40, []
    for _ in range(n):
        at += 4
        fields = []
        for _ in range(6):
            end = b.index(b"\0", at)
            fields.append(b[at:end].decode("latin-1"))
            at = end + 1
        at += 4
        out.append(fields)
    return out


@lru_cache(maxsize=1)
def index(folder: str | None = None) -> tuple[dict, ...]:
    root = Path(folder) if folder else paths.PROTEUS_DIR / "LIBRARY"
    out = []
    for lib in sorted(root.glob("*.LIB")):
        try:
            names = _directory(lib)
            recs = _records(lib.with_suffix(".IDX")) if lib.with_suffix(".IDX").exists() else []
        except (OSError, ValueError, struct.error):
            continue
        if recs and len(recs) != len(names):
            recs = []                                    # out of step: keep the names, drop the details
        for i, name in enumerate(names):
            if not name:
                continue
            r = recs[i] if recs else [""] * 6
            out.append({"device": name, "library": lib.stem, "category": r[0], "subcategory": r[2],
                        "manufacturer": r[3], "description": r[5]})
    return tuple(out)


def search(query: str, limit: int = 30, category: str | None = None) -> dict:
    """Every word of `query` must appear in the device name, description, category, subcategory or
    manufacturer (case-insensitive). Exact names first, then name prefixes, then other name matches."""
    words = query.lower().split()
    if not words:
        raise ValueError("give a device name or words from its description, e.g. '8255' or 'decoder'")
    hits = []
    for d in index():
        if category and category.lower() not in d["category"].lower():
            continue
        name = d["device"].lower()
        text = " ".join((name, d["description"], d["category"], d["subcategory"], d["manufacturer"])).lower()
        if not all(w in text for w in words):
            continue
        q = words[0]
        rank = 0 if name == q else 1 if name.startswith(q) else 2 if q in name else 3
        hits.append((rank, "." in name, len(name), name, d))
    hits.sort(key=lambda h: h[:4])
    return {"count": len(hits), "devices": [h[4] for h in hits[:limit]]}
