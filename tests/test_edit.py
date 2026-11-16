"""Offline edits (tap, delete_wire, move with wires) keep the file consistent: it parses, every wire end sits
on the pin or junction that refers to it, and points[0] belongs to the first holder in the file."""
import shutil
from pathlib import Path

import pytest

from proteus_mcp import dsn, route, schematic
from proteus_mcp.devices import pins

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "labs" / "2025" / "lab5" / "taslak.pdsprj"
pytestmark = pytest.mark.skipif(not TEMPLATE.exists(), reason="labs/ not present")


@pytest.fixture
def proj(tmp_path):
    p = tmp_path / "t.pdsprj"
    shutil.copy(TEMPLATE, p)
    return p


def consistent(project):
    sheet = schematic.load(project)
    wires = {w["start"]: w for w in sheet.wires()}
    holders = schematic._holders(sheet)
    for key, hs in holders.items():
        w = wires[key]
        for i, (o, slot) in enumerate(hs):
            end = w["points"][0] if i == 0 else w["points"][-1]
            if o["type"] == "component":
                xy = pins(sheet, o)[slot]["xy"]
                assert xy == end, f"{sheet.name_of(o)} slot {slot}: pin {xy} vs wire end {end}"
            elif o["type"] == "junction":
                assert o["origin"] == end, f"junction {o['origin']} vs wire end {end}"
        assert len(hs) <= 2
    for w in sheet.wires():
        for a, b in zip(w["points"], w["points"][1:]):
            assert a[0] == b[0] or a[1] == b[1], f"diagonal segment {a} {b}"
    return sheet


def wired_and_free(project):
    sheet = schematic.load(project)
    wired, free = [], []
    for c in sheet.components():
        try:
            ps = pins(sheet, c)
        except dsn.DsnError:
            continue
        for p in ps:
            if p["hidden"] or p["bus"]:
                continue
            (wired if p["connected"] else free).append(f"{sheet.name_of(c)}.{p['name']}")
    return wired, free


def test_template_is_consistent(proj):
    consistent(proj)


def test_tap_then_delete(proj):
    wired, free = wired_and_free(proj)
    done = None
    for f in free:
        for w in wired:
            if w.split(".")[0] == f.split(".")[0]:
                continue
            try:
                done = schematic.tap(proj, f, w)
                break
            except dsn.DsnError:
                continue
        if done:
            break
    assert done, "no tap possible on the template"
    n_before = len(schematic.load(TEMPLATE).wires())
    sheet = consistent(proj)
    j = [o for o in sheet.objects if o["type"] == "junction" and o["origin"] == [round(v * schematic.GRID) for v in done["junction_grid"]]]
    assert j and sum(1 for k in j[0]["pointers"] if k) >= 3
    r = schematic.delete_wire(proj, pin=done["pin"])
    sheet = consistent(proj)
    assert len(sheet.wires()) == n_before             # the tap wire gone, split halves joined again


def test_connect_falls_back_to_tap(proj):
    wired, free = wired_and_free(proj)
    for f in free:
        try:
            r = schematic.connect(proj, f, wired[0])
        except dsn.DsnError:
            continue
        assert "junction_grid" in r
        consistent(proj)
        return
    pytest.skip("no free pin could reach the net")


def test_move_wired_part(proj):
    sheet = schematic.load(proj)
    comp = min((c for c in sheet.components() if any(c["pointers"])), key=lambda c: len(c["pointers"]))
    ref = sheet.name_of(comp)
    gx, gy = comp["origin"][0] / schematic.GRID, comp["origin"][1] / schematic.GRID
    for dx, dy in ((0, 3), (0, -3), (3, 0), (-3, 0), (2, 2)):
        try:
            r = schematic.move_part(proj, ref, gx + dx, gy + dy)
            break
        except dsn.DsnError:
            continue
    else:
        pytest.skip("no free spot near the resistor")
    assert r["wires_stretched"]
    consistent(proj)


