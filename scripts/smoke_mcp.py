"""Drive the server through a real MCP stdio client.

    python -X utf8 scripts/smoke_mcp.py            list tools, call proteus_doctor
    python -X utf8 scripts/smoke_mcp.py <.pdsprj>  also: open, rebuild firmware, launch, run, check, close
"""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO = Path(__file__).resolve().parents[1]
PY = REPO / ".venv" / "Scripts" / "python.exe"


def show(name, res):
    for c in res.content:
        if c.type == "text":
            t = c.text
            print(f"--- {name}: {t[:1500]}{' ...' if len(t) > 1500 else ''}")
        elif c.type == "image":
            out = REPO / "work" / f"smoke_{name}.png"
            import base64
            out.write_bytes(base64.b64decode(c.data))
            print(f"--- {name}: image saved to {out}")
    if res.is_error:
        print(f"!!! {name} returned an error")


async def main():
    params = StdioServerParameters(command=str(PY), args=["-X", "utf8", "-m", "proteus_mcp", "serve"], cwd=str(REPO))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print("tools:", [t.name for t in tools.tools])
            show("proteus_doctor", await s.call_tool("proteus_doctor", {}))
            if len(sys.argv) < 2:
                return
            call = lambda n, a={}: s.call_tool(n, a)
            show("project_open", await call("project_open", {"path": sys.argv[1], "name": "smoke", "overwrite": True}))
            show("firmware_build", await call("firmware_build", {"project": "smoke"}))
            show("proteus_launch", await call("proteus_launch", {"project": "smoke"}))
            try:
                show("net_of", await call("net_of", {"ref": "U3", "pin": "PA0"}))
                show("sim_start", await call("sim_start", {"wait_seconds": 4}))
                show("sim_pause", await call("sim_pause"))
                show("debug_window", await call("debug_window", {"name": "8255 Internal Status"}))
                show("screenshot", await call("screenshot"))
                show("sim_stop", await call("sim_stop"))
                show("net_of_after", await call("net_of", {"ref": "U3", "pin": "PB0"}))
            finally:
                show("proteus_close", await call("proteus_close"))

asyncio.run(main())
