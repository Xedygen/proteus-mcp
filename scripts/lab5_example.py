"""Worked example: complete the 2025 lab5 template (8255 + 7-segment) through the MCP tools, then check it.

    python -X utf8 scripts/lab5_example.py [labs/2025/lab5/taslak.pdsprj]

Drives the real server over stdio: opens the template, wires the 8255 address lines, adds the eight series
resistors, connects ports A/B to the display, buttons and pull-ups with named terminals, builds the 8086
program with MASM 5.10, then launches Proteus and compares Proteus's own netlist with what the lab requires.
"""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = sys.argv[1] if len(sys.argv) > 1 else str(REPO / "labs" / "2025" / "lab5" / "taslak.pdsprj")
P = "lab5_example"

SOURCE = r"""
; BLM3061 LAB 5 - 8255 + common-anode 7-segment display
; 8255 A0/A1 are on latch outputs Q1/Q2 (address bits A1/A2), so the ports are at even addresses.
; Port A: output to the display through series resistors.  Port B: input, 8 buttons to GND with pull-ups.
PORTA   EQU 00H
PORTB   EQU 02H
CTRL    EQU 06H
CTRLW   EQU 82H             ; 1000 0010: I/O mode, mode 0, PA out, PC-hi out, PB in, PC-lo out

CODE    SEGMENT PUBLIC 'CODE'
        ASSUME CS:CODE
START:
        MOV AL, CTRLW       ; port directions
        OUT CTRL, AL
        MOV AL, 0C0H        ; show 0 at start
        OUT PORTA, AL
MAINLOOP:
        IN  AL, PORTB       ; pressed button reads 0
        XOR AL, 0FFH        ; invert (NOT would leave the flags unchanged, so JZ would test stale ZF)
        JZ  MAINLOOP        ; nothing pressed: keep the digit
        MOV BX, OFFSET TABLE
        MOV CX, 8
FIND:   SHR AL, 1           ; lowest pressed button wins
        JC  FOUND
        INC BX
        LOOP FIND
        JMP MAINLOOP
FOUND:  MOV AL, CS:[BX]     ; segment code for this button
        OUT PORTA, AL
        JMP MAINLOOP
; common anode, bit order dp g f e d c b a, 0 = segment on: digits 0..7
TABLE   DB 0C0H, 0F9H, 0A4H, 0B0H, 099H, 092H, 082H, 0F8H
CODE    ENDS
        END START
"""

SEGMENTS = "ABCDEFGH"              # display pins; PA0 -> A ... PA7 -> H (dp)
PULLUPS = ["RES#0016", "RES#0015", "RES#0014", "RES#0013", "RES#0012", "RES#0011", "RES#0010"]
BUTTONS = ["BUTTON#0007", "BUTTON#0008", "BUTTON#0009", "BUTTON#000A",
           "BUTTON#000B", "BUTTON#000C", "BUTTON#000D", "BUTTON#000E"]
