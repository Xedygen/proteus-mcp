"""Worked example of a circuit that is not in any template: an 8255 driving eight LEDs, added through the MCP
tools to a board that keeps only the 8086, its address latches and a 74154.

    python -X utf8 scripts/scratch_example.py

Starts from the 2025 lab6 template, deletes its 8251 (annotated, so Proteus deletes it) and its two virtual
terminals, places an 8255A and a LED-RED from Proteus's libraries (real mouse: background mode is switched
off for that step), copies the LED seven times and wires everything, all inside the sheet border.

Decoder (isolated I/O, partial decoding):
    74154 A..D = A4..A7, E1 = M/IO (low in I/O cycles), E2 = GND, output 2 -> 8255 /CS: ports 20H..2FH
    8255 A0 = A1, A1 = A2 (even addresses, low data byte): PA 20H, PB 22H, PC 24H, control 26H
The program sets port A as output and writes 0A5H: LEDs PA0, PA2, PA5 and PA7 light.
"""
import asyncio
import base64
import json
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO = Path(__file__).resolve().parents[1]
P = "scratch_example"
PATTERN = 0xA5

SOURCE = r"""
; 8255 at 20H (PA), 22H (PB), 24H (PC), 26H (control); eight LEDs on port A
PORTA   EQU 20H
CTRL    EQU 26H
CODE    SEGMENT PUBLIC 'CODE'
        ASSUME CS:CODE
START:  MOV AL, 80H         ; mode set: all ports mode 0, outputs
        OUT CTRL, AL
        MOV AL, 0A5H        ; 1010 0101
        OUT PORTA, AL
HOLD:   JMP HOLD
CODE    ENDS
        END START
"""


async def main():
    params = StdioServerParameters(command=str(REPO / ".venv" / "Scripts" / "python.exe"),
                                   args=["-X", "utf8", "-m", "proteus_mcp", "serve"], cwd=str(REPO))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            async def call(tool, **args):
                res = await s.call_tool(tool, args)
                if res.is_error:
                    raise RuntimeError(f"{tool}{args}: {res.content[0].text}")
                return res.structured_content or (json.loads(res.content[0].text) if res.content else {})

            await call("project_open", path="labs/2025/lab6/taslak.pdsprj", name=P, overwrite=True)
            for ref in ("U7", "$IVIRTUAL TERMINAL#0002", "$IVIRTUAL TERMINAL#000D"):
                print("delete", ref, await call("schematic_delete", project=P, ref=ref))
            was = (await call("proteus_background"))["background"]
            await call("proteus_background", on=False)
            try:
                placed = (await call("schematic_place_devices", project=P, devices=[
                    {"device": "8255A", "x_grid": 8, "y_grid": -6},
                    {"device": "LED-RED", "x_grid": 47, "y_grid": -22}]))["result"]
            finally:
                await call("proteus_background", on=was)
            ppi = next(c["ref"] for c in placed if c["device"] == "8255A")
            led0 = next(c["ref"] for c in placed if c["device"] == "LED-RED")
            print("placed", ppi, led0)
            # eight LEDs in a row below and right of port A; PA0 takes the rightmost so no wires cross
            leds = [led0] + [(await call("schematic_add_part", project=P, like=led0, x_grid=47 - 3 * k,
                                         y_grid=-22))["part"] for k in range(1, 8)]
            T = lambda pin, net: call("schematic_add_terminal", project=P, pin=pin, net=net)
            for k, led in enumerate(leds):
                await call("schematic_connect", project=P, a=f"{ppi}.PA{k}", b=f"{led}.A")
                await T(f"{led}.K", "GND")
            for k in range(8):
                await T(f"{ppi}.D{k}", f"AD{k}")
            for pin, net in (("$RD$", "/RD"), ("$WR$", "/WR"), ("A0", "A1"), ("A1", "A2"), ("RESET", "GND"),
                             ("$CS$", "CS8255")):
                await T(f"{ppi}.{pin}", net)
            for pin, net in zip("ABCD", ["A4", "A5", "A6", "A7"]):
                await T(f"U6.{pin}", net)
            await T("U6.E1", "M/IO")
            await T("U6.E2", "GND")
            await T("U6.2", "CS8255")
            print("check:", await call("schematic_check", project=P))
            build = await call("firmware_build", project=P, source=SOURCE)
            print("firmware:", {k: build[k] for k in ("ok", "errors", "stored") if k in build})

            await call("proteus_launch", project=P)
            try:
                checks = {}
                cs = await call("net_of", ref=ppi, pin="CS")
                checks["8255 CS <- 74154 Y2"] = "U6.2" in cs["members"]
                for k, led in enumerate(leds):
                    net = await call("net_of", ref=led, pin="A")
                    checks[f"{led}.A on PA{k}"] = f"{ppi}.PA{k}" in net["members"]
                sim = await call("sim_start", wait_seconds=3)
                checks["firmware loaded"] = sim["firmware"].get("matches_project_exe")
                await call("sim_pause")
                shot = await s.call_tool("screenshot_part", {"ref": leds[3], "margin_grid": 14})
                img = next(c for c in shot.content if c.type == "image")
                out = REPO / "work" / "scratch_leds.png"
                out.write_bytes(base64.b64decode(img.data))
                print(f"expected lit: {[f'PA{k}' for k in range(8) if PATTERN >> k & 1]} - see {out}")
                log = await call("sim_log")
                print("log:", [r["message"][:80] for r in log["result"]][-6:])
                await call("sim_stop")
            finally:
                await call("proteus_close")
            print(json.dumps(checks, indent=1))
            await call("project_export", project=P, dest=str(REPO / "work" / "scratch_example.pdsprj"))

asyncio.run(main())
