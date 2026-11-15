"""Edit a lab template through the MCP server and confirm the change in Proteus's own netlist.

    python -X utf8 scripts/smoke_edit.py labs/2025/lab5/taslak.pdsprj
"""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO = Path(__file__).resolve().parents[1]


def text(res):
    return "".join(c.text for c in res.content if c.type == "text")


async def main():
    params = StdioServerParameters(command=str(REPO / ".venv" / "Scripts" / "python.exe"),
                                   args=["-X", "utf8", "-m", "proteus_mcp", "serve"], cwd=str(REPO))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            call = lambda n, a={}: s.call_tool(n, a)
            print(text(await call("project_open", {"path": sys.argv[1], "name": "edit_smoke", "overwrite": True}))[:200])
            part = (await call("schematic_components", {"project": "edit_smoke", "ref": "U3"})).structured_content["result"]
            print("U3 pins:", [(p["name"], p["connected"]) for p in part[0]["pins"]][:14])
            for a, b in (("U2.Q1", "U3.A0"), ("U2.Q2", "U3.A1")):
                res = await call("schematic_connect", {"project": "edit_smoke", "a": a, "b": b})
                print("connect", a, b, "->", text(res)[:160], "ERROR" if res.is_error else "")
            print(text(await call("proteus_launch", {"project": "edit_smoke"}))[:200])
            try:
                for ref, pin in (("U3", "A0"), ("U3", "A1")):
                    print(ref, pin, text(await call("net_of", {"ref": ref, "pin": pin})))
            finally:
                print(text(await call("proteus_close")))

asyncio.run(main())
