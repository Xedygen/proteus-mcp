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
