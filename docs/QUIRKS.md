# Quirks: what Claude must know when writing lab code and driving Proteus

## Start-up warnings and simulation (resolved 2026-11-15)

The first Proteus install here showed `Symbol "$MKRORIGIN" used but not found in libraries.` on every load and
no 8086 IN/OUT ever reached a peripheral, even in Labcenter's own samples. The owner reinstalled Proteus 8.6;
since then the labs simulate correctly (lab5 display and buttons, lab6 8251, lab8 8259/ADC/8251).
A dialog whose text is exactly `Symbol "<name>" used but not found in libraries.` is still answered with OK
and reported in the launch result's `warnings`; any other unknown dialog stops automation.

## Model quirks found while building the labs

- **8251 USART:** after `IN AL, data_port` run `SHR AL, 1` (lab PDFs say so too). Mode 4DH (x1, 8N1) with a
  9600 Hz clock on TxC/RxC (`schematic_add_generator`) gives 9600 baud. The CLK pin can stay open; tie RESET
  and /CTS to GND and reset it in software (mode, 40H, mode, command).
- **8251 TxRDY as an interrupt:** the TxRDY pin does not drop when the transmitter is disabled, so toggling
  TxEN gives no new edge. Start a burst by writing the first byte yourself; TxRDY then falls and rises.
- **ADC0804:** do one `IN` from the ADC before the first conversion start (`OUT`), or the first start is
  ignored and /INTR never falls. Internal clock (INTCLK=1, 640 kHz) is enough; no generator needed.
  Edge-triggered 8259 input: feed it NOT(/INTR).
- **8259 PIC:** the model marks a newly requested level in service early, so a non-specific EOI (20H) can
  clear the wrong level. Send specific EOIs (60H + level). A request latched while masked can come back as the
  spurious vector base+7; give it a handler that sends an EOI 20H and IRETs (the model needs that EOI).
- **POT (plain)** is static in simulation (always mid-scale). Use **POT-HG** (place it with
  `schematic_place_devices`), then hold its red buttons with `sim_click(dx_grid=-1.27, dy_grid=+-1.05,
  hold_seconds=...)`; roughly one step per 0.2 s.
- **Digital Oscilloscope** draws no trace on this install, not even for the student's working lab7 solution
  (checked with a real screen grab, not only PrintWindow). Check analog outputs with the template's DC
  voltmeters instead: they average a fast signal (lab7's 6 + 6 sin shows 5.99 V), so for evidence build a
  slowed-down copy of the program (about 0.7 s per sample) and sample the meter with screenshot_part.
- **8255 log:** the simulation log shows only the power-on control word (`CTRLW SET TO 9B`), not the one your
  program writes; read it in debug_window("8255 Internal Status") instead.
- **Debug panels** (Registers, Memory Dump, Variables) open docked at the bottom of the Source Code tab;
  debug_window finds them by name and returns their picture.
- **Clock generators** do not show up in the netlist: a pin with only a generator looks like a single-pin net
  in net_of. Confirm clocks from behaviour (e.g. the baud rate works) or with schematic_labels.
- **Memory Dump** shows only its first page (0000-0107H) in debug_window and cannot be scrolled from the tools,
  so the interrupt vector table at 0:0144H is not readable that way.
- **74HC4078** (8-input gate): the Proteus symbol looks like an OR gate, but its Y output simulates as NOR.
  Check the polarity with the live logic levels (screenshot_part) before relying on it.
- **8251 framing error:** the log shows `[RECV/SEND] Framing error detected!` once on the first typed character
  of every run; the data still arrives correctly.
- **Multi-gate devices** (74LS32, 7427 ...): schematic_place_devices places one gate per request; ask for the
  device four times to get U4:A..U4:D in one package, or copy a gate with schematic_add_part (new package).
  The generic single-gate OR / AND / NOT devices avoid that.
- **Memory Contents** popups (62256, 27C128) show 2CH bytes per row: count offsets from the row label.
- **Firmware project that lists no files** (2024 lab 5 `proteus_sablon.pdsprj`: `FIRMWARE/8086.XML` has an
  empty `<FILES ATTACH="1"/>`): Proteus ignores a stored Debug.exe and uses the 8086's stale PROGRAM path
  ("Can't load ..."). firmware_build fills in the file list the way the lab's solution has it; Proteus then
  builds its own copy and logs "Program Loaded" (checked 2026-11-17).
- **Templates without a firmware project** (other 2024 `proteus_sablon.pdsprj`): the 8086 still points at a stale
  temp path. Proteus links programs through ROOT.CDB, which only its own dialogs may change; a changed
  property is saved into ROOT.DSN but not ROOT.CDB, so it lasts one Proteus session. firmware_build keeps the
  EXE beside the project (<name>_8086.exe) and proteus_launch sets the 8086's Program File to it through the
  Edit Component dialog every time. Removing ROOT.CDB makes Proteus crash at load. Changing an annotated
  part's property only in ROOT.DSN was seen to crash the load here (2024 template) and to be ignored in the
  2025 templates (see Schematic edits below); either way it does not work, and the tools refuse it.
- **Crash-report prompt:** after a Proteus crash, the next start asks ("Reports Uploading") whether to send
  reports to Labcenter; the tools always answer No.
- **Simulation Log** is a separate top-level popup in some projects (2024 templates), docked in others.
- **Virtual Terminal:** raw bytes show as characters; `terminal_mode("Hex Display Mode")` switches to hex.
- **8086 model:** internal memory 0x00000-0x0FFFF (DBG_MEM_SIZE); the program loads at 0800:0000, so the
  interrupt vector table at 0000:0000 is writable RAM. Keep a stack inside your own segment.

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
  on the splash screen. So offline property/value edits of annotated parts are refused by the tools; unannotated
  parts (resistors, buttons) can be changed (verified: a resistor's value).
- Proteus itself updates ROOT.CDB, but only when it compiles the design: Edit Component then Save writes the new
  value to ROOT.DSN alone (a fresh start still uses the old one), while Edit Component, a short simulation run,
  then Save writes both (verified 2026-11-16: 8086 CLOCK 1MHz -> 4MHz; a 2024 template's Program File).
  `proteus_edit_component(save=True)` and `project_export` do this.
- Proteus 8.6 may refuse to open project paths with non-ASCII characters (`tasarım.pdsprj`); project_open
  copies to an ASCII name in the workspace.
- Terminal labels (measured at 300 DPI, 2026-11-16): Proteus draws the label where the record's label xy says.
  The label transform `000001000800` right-aligns the text on that point (text left of an up/down terminal's
  circle, the default) and `000000000800` left-aligns it (text to the right). A down-facing terminal draws its
  text just above the label point, an up-facing one just below. Capitals are about 0.58 grid wide, 0.8 grid tall.
- Export Bitmap (File menu, command 22) renders the sheet at 100 to 600 DPI. Its dialog ids: Current Sheet 514,
  DPI radios 1024 to 1028, Display colours 1539, Output To File 3584 (unchecked = to the clipboard).
  `sheet_image` uses it and restores the clipboard's text afterwards.

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
