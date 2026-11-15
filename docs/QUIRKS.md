# Quirks: what Claude must know when writing lab code and driving Proteus

## Proteus start-up on this machine: "Symbol ... used but not found in libraries"

Every schematic opened in this install shows one or two modal warnings from Schematic Capture during load,
for example `Symbol "$MKRORIGIN" used but not found in libraries.` (also seen: `$MKRLABEL`). Investigated on
2026-11-14:

| Test | Result |
| --- | --- |
| Untouched copy of `labs/2025/lab5/taslak.pdsprj`, `PDS.EXE <path>`, working dir = `BIN\` (as the Start-menu shortcut) | `$MKRORIGIN` warning, then the schematic loads and draws correctly |
| Same file, working dir = repo folder | same warning |
| Untouched copy of Labcenter's own `SAMPLES\VSM for 8086\8086 Demo Board\DemoBoard.pdsprj` | same warning |
| Copy of the 2025 lab5 solution with only `Debug.exe` replaced (`ROOT.DSN`, `ROOT.CDB` byte-identical to the original) | `$MKRLABEL`, then `$MKRORIGIN` |
| `PDS.EXE` with no project | no warning (Home Page only) |

System Settings shows the library folder as `C:\Program Files (x86)\Labcenter Electronics\Proteus 8
Professional\Library`, and `LIBRARY\SYSTEM.LIB` there is readable and contains `$MKRORIGIN` and `$MKRLABEL`.
`C:\ProgramData\Labcenter Electronics` does not exist; the registry value `ProgramData` points at the install
folder. So the cause is in this install, not in the project files and not in how PDS.EXE is started. The exact
fault inside the install is not known. See "Simulation on this install" below: the same install also fails to
simulate any 8086 I/O, so these warnings are probably a symptom of a wider install problem, not harmless.

How the MCP handles it: the GUI layer reads every dialog. A dialog whose text is exactly
`Symbol "<name>" used but not found in libraries.` is recorded and answered with OK, and the symbol names are
returned with the launch result. Any other dialog stops automation. For a file the MCP wrote, the warnings
are compared with those of the source file it came from; a warning the source did not produce counts as a
failed write. "Don't display this message again" is never ticked (that would change the owner's settings).

## Simulation on this install: 8086 I/O never reaches a peripheral (blocks lab acceptance)

Investigated 2026-11-15. The simulator starts, the 8086 loads and executes the program, but no IN or OUT
reaches the bus peripherals:

| Test | Result |
| --- | --- |
| 2025 lab5, `mert_guler` solution, untouched | Display dark. Log shows only the 8255 power-on `CTRLW SET TO 9B`. |
| 2025 lab5, LordofNights13 solution, untouched and rebuilt with MASM 5.10 | Same. The 8086 Registers window shows execution past `OUT 06H, 82H` and inside the main loop; `IN` from port B returned `00` although PB is pulled up; the 8255 Internal Status window still shows control word `9B`. |
| Same project, Proteus's own netlist (Netlist Compiler, logical) | Wiring is correct: AD0-AD7 reach 8086, 74HC373 and 8255; PA0-7 through resistors to the display; PB0-7 to buttons with pull-ups. |
| Pin animation during the run | 8086 RESET low, READY high; latch outputs feeding 8255 A0/A1 stay undefined (grey), as if no bus cycle ever happens. |
| 2025 lab7, `mert_guler` solution (74273 latches, 74154, DAC0830) | Scope shows no trace. |
| Labcenter's own `SAMPLES\VSM for 8086\8086 Demo Board` | Same: 8255 stays at `9B`, the Virtual Terminal stays empty. |
| Labcenter's `SAMPLES\Interactive Simulation\Animated Circuits\TTLClock` (TTL logic only, no CPU) | Works: display lit (`0:00`), counter pins animate. So digital simulation and the 7-segment model are fine; the fault is specific to the 8086 driving its bus. |

All install files are dated February 2017 (PDS.EXE March 2017, SP2), so this is not an obvious mix of
versions. The 74HC373 model is present in `MODELS\DIGITAL.LML`. **Owner's manual test (2026-11-15):** opening the lab5 solution by hand and pressing Play gives the same
dark display and the identical log (`CTRLW SET TO 9B` only). So the fault is in this Proteus install, not in
how the MCP starts or drives it. Until one of the
sample projects shows output, no lab result can be verified by simulation on this machine.

## Things that do work (verified 2026-11-15)

- **Firmware attach:** writing `FIRMWARE/<name>/Debug/Debug.exe` into the project ZIP is enough. At run time
  Proteus unpacks it to a new temp folder and loads it (`Program Loaded: N bytes`); `sim_start` compares N with
  the load size of the EXE in the project. Proteus's own Build step is not involved.
- **Without CodeView info** the log adds `[EXE] Error: No debug information or its format is not supported`.
  The program still loads and runs; only source-level debugging is lost. (`debug_info=True` builds with
  `/Zi /CO`; not yet checked whether Proteus 8.6 reads MASM 5.10's CodeView format.)
- **Netlist:** Tool > Netlist Compiler, SDF, **Logical** mode. Physical mode silently drops every part without
  a reference designator (in the lab files: all resistors, buttons and displays). Output file names must be
  unique: Windows names are case-insensitive and Proteus appends `.SDF`.
- **Tabs:** Proteus restores the last tab per project from `<project>.<host>.<user>.workspace` and switches to
  Source Code when a simulation pauses. The Source Code tab has a different menu bar (no Tool menu). The MCP
  deletes stale workspace files before launch and clicks the Schematic Capture tab when it needs that menu.
- **Debug popups** (Registers, Memory Dump, 8255 Internal Status, Source Code, Watch Window) open from the
  Debug menu while paused and can be captured.

## Schematic edits (verified 2026-11-15)

- Wires and terminals written into ROOT.DSN are accepted by Proteus without touching ROOT.CDB.
- Annotated parts (U1, U2 ...) also have a record in ROOT.CDB, and Proteus takes their **properties and value**
  from there: the same change made only in ROOT.DSN is ignored (8086 CLOCK, 74HC373 value).
- Any rewrite of ROOT.CDB, even one that keeps every length, makes Proteus hang at "Initializing project..."
  on the splash screen. So property/value edits of annotated parts are refused by the tools; unannotated parts
  (resistors, buttons) can be changed (verified: a resistor's value).
- Proteus 8.6 may refuse to open project paths with non-ASCII characters (`tasarım.pdsprj`); project_open
  copies to an ASCII name in the workspace.

## Firmware build (MASM 5.10 through DOSBox)

The MCP assembles with MASM 5.10 and links with LINK 3.60, not with MASM32 `ml.exe` as the lab templates
assume. Builds run headless in about one second (`firmware_build`).

Tested on every `main.asm` in the 27 sample projects (`scripts/build_all.py work/x`, including the 5
`labs/2025_LordofNights13` projects): 19 of the 23 sources with code build; the 4 that fail do so for the
reasons below. Templates that hold only comments (2025 lab6,
lab7, lab8 `taslak`) give an empty EXE, which the builder reports as an `EMPTY` error.

Restrictions found:

| Symptom | Cause | Write instead |
| --- | --- | --- |
| `A2106 Line too long` | MASM 5.10 lines are limited to 128 characters | Split long `DB`/`DW` lists over several lines |
| `A2065 Label cannot have segment override` on `CALL FAR PTR [BX]` | MASM 6 syntax for an indirect far call | `CALL DWORD PTR [BX]` |
| `L4021 no stack segment` (warning) | No `SEGMENT STACK` in the program | Harmless for the 8086 model; add a stack segment to silence it |
| `EMPTY` | Source has no code / no `END label` | Write a program |

Not supported by MASM 5.10 at all (MASM 6 only): `.IF/.ELSE/.WHILE`, `INVOKE`, `PROTO`, `LOCAL` in `PROC`,
`OPTION`, `@@:` anonymous labels. Use plain labels and jumps. `.MODEL SMALL` / `.CODE` / `.DATA` / `.STACK`
simplified segments do work in 5.10, but the labs use full `SEGMENT` definitions; prefer those.

Non-ASCII characters (Turkish letters in comments) are written as code page 437; anything unmappable becomes
`?`. This only matters inside strings.

Compared with the shipped `Debug.exe` (2025 lab5 solution): same MZ header fields (entry `CS:IP`, `SS:SP`,
relocation table), same load image except where MASM 5.10 encodes a forward `JMP` as a 3-byte near jump where
MASM 6 used a 2-byte short jump. The shipped file also carries CodeView debug data after the load image; ours
does not (`debug_info=True` adds `/Zi` and `/CO`).


## 8086 code pitfalls found while testing

- `NOT` does not change any flags. `NOT AL` followed by `JZ`/`JNZ` tests whatever the zero flag was before.
  In the lab5 example this made the program ignore every key after the first release. Use `XOR AL, 0FFH`
  (or `NOT AL` then `TEST AL, AL`) when the result is tested.

- lab7 template: the ADC0804's VIN-, A GND and D GND are left open. SPICE then logs "failed to connect pin V-"
  and the analog side (DAC, op-amp, scope) does not run. Ground them.
- The Digital Oscilloscope popup does not draw its trace while it is off-screen or hidden (background mode);
  screenshots of it are blank there. Its window also opens on-screen by itself; background mode moves it.
