"""Worked example: 2025 odev2 (memory map: 64 KB ROM at 20000H, 64 KB RAM at 60000H) through the MCP tools.

    python -X utf8 scripts/odev2_example.py [labs/2025/odev2/taslak.pdsprj]

Separate even/odd bank decoders, two 74LS138:
    C,B,A = A18,A17,A16   E1 (G1) = M/IO (high in memory cycles)   E2 (/G2A) = A19
    E3 (/G2B) = A0 for the even bank (U2), /BHE for the odd bank (U3)
    Y2 = ROM block 2xxxxH, Y6 = RAM block 6xxxxH
ROM halves by A15 (OR gates, active-low chip selects):
    ROM00/ROM01 (low block, 20000H-27FFFH):   /CE = Y2(bank) OR A15
    ROM10/ROM11 (high block, 28000H-2FFFFH):  /CE = Y2(bank) OR NOT A15
    ROM A0..A13 <- A1..A14, /OE <- /RD.   RAM: /CE = Y6(bank), A0..A14 <- A1..A15, /OE <- /RD, /WE <- /WR.
    Even chips on AD0..AD7, odd chips on AD8..AD15.
The ROM images hold two FAR routines: 2000:0000 writes 1234H to 6000:0000 and 2800:0002 writes ABCDH to
6002:0000, so RAM00 shows 34 at 0000 and CD at 0010, RAM01 12 and AB.
"""
import asyncio
import base64
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = sys.argv[1] if len(sys.argv) > 1 else str(REPO / "labs" / "2025" / "odev2" / "taslak.pdsprj")
P = "odev2_example"

SOURCE = r"""
; BLM3061 ODEV 2 - calls the two FAR routines stored in the ROMs, forever
; ROM block 20000H-2FFFFH, RAM block 60000H-6FFFFH (see the decoder description in the project notes)
CODE    SEGMENT PUBLIC 'CODE'
        ASSUME CS:CODE
START:  MOV AX, CS
        MOV SS, AX
        MOV SP, OFFSET STKTOP
ENDLESS:
        DB  9AH             ; CALL FAR 2000H:0000H
        DW  0000H, 2000H
        DB  9AH             ; CALL FAR 2800H:0002H
        DW  0002H, 2800H
        JMP ENDLESS
STK     DW 32 DUP (0)
STKTOP  LABEL WORD
CODE    ENDS
        END START
"""


def io_pins(part):
    """Inputs (left-most two) and output (right-most) of a two-input gate, from pin positions."""
    pins = sorted(part["pins"], key=lambda p: p["grid"][0])
    return [pins[0]["name"], pins[1]["name"]], pins[-1]["name"]


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

            await call("proteus_background", on=False)      # placing the OR gate uses the mouse
            opened = await call("project_open", path=TEMPLATE, name=P, overwrite=True)
            print("files copied with the project:", opened.get("files_copied"))
            T = lambda pin, net: call("schematic_add_terminal", project=P, pin=pin, net=net)

            # gates: one OR from the library, three copies of it, and a NOT (copy of the template's U8)
            first = (await call("schematic_place_devices", project=P,
                                devices=[{"device": "OR", "x_grid": 0, "y_grid": -20}]))["result"][0]["ref"]
            await call("schematic_move", project=P, ref=first, x_grid=0, y_grid=-10)
            gates = [first]
            for y in (-17, -24, -31):        # free area below the latches, left of the RAMs
                gates.append((await call("schematic_add_part", project=P, like=first, x_grid=0, y_grid=y))["part"])
            inv = (await call("schematic_add_part", project=P, like="U8", x_grid=-15, y_grid=-10))["part"]
            parts = {c["ref"]: c for c in (await call("schematic_components", project=P))["result"]}

            # decoders
            for dec, bank in (("U2", "A0"), ("U3", "$BHE$")):
                for pin, net in (("A", "A16"), ("B", "A17"), ("C", "A18"), ("E1", "M/$IO$"), ("E2", "A19"),
                                 ("E3", bank)):
                    await T(f"{dec}.{pin}", net)
            await T("U2.Y2", "ROMSEL0")
            await T("U3.Y2", "ROMSEL1")
            await T("U2.Y6", "RAMCS0")
            await T("U3.Y6", "RAMCS1")
            await T(f"{inv}.D", "A15")
            await T(f"{inv}.Q", "A15N")
            # ROM chip selects: (gate, bank select, A15 or A15N, chip)
            for gate, sel, a15, rom in zip(gates, ["ROMSEL0", "ROMSEL0", "ROMSEL1", "ROMSEL1"],
                                           ["A15", "A15N", "A15", "A15N"], ["ROM00", "ROM10", "ROM01", "ROM11"]):
                (i1, i2), out = io_pins(parts[gate])
                await T(f"{gate}.{i1}", sel)
                await T(f"{gate}.{i2}", a15)
                await T(f"{gate}.{out}", f"CS_{rom}")
                await T(f"{rom}.$CE$", f"CS_{rom}")
            # ROM address, data and output enable
            for rom in ("ROM00", "ROM01", "ROM10", "ROM11"):
                base = 0 if rom.endswith("0") else 8
                for i in range(14):
                    await T(f"{rom}.A{i}", f"A{i + 1}")
                for i in range(8):
                    await T(f"{rom}.D{i}", f"AD{i + base}")
                await T(f"{rom}.$OE$", "$RD$")
            # RAM
            for ram, cs in (("RAM00", "RAMCS0"), ("RAM01", "RAMCS1")):
                base = 0 if ram.endswith("0") else 8
                for i in range(15):
                    await T(f"{ram}.A{i}", f"A{i + 1}")
                for i in range(8):
                    await T(f"{ram}.D{i}", f"AD{i + base}")
                await T(f"{ram}.$CE$", cs)
                await T(f"{ram}.$OE$", "$RD$")
                await T(f"{ram}.$WE$", "$WR$")
            build = await call("firmware_build", project=P, source=SOURCE)
            print("firmware:", {k: build[k] for k in ("ok", "errors", "stored") if k in build})

            await call("proteus_launch", project=P)
            try:
                checks = {}
                net = lambda ref, pin: call("net_of", ref=ref, pin=pin)
                checks["ROM00 /CE from an OR of Y2(even)"] = any(m.startswith(gates[0] + ".")
                                                               for m in (await net("ROM00", "CE"))["members"])
                checks["RAM01 /CE <- U3 Y6"] = "U3.Y6" in (await net("RAM01", "CE"))["members"]
                checks["ROM11 D0 on AD8"] = (await net("ROM11", "D0"))["net"] == "AD8"
                sim = await call("sim_start", wait_seconds=3)
                checks["firmware loaded"] = sim["firmware"].get("matches_project_exe")
                await call("sim_pause")
                for ram in ("RAM00", "RAM01"):
                    res = await s.call_tool("debug_window", {"name": f"Memory Contents - {ram}"})
                    for c in res.content:
                        if c.type == "image":
                            (REPO / "work" / f"odev2_{ram}.png").write_bytes(base64.b64decode(c.data))
                        else:
                            print(ram, c.text[:300])
                log = await call("sim_log")
                print("log:", [r["message"][:90] for r in log["result"]][-8:])
                await call("sim_stop")
            finally:
                await call("proteus_close")
            print(json.dumps(checks, indent=1))
            await call("project_export", project=P, dest=str(REPO / "work" / "odev2_example.pdsprj"))

asyncio.run(main())
