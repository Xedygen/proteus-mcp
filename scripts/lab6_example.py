"""Worked example: complete the 2025 lab6 template (8251 USART at 0158H) through the MCP tools, then test it.

    python -X utf8 scripts/lab6_example.py <project with a 74HC4078 placed>

The template lacks the gates a full address decoder needs, so first place one 74HC4078 with
schematic_place_devices (needs background mode off, it uses the mouse); this script copies it.

Decoder (isolated I/O, full decoding of 0158H/015AH):
    74154 A..D = A4..A7, output 5 -> 8251 /CS
    E1 (active low) = NOT( NOR8(A15..A9, A0) )                 all of them must be 0
    E2 (active low) = NOT( NOR8(A2, /A3, /A8, M/IO, 0, 0, 0, 0) )  A2=0, A3=1, A8=1, M/IO=0 (I/O cycle)
    8251 C/D = A1, so 0158H is data and 015AH control/status.
"""
import asyncio
import base64
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO = Path(__file__).resolve().parents[1]
_PLACED = [REPO / "work" / "projects" / "lab6_place" / "lab6_place.pdsprj",     # workspace layout
           REPO / "work" / "projects" / "lab6_place.pdsprj"]                    # older flat layout
SOURCE_PROJECT = sys.argv[1] if len(sys.argv) > 1 else str(next((p for p in _PLACED if p.exists()), _PLACED[0]))
P = "lab6_example"
T_IN, T_OUT = "$IVIRTUAL TERMINAL#0002", "$IVIRTUAL TERMINAL#000D"   # upper = input, lower = output

SOURCE = r"""
; BLM3061 LAB 6 - 8251 USART at 0158H (data) / 015AH (control, C/D = A1)
; Reads characters from terminal 1, keeps only A-Z, encrypts them (+3) and on '0' prints the latest
; three valid characters, in input order, on terminal 2.  Then starts over.
DATAP   EQU 0158H
CTRLP   EQU 015AH
MODEW   EQU 4DH             ; 01 00 11 01: 1 stop bit, no parity, 8 data bits, baud factor x1 (9600 Hz clock)
CMDW    EQU 15H             ; 0001 0101: error reset, receiver on, transmitter on

CODE    SEGMENT PUBLIC 'CODE'
        ASSUME CS:CODE, DS:CODE
START:  MOV AX, CS
        MOV DS, AX
        MOV DX, CTRLP       ; mode, internal reset, mode again, command
        MOV AL, MODEW
        OUT DX, AL
        MOV AL, 40H
        OUT DX, AL
        MOV AL, MODEW
        OUT DX, AL
        MOV AL, CMDW
        OUT DX, AL
        MOV COUNT, 0
NEXT:   MOV DX, CTRLP
WAITRX: IN  AL, DX          ; status bit 1 = RxRDY
        TEST AL, 02H
        JZ  WAITRX
        MOV DX, DATAP
        IN  AL, DX
        SHR AL, 1           ; Proteus 8251 model: shift after reading the data port
        CMP AL, '0'
        JE  FLUSH
        CMP AL, 'A'         ; only capital letters are kept
        JB  NEXT
        CMP AL, 'Z'
        JA  NEXT
        ADD AL, 3           ; encrypt
        MOV BL, COUNT
        CMP BL, 3
        JB  KEEP
        MOV AH, BUF+1       ; buffer full: drop the oldest
        MOV BUF, AH
        MOV AH, BUF+2
        MOV BUF+1, AH
        MOV BL, 2
        JMP SHORT PUT
KEEP:   INC COUNT
PUT:    XOR BH, BH
        MOV BUF[BX], AL
        JMP NEXT
FLUSH:  XOR SI, SI
        MOV CL, COUNT
        XOR CH, CH
        JCXZ DONE
SEND:   MOV DX, CTRLP
WAITTX: IN  AL, DX          ; status bit 0 = TxRDY
        TEST AL, 01H
        JZ  WAITTX
        MOV DX, DATAP
        MOV AL, BUF[SI]
        OUT DX, AL
        INC SI
        LOOP SEND
DONE:   MOV COUNT, 0
        JMP NEXT
BUF     DB 3 DUP (0)
COUNT   DB 0
CODE    ENDS
        END START
"""

