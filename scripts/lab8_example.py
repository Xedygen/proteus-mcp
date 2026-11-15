"""Worked example: 2025 lab8 (8259 + ADC0804 + 8251, interrupt driven) through the MCP tools.

    python -X utf8 scripts/lab8_example.py [labs/2025/lab8/taslak.pdsprj]

Decoder (isolated I/O, partial decoding; the PDF fixes no addresses):
    74154 A..D = A4..A7, E1 = M/IO (low in I/O cycles), E2 = A0 (even ports only, D0-D7 on the low byte)
    Y0 -> 8259 /CS   00H (A0 = 0) / 02H (A0 = 1), 8259 A0 = A1
    Y1 -> 8251 /CS   10H data / 12H control, C/D = A1
    Y2 -> ADC /CS    20H: OUT starts a conversion, IN reads it
Interrupts (edge triggered, ICW1 = 13H, ICW2 = 50H):
    IR1 (vector 51H) <- 8251 TxRDY            IR2 (vector 52H) <- NOT(ADC /INTR)
The ADC handler stores a sample and starts the next conversion; after five it masks IR2, unmasks IR1 and
writes the first byte itself: TxRDY drops and rises again when the byte moves on, and those edges drive
the send handler through the other four.  After the fifth byte it unmasks IR2 and starts the next round.
Proteus model notes (found here): the ADC0804 needs one read before its first conversion start; the 8259
model marks IR1 in service as soon as it is requested, so each handler sends a specific EOI; a request
latched while masked may come back as the spurious vector 57H (IR7), whose handler also sends an EOI.
The ADC0804 uses its internal 640 kHz clock (INTCLK=1); the 8251 gets 9600 Hz on TxC/RxC (x1 -> 9600 baud).
"""
import asyncio
import base64
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = sys.argv[1] if len(sys.argv) > 1 else str(REPO / "labs" / "2025" / "lab8" / "taslak.pdsprj")
P = "lab8_example"
TERM = "$IVIRTUAL TERMINAL#0006"

