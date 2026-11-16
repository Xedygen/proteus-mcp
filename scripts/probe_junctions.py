"""Junction layout in the samples: pointer order, wire ends at the junction, which object refers first."""
import sys
import zipfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from proteus_mcp import dsn  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
stats = Counter()
shown = 0
for path in sorted((REPO / "labs").glob("**/*.pdsprj")):
    with zipfile.ZipFile(path) as z:
        sheet = dsn.parse(z.read("ROOT.DSN"))
    wires = {o["start"]: o for o in sheet.wires()}
    order = {id(o): i for i, o in enumerate(sheet.objects)}
    refs = {}
    for o in sheet.objects:
        for slot, p in enumerate(o.get("pointers", [])):
            if p:
                refs.setdefault(p, []).append((o, slot))
    for j in (o for o in sheet.objects if o["type"] == "junction"):
        ptrs = j["pointers"]
        n = sum(1 for p in ptrs if p)
        stats[f"junction with {n} wires"] += 1
        stats["zeros at end" if all(p == 0 for p in ptrs[n:]) else "zeros inside"] += 1
        for p in ptrs:
            if not p:
                continue
            w = wires[p]
            at_start = w["points"][0] == j["origin"]
            at_end = w["points"][-1] == j["origin"]
            holders = refs[p]
            other = [h for h in holders if h[0] is not j]
            if not other:
                stats["wire with only the junction"] += 1
                continue
            jfirst = order[id(j)] < order[id(other[0][0])]
            # rule: points[0] belongs to whichever holder comes first in the file
            ok = (at_start and jfirst) or (at_end and not jfirst)
            stats["first-holder rule holds" if ok else "rule BROKEN"] += 1
            stats[f"other end is {other[0][0]['type']}"] += 1
            if not (at_start or at_end):
                stats["junction not at a wire end"] += 1
        if shown < 3:
            print(path.parent.name, j["origin"], [hex(p) for p in ptrs],
                  [(wires[p]["points"][0] == j["origin"], len(wires[p]["points"])) for p in ptrs if p])
            shown += 1
for k, v in sorted(stats.items()):
    print(f"{v:6} {k}")
