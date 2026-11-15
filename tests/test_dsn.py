"""ROOT.DSN parser and writer, checked against every lab sample (skipped when labs/ is absent)."""
import zipfile
from pathlib import Path

import pytest

from proteus_mcp import dsn

REPO = Path(__file__).resolve().parents[1]
SAMPLES = sorted((REPO / "labs").glob("**/*.pdsprj"))


def _dsn(path):
    with zipfile.ZipFile(path) as z:
        return z.read("ROOT.DSN")


@pytest.mark.skipif(not SAMPLES, reason="labs/ not present")
@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.parent.name + "/" + p.stem)
def test_round_trip(path):
    data = _dsn(path)
    sheet = dsn.parse(data)
    assert sheet.components(), "no components found"
    assert sheet.build() == data


@pytest.mark.skipif(not SAMPLES, reason="labs/ not present")
@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.parent.name + "/" + p.stem)
def test_resize_relocates(path):
    """Grow one wire by a point: every later pointer and the sheet directory must follow."""
    data = _dsn(path)
    sheet = dsn.parse(data)
    wire = sheet.wires()[0]
    wire["points"].append(list(wire["points"][-1]))
    out = sheet.build()
    assert len(out) == len(data) + 8
    again = dsn.parse(out)            # pointers and the end-of-sheet check still hold
    assert len(again.objects) == len(sheet.objects)
    import struct
    directory = struct.unpack_from("<I", out, again.circuit - 4)[0]
    second = out.find(dsn.SIGNATURE, again.circuit + 1)
    assert out[directory:directory + 2] == data[struct.unpack_from("<I", data, sheet.circuit - 4)[0]:][:2]
    assert struct.pack("<I", second) in out[directory:directory + 64]


LAB5 = REPO / "labs" / "2025" / "lab5" / "taslak.pdsprj"


@pytest.mark.skipif(not LAB5.exists(), reason="labs/ not present")
def test_connect_and_pin_geometry(tmp_path):
    from proteus_mcp import devices, pdsprj, schematic
    proj = pdsprj.copy(LAB5, tmp_path / "t.pdsprj")
    schematic.connect(proj, "U2.Q1", "U3.A0")
    sheet = schematic.load(proj)
    _, q1 = devices.find_pin(sheet, "U2", "Q1")
    _, a0 = devices.find_pin(sheet, "U3", "A0")
    assert q1["connected"] and a0["connected"]
    wire = next(w for w in sheet.wires() if {tuple(w["points"][0]), tuple(w["points"][-1])}
                == {tuple(q1["xy"]), tuple(a0["xy"])})
    assert all(a[0] == b[0] or a[1] == b[1] for a, b in zip(wire["points"], wire["points"][1:]))
    with pytest.raises(dsn.DsnError):
        schematic.connect(proj, "U2.Q1", "U3.A1")   # Q1 already has a wire


@pytest.mark.skipif(not SAMPLES, reason="labs/ not present")
def test_every_connected_pin_meets_its_wire():
    from proteus_mcp import devices
    for path in SAMPLES:
        sheet = dsn.parse(_dsn(path))
        wires = {w["start"]: w for w in sheet.wires()}
        for comp in sheet.components():
            for pin, ptr in zip(devices.pins(sheet, comp), comp["pointers"]):
                if ptr:
                    w = wires[ptr]
                    assert pin["xy"] in (w["points"][0], w["points"][-1]), (path.name, sheet.name_of(comp), pin["name"])


@pytest.mark.skipif(not LAB5.exists(), reason="labs/ not present")
def test_terminals_face_outwards_and_stay_clear(tmp_path):
    from proteus_mcp import devices, pdsprj, schematic
    proj = pdsprj.copy(LAB5, tmp_path / "t.pdsprj")
    r = schematic.add_terminal(proj, "U3.RESET", "GND")       # left-side pin: terminal to the left
    sheet = schematic.load(proj)
    _, reset = devices.find_pin(sheet, "U3", "RESET")
    assert reset["connected"] and r["terminal_grid"][0] < reset["xy"][0] / dsn.GRID
    r = schematic.add_terminal(proj, "U3.PC0", "NETX")        # right-side pin: terminal to the right
    assert r["terminal_grid"][0] > 27
    names = [o["label"]["text"] for o in schematic.load(proj).objects if o["type"] == "terminal"]
    assert "NETX" in names


@pytest.mark.skipif(not LAB5.exists(), reason="labs/ not present")
def test_new_wire_starts_at_first_serialised_end(tmp_path):
    """ISIS binds points[0] to the object written first; argument order must not matter."""
    from proteus_mcp import devices, pdsprj, schematic
    proj = pdsprj.copy(LAB5, tmp_path / "t.pdsprj")
    schematic.connect(proj, "U3.A0", "U2.Q1")               # U2 is written before U3
    sheet = schematic.load(proj)
    _, q1 = devices.find_pin(sheet, "U2", "Q1")
    wire = next(w for w in sheet.wires() if q1["xy"] in (w["points"][0], w["points"][-1]))
    assert wire["points"][0] == q1["xy"]
    with pytest.raises(dsn.DsnError):
        schematic.connect(proj, "U2.VCC", "U3.PC7")          # hidden power pin


@pytest.mark.skipif(not LAB5.exists(), reason="labs/ not present")
def test_add_part_copies(tmp_path):
    from proteus_mcp import pdsprj, schematic
    proj = pdsprj.copy(LAB5, tmp_path / "t.pdsprj")
    r = schematic.add_part(proj, "RES#0010", 30, 20, value="220")
    assert r["part"] == "R1"
    names = {c["ref"]: c for c in schematic.components(proj)}
    assert names["R1"]["value"] == "220" and not any(p["connected"] for p in names["R1"]["pins"])
    assert schematic.add_part(proj, "U2", 0, 40)["part"] == "U4"   # annotated source: same prefix, next number
    with pytest.raises(dsn.DsnError):
        schematic.add_part(proj, "RES#0010", 30, 20)          # pins would land on R1's pins


LAB8 = REPO / "labs" / "2025" / "lab8" / "taslak.pdsprj"


@pytest.mark.skipif(not LAB8.exists(), reason="labs/ not present")
def test_bus_terminal_on_bus_pin(tmp_path):
    from proteus_mcp import pdsprj, schematic
    proj = pdsprj.copy(LAB8, tmp_path / "t.pdsprj")
    r = schematic.add_terminal(proj, "U5.D[0..7]", "AD[0..7]")
    assert r["terminal"] == "$TERBUS"
    sheet = schematic.load(proj)
    term = next(o for o in sheet.objects if o["type"] == "terminal" and o["label"]["text"] == "AD[0..7]"
                and o["origin"][0] == round(r["terminal_grid"][0] * dsn.GRID) - dsn.GRID)
    wire = next(w for w in sheet.wires() if w["start"] == term["pointers"][0])
    assert wire["flags"] == 1                                   # bus wire
    with pytest.raises(dsn.DsnError):
        schematic.add_terminal(proj, "U9.D[0..7]", "DATA")     # bus pin needs a bus name


LAB7 = REPO / "labs" / "2025" / "lab7" / "taslak.pdsprj"


@pytest.mark.skipif(not LAB7.exists(), reason="labs/ not present")
def test_labels_find_placeholders():
    from proteus_mcp import schematic
    names = schematic.labels(LAB7)
    assert {"YA1", "YA6", "AD0"} <= set(names)
    assert names["$IO1$"]["pins"] == ["U5.1"]
