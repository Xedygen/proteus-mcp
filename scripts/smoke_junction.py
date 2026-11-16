"""Tap a wire with a junction, move a wired part, delete a wire and label two nets alike through the MCP
server, then confirm the
result in Proteus's own netlist (and that Proteus opens the file without a dialog).

    python -X utf8 scripts/smoke_junction.py labs/2025/lab5/taslak.pdsprj [screenshot.png]
"""
import asyncio
import base64
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO = Path(__file__).resolve().parents[1]
P = "junction_smoke"


def text(res):
    return "".join(c.text for c in res.content if c.type == "text")


async def main():
    params = StdioServerParameters(command=str(REPO / ".venv" / "Scripts" / "python.exe"),
                                   args=["-X", "utf8", "-m", "proteus_mcp", "serve"], cwd=str(REPO))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            async def call(n, a=None):
                res = await s.call_tool(n, a or {})
                print(f"{n} {a or ''} -> {'ERROR ' if res.is_error else ''}{text(res)[:300]}")
                return res

            await call("project_open", {"path": sys.argv[1], "name": P, "overwrite": True})
            await call("schematic_connect", {"project": P, "a": "U2.Q1", "b": "U3.A0"})
            await call("schematic_tap", {"project": P, "pin": "U3.A1", "target": "U2.Q1"})
            await call("schematic_connect", {"project": P, "a": "U2.Q2", "b": "U3.PA0"})
            await call("schematic_connect", {"project": P, "a": "U2.Q3", "b": "U3.PA0"})  # falls back to a tap
            await call("schematic_delete_wire", {"project": P, "pin": "U2.Q3"})            # tap gone, wire merged
            await call("schematic_connect", {"project": P, "a": "RES#0010.1", "b": "U3.PB0"})
            comp = (await s.call_tool("schematic_components", {"project": P, "ref": "RES#0010"})).structured_content["result"][0]
            await call("schematic_move", {"project": P, "ref": "RES#0010", "x_grid": comp["grid"][0] + 2, "y_grid": comp["grid"][1] + 3})
            # same label on two separate nets: Proteus must report them as one net
            await call("schematic_add_wire_label", {"project": P, "target": "U2.Q1", "name": "SMOKE"})
            await call("schematic_add_wire_label", {"project": P, "target": "U3.PB0", "name": "SMOKE"})
            await call("schematic_check", {"project": P})
            await call("proteus_launch", {"project": P})
            try:
                for ref, pin in (("U3", "A1"), ("U3", "PA0"), ("U2", "Q3"), ("U3", "PB0")):
                    await call("net_of", {"ref": ref, "pin": pin})
                if len(sys.argv) > 2:
                    res = await s.call_tool("screenshot", {})
                    img = next(c for c in res.content if c.type == "image")
                    Path(sys.argv[2]).write_bytes(base64.b64decode(img.data))
            finally:
                await call("proteus_close")

asyncio.run(main())