SOURCE = r"""
; BLM3061 LAB 8 - 8259 + ADC0804 + 8251A, interrupt driven
; 8259: 00H / 02H   8251: 10H data, 12H control   ADC0804: 20H
; INT 52H (IR2) = ADC conversion done, INT 51H (IR1) = 8251 transmitter ready
P8259A  EQU 00H
P8259B  EQU 02H
DATA51  EQU 10H
CTRL51  EQU 12H
ADCP    EQU 20H
NSAMP   EQU 5

CODE    SEGMENT PUBLIC 'CODE'
        ASSUME CS:CODE, DS:CODE
START:  CLI
        MOV AX, CS
        MOV DS, AX
        MOV SS, AX
        MOV SP, OFFSET STKTOP
        XOR AX, AX          ; interrupt vector table at 0000:0000
        MOV ES, AX
        MOV WORD PTR ES:[51H*4], OFFSET TXISR
        MOV WORD PTR ES:[51H*4+2], CS
        MOV WORD PTR ES:[52H*4], OFFSET ADCISR
        MOV WORD PTR ES:[52H*4+2], CS
        MOV WORD PTR ES:[57H*4], OFFSET SPUR   ; IR7 = 8259 spurious interrupt
        MOV WORD PTR ES:[57H*4+2], CS
        ; 8259: ICW1 edge triggered, single, ICW4 needed; ICW2 base 50H; ICW4 8086 mode, normal EOI
        MOV AL, 13H
        OUT P8259A, AL
        MOV AL, 50H
        OUT P8259B, AL
        MOV AL, 01H
        OUT P8259B, AL
        MOV AL, 0FBH        ; OCW1: only IR2 (ADC) enabled
        OUT P8259B, AL
        ; 8251: mode, internal reset, mode, command (transmitter off until five samples are in)
        MOV AL, 4DH         ; 1 stop bit, no parity, 8 data bits, x1
        OUT CTRL51, AL
        MOV AL, 40H
        OUT CTRL51, AL
        MOV AL, 4DH
        OUT CTRL51, AL
        MOV AL, 15H         ; error reset, receiver on, transmitter on
        OUT CTRL51, AL
        MOV COUNT, 0
        IN  AL, ADCP        ; dummy read: the ADC model ignores the first start without it
        MOV CX, 2000
WAIT0:  LOOP WAIT0
        OUT ADCP, AL        ; start the first conversion
        STI
MAIN:   JMP MAIN

; INT 52H: read the sample, store it, start the next conversion; after five start sending
ADCISR: PUSH AX
        PUSH BX
        IN  AL, ADCP        ; raw 8-bit value, also clears /INTR
        MOV BL, COUNT
        XOR BH, BH
        MOV BUF[BX], AL
        INC COUNT
        CMP COUNT, NSAMP
        JAE TXGO
        OUT ADCP, AL        ; next conversion
        JMP SHORT AEOI
TXGO:   MOV COUNT, 0
        MOV AL, 0FDH        ; only IR1 (transmitter) enabled
        OUT P8259B, AL
        MOV AL, BUF         ; first byte: its TxRDY edges raise IR1 for the rest
        OUT DATA51, AL
        MOV IDX, 1
AEOI:   MOV AL, 62H         ; specific EOI for IR2
        OUT P8259A, AL
        POP BX
        POP AX
        IRET

; INT 51H: send the next stored sample; after the last one go back to sampling
TXISR:  PUSH AX
        PUSH BX
        MOV BL, IDX
        CMP BL, NSAMP       ; a stale request after the round: nothing to send
        JAE TEOI
WTX:    IN  AL, CTRL51      ; wait for TxRDY (a request latched earlier may arrive early)
        TEST AL, 01H
        JZ  WTX
        XOR BH, BH
        MOV AL, BUF[BX]
        OUT DATA51, AL
        INC IDX
        CMP IDX, NSAMP
        JB  TEOI
        MOV AL, 0FBH        ; all five sent: only IR2 again, next round of conversions
        OUT P8259B, AL
        OUT ADCP, AL
TEOI:   MOV AL, 61H         ; specific EOI for IR1
        OUT P8259A, AL
        POP BX
        POP AX
        IRET

; INT 57H: spurious interrupt (a request latched while masked whose line dropped before INTA).  A real
; 8259 needs no EOI here, but the Proteus model keeps that level as its current priority until one comes.
SPUR:   PUSH AX
        MOV AL, 20H
        OUT P8259A, AL
        POP AX
        IRET

BUF     DB NSAMP DUP (0)
COUNT   DB 0
IDX     DB 0
STK     DW 64 DUP (0)
STKTOP  LABEL WORD
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

            await call("proteus_background", on=False)      # pot placement and hex mode use the mouse
            await call("project_open", path=TEMPLATE, name=P, overwrite=True)
            # the template's POT is static in simulation; an interactive POT-HG replaces it (placed by
            # Proteus itself, so this step needs background mode off), then the old one is deleted
            pot = (await call("schematic_place_devices", project=P,
                              devices=[{"device": "POT-HG", "x_grid": -12, "y_grid": -26}]))["result"][0]["ref"]
            await call("schematic_delete", project=P, ref="RV1")
            await call("schematic_move", project=P, ref=pot, x_grid=-12, y_grid=-19)   # where RV1 was
            T = lambda pin, net: call("schematic_add_terminal", project=P, pin=pin, net=net)
            inv = (await call("schematic_add_part", project=P, like="U8", x_grid=24, y_grid=-28))["part"]
            # decoder
            for pin, net in zip("ABCD", ["A4", "A5", "A6", "A7"]):
                await T(f"U11.{pin}", net)
            await T("U11.E1", "M/$IO$")
            await T("U11.E2", "A0")
            for out, net in (("0", "CS8259"), ("1", "CS8251"), ("2", "CS0804")):
                await T(f"U11.{out}", net)
            # 8259
            for pin, net in (("$CS$", "CS8259"), ("$WR$", "$WR$"), ("$RD$", "$RD$"), ("A0", "A1"),
                             ("INT", "INTR"), ("$INTA$", "$INTA$"), ("$SP$/$EN$", "VCC"),
                             ("IR1", "TXRDY"), ("IR2", "ADCINT"), ("D[0..7]", "AD[0..7]")):
                await T(f"U5.{pin}", net)
            # 8251
            for pin, net in (("$CS$", "CS8251"), ("C/$D$", "A1"), ("$WR$", "$WR$"), ("$RD$", "$RD$"),
                             ("RESET", "GND"), ("$CTS$", "GND"), ("TxRDY", "TXRDY"), ("D[0..7]", "AD[0..7]"),
                             ("TxD", "SER_OUT"), ("RxD", "SER_IN")):
                await T(f"U9.{pin}", net)
            await call("schematic_add_generator", project=P, pin="U9.$TxC$", frequency_hz=9600, name="BAUD_TX")
            await call("schematic_add_generator", project=P, pin="U9.$RxC$", frequency_hz=9600, name="BAUD_RX")
            await T(f"{TERM}.RXD", "SER_OUT")
            await T(f"{TERM}.TXD", "SER_IN")
            # ADC0804 and the potentiometer (pin 3 is the wiper)
            for pin, net in (("$CS", "CS0804"), ("$RD", "$RD$"), ("$WR", "$WR$"), ("$INTR", "ADC_INTR"),
                             ("VIN+", "VIN"), ("VIN-", "GND"), ("A GND", "GND"), ("D GND", "GND"), ("VCC", "VCC")):
                await T(f"U2.{pin}", net)
            for i, pin in enumerate(["DB0(LSB)", "DB1", "DB2", "DB3", "DB4", "DB5", "DB6", "DB7(MSB)"]):
                await T(f"U2.{pin}", f"AD{i}")
            await T(f"{inv}.D", "ADC_INTR")
            await T(f"{inv}.Q", "ADCINT")
            await T(f"{pot}.1", "GND")
            await T(f"{pot}.2", "VCC")
            await T(f"{pot}.3", "VIN")
            build = await call("firmware_build", project=P, source=SOURCE)
            print("firmware:", {k: build[k] for k in ("ok", "errors", "stored") if k in build})

            await call("proteus_launch", project=P)
            try:
                checks = {}
                net = lambda ref, pin: call("net_of", ref=ref, pin=pin)
                checks["8259 /CS <- 74154 Y0"] = "U11.0" in (await net("U5", "$CS$"))["members"]
                checks["8251 /CS <- 74154 Y1"] = "U11.1" in (await net("U9", "$CS$"))["members"]
                checks["ADC /CS <- 74154 Y2"] = "U11.2" in (await net("U2", "$CS"))["members"]
                checks["IR1 <- TxRDY"] = "U9.TXRDY" in (await net("U5", "IR1"))["members"]
                checks["IR2 <- NOT(/INTR)"] = f"{inv}.Q" in (await net("U5", "IR2"))["members"]
                checks["8259 INT -> 8086 INTR"] = "U1.INTR" in (await net("U5", "INT"))["members"]
                checks["VIN+ <- wiper"] = f"{pot}.3" in (await net("U2", "VIN+"))["members"]
                checks["ADC DB0 on AD0"] = (await net("U2", "DB0(LSB)"))["net"] == "AD0"
                sim = await call("sim_start", wait_seconds=4)
                checks["firmware loaded"] = sim["firmware"].get("matches_project_exe")
                await call("terminal_mode", item="Hex Display Mode", on=True)
                # turn the pot: hold its lower button, then its upper one; the readings follow
                for step, (dy, hold) in enumerate(((0, 0), (-1.05, 4.0), (1.05, 8.0))):
                    if hold:
                        await call("sim_click", ref=pot, dx_grid=-1.27, dy_grid=dy, hold_seconds=hold)
                    await asyncio.sleep(1.5)
                    shots = await s.call_tool("terminal_screenshot", {})
                    for c in (x for x in shots.content if x.type == "image"):
                        (REPO / "work" / f"lab8_terminal_{step}.png").write_bytes(base64.b64decode(c.data))
                log = await call("sim_log")
                print("log:", [r["message"][:90] for r in log["result"]][-10:])
                await call("sim_stop")
            finally:
                await call("proteus_close")
            print(json.dumps(checks, indent=1))
            await call("project_export", project=P, dest=str(REPO / "work" / "lab8_example.pdsprj"))

asyncio.run(main())
