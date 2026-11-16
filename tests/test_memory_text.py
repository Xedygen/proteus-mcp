"""Parsing a memory popup's 'Copy All' text and the address forms debug_memory accepts."""
import pytest

from proteus_mcp import gui, server

# as copied from 'Memory Contents - RAM00' in Proteus 8.6 (odev2, 2026-11-16), rows shortened
TEXT = ("0000: 34 00 00 00 | 4...\r\n"
        "0004: CD CD | ..\r\n")


def test_parse_memory_text():
    assert gui.Session.parse_memory_text(TEXT) == {0: 0x34, 1: 0, 2: 0, 3: 0, 4: 0xCD, 5: 0xCD}


def test_parse_refuses_other_formats():
    with pytest.raises(gui.GuiError):
        gui.Session.parse_memory_text("0000: 0034 0000 | 4...\r\n")


@pytest.mark.parametrize("a,want", [(16, 16), ("0x6000", 0x6000), ("6000H", 0x6000), ("60000", 0x60000),
                                    ("8A", 0x8A)])
def test_address_forms(a, want):
    assert server._address(a) == want
