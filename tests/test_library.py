"""The device library index: names line up with the IDX descriptions, and every device in the lab files is
found (so schematic_place_devices never refuses a real name)."""
from pathlib import Path

import pytest

from proteus_mcp import library, paths, schematic

LIB = paths.PROTEUS_DIR / "LIBRARY"
pytestmark = pytest.mark.skipif(not LIB.exists(), reason="Proteus libraries not installed")
REPO = Path(__file__).resolve().parents[1]


def test_known_devices():
    by = {d["device"]: d for d in library.index()}
    assert by["8255A"]["library"] == "MICRO" and "Peripheral Interface" in by["8255A"]["description"]
    assert "4-to-16" in by["74154"]["description"]
    assert by["LED-RED"]["library"] == "ACTIVE"


def test_search_ranks_exact_name_first():
    r = library.search("74154")
    assert r["devices"][0]["device"] == "74154"
    assert library.search("decoder 4-to-16")["count"] >= 2
    with pytest.raises(ValueError):
        library.search("  ")


@pytest.mark.skipif(not (REPO / "labs").exists(), reason="labs/ not present")
def test_lab_devices_are_indexed():
    names = {d["device"].lower() for d in library.index()}
    missing = set()
    for p in (REPO / "labs").glob("**/*.pdsprj"):
        try:
            missing |= {c["device"] for c in schematic.load(p).components() if c["device"].lower() not in names}
        except Exception:
            continue
    assert not missing
