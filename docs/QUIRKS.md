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
fault inside the install is not known.

How the MCP handles it: the GUI layer reads every dialog. A dialog whose text is exactly
`Symbol "<name>" used but not found in libraries.` is recorded and answered with OK, and the symbol names are
returned with the launch result. Any other dialog stops automation. For a file the MCP wrote, the warnings
are compared with those of the source file it came from; a warning the source did not produce counts as a
failed write. "Don't display this message again" is never ticked (that would change the owner's settings).

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