def test_move_keeps_wire_direction(proj):
    """Moving a part whose pin is the wire's last point must not reverse the wire (points[0] stays with the
    first holder) and the wire must reach the pin from outside the part."""
    schematic.connect(proj, "RES#0010.1", "U3.PB0")
    consistent(proj)
    schematic.move_part(proj, "RES#0010", 48, 9)
    sheet = consistent(proj)
    comp = sheet.component("RES#0010")
    p = pins(sheet, comp)[0]
    w = next(w for w in sheet.wires() if w["start"] == comp["pointers"][0])
    a, b = (w["points"][-2], w["points"][-1]) if w["points"][-1] == p["xy"] else (w["points"][1], w["points"][0])
    sgn = lambda v: (v > 0) - (v < 0)
    assert (sgn(b[0] - a[0]), sgn(b[1] - a[1])) == (-p["out"][0], -p["out"][1])
    sheet.objects.remove(w)
    assert route.path_clear(route.Obstacles(sheet, {tuple(w["points"][0]), tuple(w["points"][-1])}), w["points"])


def test_delete_part_tidies_junctions(proj):
    """Deleting a part whose wire ends on a junction merges the junction away when two wires remain."""
    schematic.connect(proj, "U2.Q2", "U3.PA0")
    r = schematic.tap(proj, "RES#0010.1", "U3.PA0")
    n_j = sum(1 for o in schematic.load(proj).objects if o["type"] == "junction")
    schematic.delete_part(proj, "RES#0010")
    sheet = consistent(proj)
    assert sum(1 for o in sheet.objects if o["type"] == "junction") == n_j - 1
    for o in sheet.objects:
        if o["type"] == "junction":
            ks = o["pointers"]
            assert ks == [k for k in ks if k] + [0] * ks.count(0)


def test_wire_label(proj):
    schematic.connect(proj, "U2.Q2", "U3.PA0")
    r = schematic.add_wire_label(proj, "U3.PA0", "SIG7")
    sheet = consistent(proj)
    w = next(w for w in sheet.wires() if w["labels"] and w["labels"][0]["text"] == "SIG7")
    lab = w["labels"][0]
    assert lab["style"] == schematic._WIRE_LABEL_STYLE
    x, y = round(r["at_grid"][0] * schematic.GRID), round(r["at_grid"][1] * schematic.GRID)
    assert any(schematic._on_segment((x, y), a, b) for a, b in zip(w["points"], w["points"][1:]))
    with pytest.raises(dsn.DsnError):
        schematic.add_wire_label(proj, "U3.PA0", "OTHER")      # one label per wire
    with pytest.raises(dsn.DsnError):
        schematic.add_wire_label(proj, "U2.Q2", "AD[0..7]")   # bus name on a plain wire


def test_generators_report_their_pins(proj):
    r = schematic.add_generator(proj, "U3.PA0", 1000, name="CK1", distance_grid=10)
    consistent(proj)
    g = next(g for g in schematic.generators(proj) if g["name"] == "CK1")
    assert g["pins"] == ["U3.PA0"] and g["clock"] == "1000"
    schematic.tap(proj, "U2.Q2", "U3.PA0")              # a second pin on the generator's net
    g = next(g for g in schematic.generators(proj) if g["name"] == "CK1")
    assert sorted(g["pins"]) == ["U2.Q2", "U3.PA0"]


def test_delete_wire_by_point(proj):
    sheet = schematic.load(proj)
    holders = schematic._holders(sheet)
    w = next(w for w in sheet.wires() if len(w["points"]) >= 2 and all(o["type"] == "component" for o, _ in holders[w["start"]]))
    a, b = w["points"][0], w["points"][1]
    mid = [(a[0] + b[0]) / 2 / schematic.GRID, (a[1] + b[1]) / 2 / schematic.GRID]
    schematic.delete_wire(proj, at_grid=mid)
    sheet = consistent(proj)
    assert all(x["start"] != w["start"] for x in sheet.wires()) or len(sheet.wires()) < len(holders)
