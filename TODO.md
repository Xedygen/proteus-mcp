# TODO: work order

Goal: a working MCP server for **Proteus 8.6** that lets Claude take a lab's `tanım.pdf` plus its template
`.pdsprj` and produce a project that simulates correctly. "Working" means demonstrated on this machine, not
just written.

Read first: [README.md](README.md) (scope, machine, design), [docs/FORMAT.md](docs/FORMAT.md) (what is known
about the file format), this file.

## Decisions already made (owner)

- Scope is the Proteus labs only: 2025 lab5, lab6, lab7, lab8, odev2, with 2024 lab 5 to 8 as extra cases.
  The plain assembly labs (1 to 4, odev1) are out of scope.
- Assembler: MASM 5.10 in `tools/masm/` (`MASM.EXE`, `LINK.EXE`), run through DOSBox 0.74-3 at
  `C:\Program Files (x86)\DOSBox-0.74-3\DOSBox.exe`.
- The MCP must work in both Claude Code and Claude Desktop (stdio server, config for each).
- The MCP may control the desktop (launch Proteus, send keys and clicks, take screenshots). If a step truly
  cannot be automated, say so; the owner will help by hand.
- `labs/` stays out of git.
- Python. `uv` and Python 3.10.11 are installed. Do not require a newer Python unless unavoidable.

## Rules while working

- Never modify anything under `labs/`. Copy a project into `work/` (git-ignored) and edit the copy.
- Only touch Proteus processes this work started. Do not kill or take over a Proteus the owner has open.
- Never click through a dialog you do not recognise. Screenshot it, read it, decide.
- No licence workarounds, no patching Proteus binaries.
- Verify by observation. "Saved without error" is not proof; reopen the file in Proteus and look.
- Commit at each milestone: `<type>: <description>` (feat, fix, refactor, docs, test, chore). No AI
  attribution or Co-Authored-By lines. Do not push; there is no remote yet.
- Remove debug prints before a commit. Keep `docs/FORMAT.md` and the README limitations honest and current.
- If blocked on something only the owner can do, write it under "Needs the owner" at the bottom and carry on
  with whatever else can be done.

## Milestone 0: environment (small)

- [x] `pyproject.toml` with a `src/proteus_mcp` package, `uv` venv in `.venv`, dependencies: `mcp`, plus
      whatever is needed for Windows automation and screenshots (`pywinauto` or `pywin32`, `pillow`).
- [x] `python -m proteus_mcp doctor` prints: Proteus path and build, MASM and DOSBox found, Python version.
- [x] Extract the samples for analysis: `python -X utf8 scripts/extract_labs.py labs work/x`.

## Milestone 1: firmware build (independent of everything else)

- [x] `firmware.py`: assemble and link an `.asm` with MASM 5.10 inside DOSBox, headless. Copy the source to a
      temp folder under an 8.3 name, write a DOSBox conf (mount, `MASM X.ASM,X.OBJ,X.LST,NUL; > MASM.LOG`,
      `LINK X.OBJ,X.EXE,NUL,,NUL; > LINK.LOG`, `exit`), run with `-noconsole` and `SDL_VIDEODRIVER=dummy`
      so no window appears, with a timeout. Return success, the EXE bytes, and parsed errors (file, line,
      message).
- [x] Test: build every solution `main.asm` under `work/x/*/FIRMWARE/*/main.asm`. Record which fail and why.
      The sources were written for MASM32 `ml.exe` (MASM 6); note syntax that MASM 5.10 rejects in
      `docs/QUIRKS.md` so Claude knows what to avoid.
- [x] Find out what EXE the 8086 model accepts. (MASM 5.10 EXEs load and run; CodeView info only adds source debugging.) Compare a MASM 5.10 EXE with the shipped `Debug.exe` (which
      was linked with CodeView info). Real proof comes in milestone 3.

## Milestone 2: read a project

