"""Part records in ROOT.CDB.

For annotated parts (U1, U2 ...) Proteus takes properties from ROOT.CDB, not from ROOT.DSN: a property
changed only in the DSN is ignored (seen 2026-11-15 with the 8086 CLOCK).  A part record is

    u8 len + reference, u8 len + device, u8 len + value, u8 len + package, u32 n + property text

Unannotated parts (RES#0010 ...) have no record here.
"""
from __future__ import annotations

import struct


class CdbError(Exception):
    pass


def _find(cdb: bytes, ref: str, device: str) -> list[int]:
    key = bytes([len(ref)]) + ref.encode("latin-1") + bytes([len(device)]) + device.encode("latin-1")
    hits, start = [], 0
    while True:
        i = cdb.find(key, start)
        if i < 0:
            return hits
        hits.append(i)
        start = i + 1


def read_part(cdb: bytes, ref: str, device: str) -> dict | None:
    hits = _find(cdb, ref, device)
    if not hits:
        return None
    if len(hits) > 1:
        raise CdbError(f"{ref} appears {len(hits)} times in ROOT.CDB")
    pos = hits[0]
    fields = []
    for _ in range(4):
        n = cdb[pos]
        fields.append(cdb[pos + 1:pos + 1 + n].decode("latin-1"))
        pos += 1 + n
    n = struct.unpack_from("<I", cdb, pos)[0]
    if n > len(cdb) - pos:
        raise CdbError(f"implausible property length for {ref}")
    return {"start": hits[0], "props_at": pos, "end": pos + 4 + n, "ref": fields[0], "device": fields[1],
            "value": fields[2], "package": fields[3], "props": cdb[pos + 4:pos + 4 + n].decode("latin-1")}
