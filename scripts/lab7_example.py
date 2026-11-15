"""Worked example: 2025 lab7 part 1 (DAC0830 at 200H, 100 Hz sine with 32 samples) through the MCP tools.

    python -X utf8 scripts/lab7_example.py [labs/2025/lab7/taslak.pdsprj]

The template's decoder already exists: 74154 inputs A9..A12, E1 = M/IO OR A0, E2 = NOT(NOR(A13..A15)), so
200H -> /IO1, 400H -> /IO2, 800H -> /IO4.  The placeholder labels are renamed accordingly:
    YA6 (DAC0830 /CS)      -> $IO1$        YA1 (ADC0804 /CS)     -> $IO2$
    YA2, YA3 (ADC VIN+, LDR divider) -> SENSOR
    YA4, YA5 (motor supply, op-amp output) -> DAC_OUT
v(t) = 6 + 6 sin(2 pi f t): the DAC drives an inverting op-amp with VREF = -12 V, so Vout = 12 V * D / 256 and
D = 127.5 + 127.5 sin(2 pi k / 32).
"""
import asyncio
import base64
import json
import math
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = sys.argv[1] if len(sys.argv) > 1 else str(REPO / "labs" / "2025" / "lab7" / "taslak.pdsprj")
P = "lab7_example"

TABLE = [round(127.5 + 127.5 * math.sin(2 * math.pi * k / 32)) for k in range(32)]
SAMPLE_US = 1e6 / (100 * 32)          # 312.5 us per sample for 100 Hz
CLOCK_HZ = 1.5e6                      # 8086 CLOCK property in the template
LOOP_FIXED, LOOP_PER = 40, 17         # clock cycles: loop body, one LOOP pass
DELAY = round((SAMPLE_US * 1e-6 * CLOCK_HZ - LOOP_FIXED) / LOOP_PER)

SOURCE = f"""
; BLM3061 LAB 7 part 1 - 100 Hz sine on DAC0830 at 200H, 32 samples per period
; sample period 312.5 us; 8086 at 1.5 MHz -> about 469 clocks per sample:
; ~{LOOP_FIXED} clocks of loop body + {LOOP_PER} per DELAY pass, DELAY = {DELAY}
DACP    EQU 200H

CODE    SEGMENT PUBLIC 'CODE'
        ASSUME CS:CODE
START:  MOV DX, DACP
        XOR SI, SI
NEXTS:  MOV AL, CS:SINE[SI] ; next sample
        OUT DX, AL
        INC SI
        AND SI, 31          ; 32 samples, wrap around
        CALL DELAYP
        JMP NEXTS

DELAYP  PROC NEAR           ; waits DELAY loop passes
        MOV CX, {DELAY}
DLY:    LOOP DLY
        RET
DELAYP  ENDP

; D = 127.5 + 127.5 sin(2 pi k / 32), k = 0..31
SINE    DB {", ".join(map(str, TABLE[:16]))}
        DB {", ".join(map(str, TABLE[16:]))}
CODE    ENDS
        END START
"""

RENAMES = [("YA6", "$IO1$"), ("YA1", "$IO2$"), ("YA2", "SENSOR"), ("YA3", "SENSOR"),
           ("YA4", "DAC_OUT"), ("YA5", "DAC_OUT")]


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

            await call("proteus_background", on=False)      # the scope only draws while it is visible
            await call("project_open", path=TEMPLATE, name=P, overwrite=True)
            for old, new in RENAMES:
                await call("schematic_rename_label", project=P, old=old, new=new)
            # the template leaves the ADC0804's VIN-, A GND and D GND open; SPICE then reports
            # "failed to connect pin V-" and the analog part (DAC, op-amp, scope) does not run
            for pin in ("VIN-", "A GND", "D GND"):
                await call("schematic_add_terminal", project=P, pin=f"U111.{pin}", net="GND")
            build = await call("firmware_build", project=P, source=SOURCE)
            print("firmware:", {k: build[k] for k in ("ok", "errors", "stored") if k in build}, "DELAY", DELAY)
            print("sample table:", TABLE)

            await call("proteus_launch", project=P)
            try:
                checks = {}
                cs = await call("net_of", ref="U11", pin="$CS")
                checks["DAC /CS on /IO1 (200H)"] = "U5.1" in cs["members"]
                adc = await call("net_of", ref="U111", pin="$CS")
                checks["ADC /CS on /IO2 (400H)"] = "U5.2" in adc["members"]
                vin = await call("net_of", ref="U111", pin="VIN+")
                checks["ADC VIN+ on the LDR divider"] = any(m.startswith(("R3.", "LDR1.")) for m in vin["members"])
                sim = await call("sim_start", wait_seconds=4)
                checks["firmware loaded"] = sim["firmware"].get("matches_project_exe")
                await asyncio.sleep(2)
                log = await call("sim_log")
                print("log:", [r["message"][:70] for r in log["result"]])
                shot = await s.call_tool("screenshot", {"window": "Oscilloscope"})
                for c in shot.content:
                    if c.type == "image":
                        (REPO / "work" / "lab7_scope.png").write_bytes(base64.b64decode(c.data))
                print("scope saved to work/lab7_scope.png")
                await call("sim_stop")
            finally:
                await call("proteus_close")
            print(json.dumps(checks, indent=1))
            await call("project_export", project=P, dest=str(REPO / "work" / "lab7_example.pdsprj"))

asyncio.run(main())