- [x] Finish the `ROOT.DSN` object walker (`scripts/probe_dsn.py` is the starting point, `docs/FORMAT.md` the
      notes). Target: all 22 samples walk to the 0xFF terminator with zero unknown records. Method: when a
      file stops, hex-dump around the record *before* the stop and compare with the same construct in a file
      that parses. Template and solution pairs differ by only a few objects, which isolates new record types.
      The Proteus 9 reader in `chen0416ccc-cpu/Proteus-MCP` (`proteus_mcp/native9.py`, MIT, attribution
      required if code is reused) describes the sibling format.
- [x] Round-trip test: parse then re-serialise every sample and compare bytes. Must be identical.
- [x] Decode pin positions. Each component's pins come from the device definition embedded earlier in the
      file (`$PINDEFAULT`, `$PINSHORT`, `$PINBUS` ... with `$MKRORIGIN` / `$MKRNODE`). Needed: for a component
      reference and pin name or number, the pin end coordinates on the sheet after rotation and mirror.
      Cross-check against existing wires: a wire attached to pin N must start or end exactly on that point.
- [x] (done as `schematic_components`) `project.py`: open a `.pdsprj`, list components (ref, device, value, properties, origin), pins with
      names, numbers and positions, terminals, wires with labels, junctions.
- [x] (replaced by Proteus's own netlist, `netlist`/`net_of`) Netlist: group pins into nets from wire geometry, junctions, wire labels, terminal names and bus
      labels (`AD[0..15]` expands to `AD0` ... `AD15`). Cross-check against the lab solutions: the 8255 in
      2025 lab5 `mert_guler` must show `PA0..PA7` going to the display and `PB0..PB7` to the buttons.
- [x] Decide whether to parse `ROOT.CDB` for anything. (Read only: Proteus hangs on any rewritten CDB; annotated parts' values/properties come from it.) It lists pin names per part in plain text and may be
      an easier source for pin names than the DSN.

## Milestone 3: GUI control (can start in parallel with 2)

- [x] `gui.py`: launch `PDS.EXE <project>` and track that process id. Detect the main window and its title.
      Handle the start-up dialogs that appear on this machine (record them in `docs/QUIRKS.md`).
- [x] Find how to reach commands. Try in order: Win32 menu command ids via UI Automation (the approach in
      `jackmaikr4-sketch/keil-proteus-automation-skill-mcp`, `src/proteus.ts` and `scripts/desktop.ps1`),
      then keyboard shortcuts (F12 run, Pause, Shift+Pause stop, as configured in this install), then clicks
      on the transport buttons at the bottom left.
- [x] `sim_start`, `sim_pause`, `sim_stop`, `sim_status`, and a screenshot of the Proteus window returned as
      an image the model can look at. Screenshot of a named popup window (Virtual Terminal, Digital
      Oscilloscope, Memory Contents) as well.
- [x] Read the simulation log / message window as text if it is reachable; otherwise screenshot it.
- [x] **First end-to-end proof:** (firmware attach answered: replacing Debug.exe in the ZIP works, verified
      by the load size in the log. The display check fails on this install; see Needs the owner.) open the 2025 lab5 solution (`labs/2025/lab5/mert_guler/tasarım.pdsprj`,
      copied to `work/`), rebuild its `main.asm` with the milestone 1 builder, attach the EXE, start the
      simulation, press a button, screenshot, and see the right digit on the 7-segment display.
      This also answers how firmware is attached in 8.6: test whether replacing
      `FIRMWARE/<name>/Debug/Debug.exe` inside the ZIP is enough, or the `{PROGRAM=...}` property must be set
      to an absolute path of an EXE on disk (it exists in both `ROOT.DSN` and `ROOT.CDB`), or the Edit
      Component dialog must be used. Pick the method that works and document it.
- [x] Netlist from Proteus itself (Netlist Compiler, logical mode) as the connectivity reference: `netlist`,
      `net_of` tools. This replaces rebuilding nets from DSN geometry for verification.
- [x] Interaction: press and release a button (sim_click), type into Virtual Terminals (terminal_type)., toggle a switch, set a potentiometer, type into a Virtual
      Terminal. Clicking needs sheet coordinate to screen coordinate mapping: derive it from the editing
      window's view (zoom-to-fit, then the known sheet extents), and confirm with a click on a known part.
- [x] Commit.

## Milestone 4: edit a schematic

Status 2026-11-15: Route A works. All 27 samples parse and rebuild byte for byte; pin positions verified on
3213 pins; wires and terminals written into the lab5 template are accepted by Proteus and appear in its
netlist; ROOT.CDB can stay untouched. Still open below: bus entries, cloning/adding parts, deleting.

Two routes. Try A first; it is deterministic and needs no screen. Fall back to B for whatever A cannot do.

**Route A: write `ROOT.DSN` directly.**

- [x] Smallest experiment first: take a template, add one two-point wire between two pins that are both
      unconnected, relocate every pointer, write the ZIP, open it in Proteus 8.6. Does it load, show the
      wire, and does the netlist (Tools, Netlist Compiler, or just the simulation) see the connection?
- [x] Same experiment for `ROOT.CDB`: leave it untouched, then try removing or emptying it, and see which
      Proteus accepts. This decides whether the database must be written too.
- [x] (terminals incl. GND/power/named/bus, junction taps onto existing wires) Then: a wire stub from a pin with a wire label; a wire onto an existing wire with a junction dot; a
      ground and a power terminal; a labelled terminal; a bus entry (wire from a pin to a bus with a label).
- [~] (unannotated parts only; annotated parts need ROOT.CDB, refused) Change a component property (for example `PROGRAM`, `CLOCK`, the ROM image `FILE` of a 27C128) and a
      component value. Rename a terminal or wire label (lab7 asks for the `YA1` to `YA6` labels to be renamed).
- [x] (add_part; new device types via schematic_place_devices, needs the mouse) Add a component that is already present in the file by cloning its record at a new position with a new
      reference and identity (a resistor, a NOT gate). For devices not yet in the file the definition must be
      copied from `LIBRARY\*.LIB`; do this only if the labs need it (lab6 and odev2 allow extra logic gates).
- [x] (schematic_delete, schematic_delete_wire) Delete a component or a wire.

**Route B: drive the editor.** Only for operations A cannot do reliably. Wire mode clicks at computed pin
positions, the label tool, the pick-device dialog (`P`), typed dialogs. After every GUI edit, save, re-read
the file with the milestone 2 reader and confirm the netlist changed as intended.

- [x] (every edit re-parses its output; netlist/net_of verify in Proteus) Whichever route: every edit function re-reads the result and returns the before and after nets for the
      pins it touched. An edit that does not produce the requested connection is reported as failed.
- [x] Commit.

## Milestone 5: the MCP server

- [x] `server.py` with the official `mcp` SDK (FastMCP, stdio). Tools return compact JSON; screenshots are
      returned as image content. Tool descriptions say what the tool verifies and what it does not.
- [x] (see README tool table) Tools (adjust names as the design settles):
      - `proteus_doctor`
      - `project_open(path)` copies into the workspace and returns a handle; `project_save_as`
      - `project_summary`, `component_info(ref)` (pins, positions, nets, properties), `netlist`, `net_of(ref, pin)`
      - `connect(a, b, via="label"|"wire")`, `label_pin`, `add_terminal`, `rename_label`, `set_property`,
        `set_value`, `add_component`, `delete`
      - `firmware_set_source(text)`, `firmware_build` (errors with line numbers), `firmware_attach`
      - `rom_load(ref, binfile)` for odev2
      - `sim_start`, `sim_pause`, `sim_stop`, `sim_status`, `sim_log`
      - `press_button(ref)`, `set_switch`, `set_pot(ref, percent)`, `terminal_type(ref, text)`
      - `screenshot(target)` for the sheet or a named instrument window; `terminal_read`, `memory_read` if
        text is reachable
      - `lab_read(pdf)` is not needed: Claude reads PDFs itself. Offer `quirks()` returning `docs/QUIRKS.md`.
- [x] Config snippets in the README for Claude Code (`claude mcp add` / `.mcp.json`) and Claude Desktop
      (`claude_desktop_config.json`), using the venv interpreter and `-X utf8`.
- [x] Smoke test script: start the server over stdio, list tools, call `proteus_doctor`.
- [x] Commit.

## Milestone 6: lab acceptance

For each lab: start from the template only, follow the PDF, use only the MCP tools (drive them through a
script or a real MCP client), then run the check. Keep the produced project in `work/` and a screenshot of
the passing result in `docs/evidence/`.

- [x] 2025 lab5: each of the 8 buttons shows its digit on the 7-segment display (scripts/lab5_example.py,
      docs/evidence/lab5_buttons.png).
- [x] 2025 lab6 (scripts/lab6_example.py, docs/evidence/lab6_terminals.png: DEFNODEF): 8251 at I/O 0158H; `A B C 0` gives `DEF`, `K L 0` gives `NO`, `A 1 B ? C n 0` gives `DEF`
      on terminal 2. Remember `SHR AL,1` after reading the 8251 data port.
- [x] 2025 lab7 part 1 (scripts/lab7_example.py): labels renamed, DAC at 200H verified in the netlist, program
      runs without simulation errors; a slowed copy shows the sine on the voltmeter (docs/evidence/lab7_dac_slow.png).
      The scope draws nothing on this install, not even for the student solution.
- [x] 2025 lab8 (scripts/lab8_example.py, docs/evidence/lab8_pot_terminal.png): 8259 vectors 51H and 52H, samples printed as hex, pot turns change them.
- [x] 2025 odev2 (scripts/odev2_example.py, docs/evidence/odev2_ram.png): ROM at 20000H, RAM at 60000H, `00.bin` ... `11.bin` loaded, RAM00 / RAM01 contents match
      the sample output in the PDF.
- [x] 2025 lab7 part 2 (fresh minimal-prompt session, see below). The 2024 labs if time allows.
- [x] One circuit that is not from a template (scripts/scratch_example.py, docs/evidence/scratch_8255_leds.png):
      lab6's 8251 deleted, an 8255A and eight LEDs placed from the libraries, 74154 decoding, 0A5H on port A.

## Milestone 7: wrap up

- [~] README: replace the planning text with what exists, how to install, how to configure both clients, a
      worked example prompt, and a limitations section that separates verified from unverified.
- [x] `docs/QUIRKS.md`: model quirks and MASM 5.10 restrictions that Claude must know when writing lab code.
- [x] (tests/test_dsn.py, 60 tests) Tests that run without Proteus (parser round-trip, netlist on the samples when `labs/` is present,
      firmware build) under `tests/`.
- [ ] Final commit.

## Open work (2026-11-15)

- [x] lab8: decoder, 8259/8251/ADC wiring, interrupt-driven program, 9600 Hz generators, POT-HG, hex terminal
      (`scripts/lab8_example.py`, evidence `docs/evidence/lab8_pot_terminal.png`).
- [x] Deleting parts (`schematic_delete`), moving unwired parts (`schematic_move`), `lab_list`.
- [x] odev2: `scripts/odev2_example.py`, evidence `docs/evidence/odev2_ram.png`. project_open/export now copy
      files the project refers to (ROM images).
- [x] lab7 part 1: DAC output checked with the voltmeter on a slowed program (scope draws nothing here).
- [x] lab7 part 2: ADC reads the LDR, motor speed follows it (done by the minimal-prompt fresh session below,
      Application 2: motor voltage follows the torch 0 -> 11.9 V).
- [ ] Scope: find out why no trace (timebase 0.5 us/div? display/OpenGL setting?).
- [x] Junction taps (`schematic_tap`, connect falls back to it), deleting single wires
      (`schematic_delete_wire`), moving wired parts, maze router for all new wires (2026-11-16, checked in
      Proteus with `scripts/smoke_junction.py`).
- [x] Wire labels (`schematic_add_wire_label`, checked in Proteus: two nets labelled alike become one) and
      built-in wire / bus wire / terminal label styles, so edits no longer need an existing object to copy
      from (2026-11-16; the styles are byte-identical in all 28 sample files).
- [x] Review items: unknown pin glyphs refused, parts with unreadable pin tables blocked as boxes, junctions
      tidied when a part is deleted, add_part/move refuse pins on wires or bodies, drop-offset correction no
      longer overshoots.
- [x] Layout: crowded terminals overlap (lab5 display pins are half a grid apart; power-terminal labels on pins
      2 grids apart cover the next one). add_terminal now steps a terminal out and, for up/down terminals,
      may put its label on the other side of the circle, scoring label/wire clashes first, then blocking a
      neighbour's lead, then distance; a clash it cannot avoid comes back as a `note`. Checked in Proteus at
      200-300 DPI with the new `sheet_image` (lab5_example: display row, resistor row, pull-ups all clear,
      2026-11-16).
- [ ] 2024 labs as extra cases (one circuit drawn from scratch: done, scripts/scratch_example.py).
- [~] Fresh-session acceptance (headless claude -p, Opus 5.5, only the proteus tools + Read, prompts in
      work/e2e/prompt_*.txt):
      - [x] lab5 (2026-11-16): all 8 keys show their digit; 41 turns after a restart. It found 4 tool problems,
            all fixed (first sim_click lost, docked debug panels, clash message, 8255 log note).
      - [x] lab8 (2026-11-16): 116 turns, about 2.3 USD, 7 min. Pot turns gave 03 87 AB BF FF. Fixed from it:
            placement now corrects Proteus's drop offset and redoes parts dropped on wires; sim_start while
            running. Open: generators absent from the netlist. (Memory Dump paging: done, debug_memory and
            debug_window(address=...), checked live 2026-11-16.)
      - [x] lab6 (2026-11-16): 85 turns, about 1.8 USD, 6 min; DEF / NO / DEF (+ UVW) on terminal 2. Fixed from
            it: wires leave pins before turning and detour round parts; generators listed with frequency.
      - [x] odev2 (2026-11-16): 226 turns, about 2.7 USD, 7.5 min; RAM00 34/CD, RAM01 12/AB. Fixed from it: gate
            copies no longer take the package name (U4 vs U4:A), schematic_add_terminals (batch).
      - [x] Minimal prompt ("do the lab in this PDF" + save path), lab7 (2026-11-16): picked Application 2
            (LDR -> ADC -> DAC -> motors), motor voltage follows the torch 0 -> 11.9 V; 50 turns, 0 tool errors.
      - [x] 2024 lab 8 (minimal prompt; template without a firmware project): DAC square 19.997 Hz and sawtooth
            39.86 Hz measured in simulation. Fixed from it: lab_list finds proteus_sablon templates, programs
            for such templates are attached at launch, crash-report prompt declined, top-level log popup.
            Open: the exported file needs its Program File set once by hand. (screenshot_part came back black
            for parts placed outside the sheet border, beyond the zoomed view: now clipped to the canvas, with a
            clear error when the part is not visible at all.)
      - Note: the advisor server tool hangs headless sessions; run with --disallowedTools advisor.

## Review findings still open (Fable review, 2026-11-15)

Fixed already: readable tool errors, project_open name clash, proteus_close keeping a live session + force,
exit cleanup, set_property backslashes, README Desktop JSON, stale quirks text and server instructions,
place-devices verification, generator identity clash.

- [x] add_generator input checks (name vs net labels, plain-number frequency, distance). Embedded-library
      check for `$DCLOCK0` still open (only matters for files that never held a generator).
- [x] Wiring results echo the resolved pin name and number (74154 "5" = Y5, pin 6).
- [x] net_of errors on unknown parts/pins; netlist compiled once per Proteus session, one SDF per project.
- [x] sim() waits for the requested state. (sim_log still pauses/resumes when the log popup is closed.)
- [x] Turning background mode off brings hidden popups back on screen.
- [x] add_part prefix for multi-gate parts. Rotation: labels of a copy turned 90 degrees against its source
      are moved beside it; `mirror` added (both checked in Proteus 2026-11-16).
- [x] _check_clear misses a corner landing mid-wire; parts with unparsable pin tables are unprotected.
      (New wires come from the maze router, which forbids corners on wires and boxes unreadable parts.)
- [x] dialog_answer, schematic_labels added; ROM images travel with the project.
- [x] Docs: FORMAT.md stale "6 of 27 parse" section and NUL bytes near line 121.