SCENARIOS = [("ABC0", "DEF"), ("KL0", "NO"), ("A1B?Cn0", "DEF")]


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

            await call("project_open", path=SOURCE_PROJECT, name=P, overwrite=True)
            parts = {c["ref"]: c for c in (await call("schematic_components", project=P))["result"]}
            nor1 = next(r for r, c in parts.items() if c["device"] == "74HC4078")
            nor2 = (await call("schematic_add_part", project=P, like=nor1, x_grid=6, y_grid=-24))["part"]
            nots = [(await call("schematic_add_part", project=P, like="U5", x_grid=x, y_grid=y))["part"]
                    for x, y in ((0, -32), (12, -32), (-30, -20), (-30, -26))]
            n_e1, n_e2, n_a3, n_a8 = nots
            T = lambda pin, net, **k: call("schematic_add_terminal", project=P, pin=pin, net=net, **k)
            # E1 = NOT(NOR8(A15..A9, A0))
            for pin, net in zip("ABCDEFGH", ["A15", "A14", "A13", "A12", "A11", "A10", "A9", "A0"]):
                await T(f"{nor1}.{pin}", net)
            await call("schematic_connect", project=P, a=f"{nor1}.Y", b=f"{n_e1}.D")
            await T(f"{n_e1}.Q", "E1N")
            await T("U6.E1", "E1N")
            # E2 = NOT(NOR8(A2, /A3, /A8, M/IO, 0, 0, 0, 0))
            await T(f"{n_a3}.D", "A3")
            await T(f"{n_a3}.Q", "A3N")
            await T(f"{n_a8}.D", "A8")
            await T(f"{n_a8}.Q", "A8N")
            for pin, net in zip("ABCDEFGH", ["A2", "A3N", "A8N", "M/IO", "GND", "GND", "GND", "GND"]):
                await T(f"{nor2}.{pin}", net)
            await call("schematic_connect", project=P, a=f"{nor2}.Y", b=f"{n_e2}.D")
            await T(f"{n_e2}.Q", "E2N")
            await T("U6.E2", "E2N")
            # 74154 address inputs, output 5 -> 8251 /CS
            for pin, net in zip("ABCD", ["A4", "A5", "A6", "A7"]):
                await T(f"U6.{pin}", net)
            await T("U6.5", "8251CS")
            # 8251 bus control and serial lines
            await T("U7.C/$D$", "A1")
            await T("U7.$WR$", "/WR")
            await T("U7.$RD$", "/RD")
            await T(f"{T_IN}.TXD", "SER_IN")
            await T("U7.RxD", "SER_IN")
            await T("U7.TxD", "SER_OUT")
            await T(f"{T_OUT}.RXD", "SER_OUT")
            build = await call("firmware_build", project=P, source=SOURCE)
            print("firmware:", {k: build[k] for k in ("ok", "errors", "stored") if k in build})

            await call("proteus_launch", project=P)
            try:
                checks = {}
                cs = await call("net_of", ref="U7", pin="CS")
                checks["8251 CS <- 74154 Y5"] = "U6.5" in cs["members"]
                checks["C/D <- A1"] = "A1" == (await call("net_of", ref="U7", pin="C/D"))["net"]
                sim = await call("sim_start", wait_seconds=3)
                checks["firmware loaded"] = sim["firmware"].get("matches_project_exe")
                for k, (typed, expected) in enumerate(SCENARIOS):
                    await call("terminal_type", text=typed)
                    await asyncio.sleep(1.5)
                shots = await s.call_tool("terminal_screenshot", {})
                for i, c in enumerate(x for x in shots.content if x.type == "image"):
                    (REPO / "work" / f"lab6_terminal_{i}.png").write_bytes(base64.b64decode(c.data))
                print("typed", [t for t, _ in SCENARIOS], "expected on terminal 2:",
                      "".join(e for _, e in SCENARIOS), "- see work/lab6_terminal_*.png")
                log = await call("sim_log")
                print("log:", [r["message"][:80] for r in log["result"]][-6:])
                await call("sim_stop")
            finally:
                await call("proteus_close")
            print(json.dumps(checks, indent=1))
            await call("project_export", project=P, dest=str(REPO / "work" / "lab6_example.pdsprj"))

asyncio.run(main())
