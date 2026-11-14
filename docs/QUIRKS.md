# Quirks: what Claude must know when writing lab code and driving Proteus

## Firmware build (MASM 5.10 through DOSBox)

The MCP assembles with MASM 5.10 and links with LINK 3.60, not with MASM32 `ml.exe` as the lab templates
assume. Builds run headless in about one second (`firmware_build`).

Tested on every `main.asm` in the 22 sample projects (`scripts/build_all.py work/x`): 14 of the 18 sources
with code build; the 4 that fail do so for the reasons below. Templates that hold only comments (2025 lab6,
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
