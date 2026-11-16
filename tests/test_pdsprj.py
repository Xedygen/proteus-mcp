"""The firmware project of the 2024 lab 5 template lists no files; register_firmware fills it in the way
Proteus writes it in the lab's solution, and leaves complete firmware projects alone."""
import re
import zipfile
from pathlib import Path

import pytest

from proteus_mcp import pdsprj

LAB5 = Path(__file__).resolve().parents[1] / "labs" / "2024" / "lab 5"
STUB = LAB5 / "proteus_sablon.pdsprj"
SOLVED = LAB5 / "yusuf_safa_koksal" / "proteus.pdsprj"
pytestmark = pytest.mark.skipif(not (STUB.exists() and SOLVED.exists()), reason="labs/ not present")


def _files(p):
    z = zipfile.ZipFile(p)
    return {n: z.read(n) for n in z.namelist()}


def _norm(xml: bytes) -> str:
    return re.sub(r'IID="\d+" BUILDPATH="[^"]*"', "", xml.decode()).replace("\r\n", "\n").strip()


def test_stub_firmware_project_is_registered_like_the_solution():
    files = _files(STUB)
    changes = pdsprj.register_firmware(files)
    assert list(changes) == ["FIRMWARE/8086.XML"]
    assert _norm(changes["FIRMWARE/8086.XML"]) == _norm(_files(SOLVED)["FIRMWARE/8086.XML"])
    assert pdsprj.register_firmware({**files, **changes}) == {}


def test_complete_firmware_project_is_left_alone():
    assert pdsprj.register_firmware(_files(SOLVED)) == {}