DISPLAY = "7SEG-MPX1-CA#0005"


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
                return res.structured_content or json.loads(res.content[0].text)

            await call("project_open", path=TEMPLATE, name=P, overwrite=True)
            # address lines: 8255 A0/A1 from latch Q1/Q2; RESET to ground
            await call("schematic_connect", project=P, a="U2.Q1", b="U3.A0")
            await call("schematic_connect", project=P, a="U2.Q2", b="U3.A1")
            await call("schematic_add_terminal", project=P, pin="U3.RESET", net="GND")
            # port A -> series resistor -> segment, through named terminals PA0..PA7 / SEGA..SEGH
            for i, seg in enumerate(SEGMENTS):
                part = (await call("schematic_add_part", project=P, like="RES#0010", x_grid=60 + 2 * i,
                                   y_grid=20, value="330"))["part"]
                await call("schematic_add_terminal", project=P, pin=f"U3.PA{i}", net=f"PA{i}")
                await call("schematic_add_terminal", project=P, pin=f"{part}.2", net=f"PA{i}")
                await call("schematic_add_terminal", project=P, pin=f"{part}.1", net=f"SEG{seg}")
                await call("schematic_add_terminal", project=P, pin=f"{DISPLAY}.{seg}", net=f"SEG{seg}",
                           distance_grid=2 + (i % 2))
            await call("schematic_add_terminal", project=P, pin=f"{DISPLAY}.1", net="VCC")
            # port B: button to GND, pull-up to VCC, both on net PBn
            pullups = PULLUPS + [(await call("schematic_add_part", project=P, like="RES#0010", x_grid=47,
                                             y_grid=6, value="1k"))["part"]]
            for i, (button, pull) in enumerate(zip(BUTTONS, pullups)):
                await call("schematic_add_terminal", project=P, pin=f"U3.PB{i}", net=f"PB{i}")
                await call("schematic_add_terminal", project=P, pin=f"{button}.1", net=f"PB{i}")
                await call("schematic_add_terminal", project=P, pin=f"{button}.2", net="GND")
                await call("schematic_add_terminal", project=P, pin=f"{pull}.2", net=f"PB{i}")
                await call("schematic_add_terminal", project=P, pin=f"{pull}.1", net="VCC",
                           distance_grid=1 if pull == "RES#0011" else 2)
            build = await call("firmware_build", project=P, source=SOURCE)
            print("firmware:", {k: build[k] for k in ("ok", "errors", "stored") if k in build})

            await call("proteus_launch", project=P)
            try:
                checks = {}
                for i, seg in enumerate(SEGMENTS):
                    pa = await call("net_of", ref="U3", pin=f"PA{i}")
                    seg_net = await call("net_of", ref=DISPLAY, pin=seg)
                    res = [m.split(".")[0] for m in pa["members"] if m.startswith("R")]
                    checks[f"PA{i}->R->{seg}"] = bool(res) and any(m.startswith(res[0] + ".") for m in seg_net["members"])
                for i, button in enumerate(BUTTONS):
                    pb = await call("net_of", ref="U3", pin=f"PB{i}")
                    checks[f"PB{i}:button+pullup"] = (f"{button}.1" in pb["members"]
                                                      and sum(m.startswith(("R", "RES")) for m in pb["members"]) == 1)
                checks["A0<-Q1"] = "U2.Q1" in (await call("net_of", ref="U3", pin="A0"))["members"]
                checks["A1<-Q2"] = "U2.Q2" in (await call("net_of", ref="U3", pin="A1"))["members"]
                checks["RESET=GND"] = (await call("net_of", ref="U3", pin="RESET"))["net"] == "GND"
                checks["display common=VCC"] = "VCC" in (await call("net_of", ref=DISPLAY, pin="1"))["net"]
                sim = await call("sim_start", wait_seconds=3)
                checks["firmware loaded"] = sim["firmware"].get("matches_project_exe")
                # press every button and keep a close-up of the display after each press
                import base64
                from PIL import Image as PILImage
                import io
                shots = []
                for i, button in enumerate(BUTTONS):
                    await call("sim_click", ref=button, hold_seconds=0.4)
                    img = await s.call_tool("screenshot_part", {"ref": DISPLAY, "margin_grid": 1})
                    data = next(c.data for c in img.content if c.type == "image")
                    shots.append(PILImage.open(io.BytesIO(base64.b64decode(data))))
                w, h = shots[0].size
                strip = PILImage.new("RGB", (w * len(shots), h), "white")
                for i, im in enumerate(shots):
                    strip.paste(im, (i * w, 0))
                strip.save(REPO / "work" / "lab5_example_buttons.png")
                print("display after pressing buttons 0..7 saved to work/lab5_example_buttons.png")
                await call("sim_stop")
                shot = await s.call_tool("screenshot", {})
                for c in shot.content:
                    if c.type == "image":
                        import base64
                        (REPO / "work" / "lab5_example.png").write_bytes(base64.b64decode(c.data))
            finally:
                await call("proteus_close")
            bad = [k for k, v in checks.items() if not v]
            print(json.dumps(checks, indent=1))
            print("ALL CONNECTIVITY CHECKS PASS" if not bad else f"FAILED: {bad}")
            await call("project_export", project=P, dest=str(REPO / "work" / "lab5_example.pdsprj"))

asyncio.run(main())
