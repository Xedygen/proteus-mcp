"""Exploratory object-section walker: reports the first unknown record per file."""
import sys, glob, os, struct
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from proteus_mcp.dsn import Reader, DsnError, SIGNATURE, OBJECTS

def component(r):
    o = {"type": "component", "start": r.offset, "kind": r.u8()}
    o["labels"] = [r.text() for _ in range(4)]
    o["props"] = r.read(r.u32())
    pins = r.u16(); o["device"] = r.pstr(); o["origin"] = r.xy()
    o["transform"] = r.read(4); o["identity"] = r.u32(); o["pin_header"] = r.read(9)
    o["pointers"] = [r.u32() for _ in range(pins)]
    return o
def wire(r):
    o = {"type": "wire", "start": r.offset, "flags": r.u8()}
    n = r.u32(); o["style"] = r.read(n - 4)
    o["labels"] = [wtext(r) for _ in range(r.u16())]
    o["points"] = [r.xy() for _ in range(r.u16())]
    return o
def wtext(r):
    assert r.u8() == 0xFF
    t = {"text": r.pstr(), "xy": r.xy(), "transform": r.read(6)}
    n = r.u32(); t["style"] = r.read(n - 4)
    return t
def junction(r):
    o = {"type": "junction", "start": r.offset}; r.u8()
    o["origin"] = r.xy(); o["pointers"] = [r.u32() for _ in range(4)]
    return o
def terminal(r):
    o = {"type": "terminal", "start": r.offset}; r.u8()
    o["origin"] = r.xy(); o["transform"] = r.read(4); o["device"] = r.pstr()
    o["attributes"] = r.read(6); o["label"] = r.text(); o["pointers"] = [r.u32()]
    return o

def walk(data, verbose=False):
    c = data.find(SIGNATURE); b = data.find(OBJECTS, c) + len(OBJECTS)
    r = Reader(data, b); objs = []; seen = set(); pending = []
    while data[r.offset] != 0xFF:
        at = r.offset
        try:
            if pending:
                exp = pending.pop(0)
                if at != exp: raise DsnError(f"wire order mismatch at {at:#x} expected {exp:#x}")
                o = wire(r)
            else:
                k = data[at]
                if k in (0, 8) and data[at+1] == 0xFF: o = component(r)
                elif k == 1: o = junction(r)
                elif k == 16: o = terminal(r)
                else: raise DsnError(f"unknown kind {k}")
        except Exception as e:
            return objs, f"{e} at {at:#x}", at
        objs.append(o)
        for p in o.get("pointers", []):
            if p:
                if p in seen: pending.append(p)
                else: seen.add(p)
    return objs, None, r.offset

if __name__ == "__main__":
    base = sys.argv[1]
    for d in sorted(glob.glob(os.path.join(base, "*"))):
        data = open(os.path.join(d, "ROOT.DSN"), "rb").read()
        objs, err, at = walk(data)
        kinds = {}
        for o in objs: kinds[o["type"]] = kinds.get(o["type"], 0) + 1
        print(os.path.basename(d), kinds, "OK" if not err else err)
        if err and len(sys.argv) > 2:
            last = objs[-1]; print("    last:", last["type"], hex(last["start"]), last.get("device"), [hex(x) for x in last.get("pointers", [])][:8], "pending-next")
            print("   ", data[at:at+96].hex(" "))
            print("   ", bytes(c if 32 <= c < 127 else 46 for c in data[at:at+96]).decode())
