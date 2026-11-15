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
- [ ] Find out what EXE the 8086 model accepts. Compare a MASM 5.10 EXE with the shipped `Debug.exe` (which
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
- [ ] `project.py`: open a `.pdsprj`, list components (ref, device, value, properties, origin), pins with
      names, numbers and positions, terminals, wires with labels, junctions.
- [ ] Netlist: group pins into nets from wire geometry, junctions, wire labels, terminal names and bus
      labels (`AD[0..15]` expands to `AD0` ... `AD15`). Cross-check against the lab solutions: the 8255 in
      2025 lab5 `mert_guler` must show `PA0..PA7` going to the display and `PB0..PB7` to the buttons.
- [ ] Decide whether to parse `ROOT.CDB` for anything. It lists pin names per part in plain text and may be
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
- [~] **First end-to-end proof:** (firmware attach answered: replacing Debug.exe in the ZIP works, verified
      by the load size in the log. The display check fails on this install; see Needs the owner.) open the 2025 lab5 solution (`labs/2025/lab5/mert_guler/tasarım.pdsprj`,
      copied to `work/`), rebuild its `main.asm` with the milestone 1 builder, attach the EXE, start the
      simulation, press a button, screenshot, and see the right digit on the 7-segment display.
      This also answers how firmware is attached in 8.6: test whether replacing
      `FIRMWARE/<name>/Debug/Debug.exe` inside the ZIP is enough, or the `{PROGRAM=...}` property must be set
      to an absolute path of an EXE on disk (it exists in both `ROOT.DSN` and `ROOT.CDB`), or the Edit
      Component dialog must be used. Pick the method that works and document it.
- [x] Netlist from Proteus itself (Netlist Compiler, logical mode) as the connectivity reference: `netlist`,
      `net_of` tools. This replaces rebuilding nets from DSN geometry for verification.
- [ ] Interaction: press and release a button, toggle a switch, set a potentiometer, type into a Virtual
      Terminal. Clicking needs sheet coordinate to screen coordinate mapping: derive it from the editing
      window's view (zoom-to-fit, then the known sheet extents), and confirm with a click on a known part.
- [ ] Commit.

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
- [ ] Then: a wire stub from a pin with a wire label; a wire onto an existing wire with a junction dot; a
      ground and a power terminal; a labelled terminal; a bus entry (wire from a pin to a bus with a label).
- [ ] Change a component property (for example `PROGRAM`, `CLOCK`, the ROM image `FILE` of a 27C128) and a
      component value. Rename a terminal or wire label (lab7 asks for the `YA1` to `YA6` labels to be renamed).
- [ ] Add a component that is already present in the file by cloning its record at a new position with a new
      reference and identity (a resistor, a NOT gate). For devices not yet in the file the definition must be
      copied from `LIBRARY\*.LIB`; do this only if the labs need it (lab6 and odev2 allow extra logic gates).
- [ ] Delete a component or a wire.

**Route B: drive the editor.** Only for operations A cannot do reliably. Wire mode clicks at computed pin
positions, the label tool, the pick-device dialog (`P`), typed dialogs. After every GUI edit, save, re-read
the file with the milestone 2 reader and confirm the netlist changed as intended.

- [ ] Whichever route: every edit function re-reads the result and returns the before and after nets for the
      pins it touched. An edit that does not produce the requested connection is reported as failed.
- [ ] Commit.

## Milestone 5: the MCP server

- [x] `server.py` with the official `mcp` SDK (FastMCP, stdio). Tools return compact JSON; screenshots are
      returned as image content. Tool descriptions say what the tool verifies and what it does not.
- [ ] Tools (adjust names as the design settles):
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
- [ ] Config snippets in the README for Claude Code (`claude mcp add` / `.mcp.json`) and Claude Desktop
      (`claude_desktop_config.json`), using the venv interpreter and `-X utf8`.
- [x] Smoke test script: start the server over stdio, list tools, call `proteus_doctor`.
- [ ] Commit.

## Milestone 6: lab acceptance

For each lab: start from the template only, follow the PDF, use only the MCP tools (drive them through a
script or a real MCP client), then run the check. Keep the produced project in `work/` and a screenshot of
the passing result in `docs/evidence/`.

- [ ] 2025 lab5: each of the 8 buttons shows its digit on the 7-segment display.
- [ ] 2025 lab6: 8251 at I/O 0158H; `A B C 0` gives `DEF`, `K L 0` gives `NO`, `A 1 B ? C n 0` gives `DEF`
      on terminal 2. Remember `SHR AL,1` after reading the 8251 data port.
- [ ] 2025 lab7 part 1: DAC0830 at 200H, 100 Hz 32-step sine on the scope.
- [ ] 2025 lab8: 8259 vectors 51H and 52H, five ADC samples printed as hex bytes on the terminal.
- [ ] 2025 odev2: ROM at 20000H, RAM at 60000H, `00.bin` ... `11.bin` loaded, RAM00 / RAM01 contents match
      the sample output in the PDF.
- [ ] 2025 lab7 part 2 and the 2024 labs if time allows.
- [ ] One circuit that is not from a template, to cover the original "draw schematics" brief: for example
      add an 8255 and LEDs to a board that has only the 8086 and latches.

## Milestone 7: wrap up

- [ ] README: replace the planning text with what exists, how to install, how to configure both clients, a
      worked example prompt, and a limitations section that separates verified from unverified.
- [ ] `docs/QUIRKS.md`: model quirks and MASM 5.10 restrictions that Claude must know when writing lab code.
- [ ] Tests that run without Proteus (parser round-trip, netlist on the samples when `labs/` is present,
      firmware build) under `tests/`.
- [ ] Final commit.

## Needs the owner

- **Simulation does not produce any 8086 I/O on this install (blocks every lab check).** Even Labcenter's own
  8086 Demo Board sample shows nothing: the CPU runs, but peripherals never see a read or write. Details and
  evidence in `docs/QUIRKS.md`. Plain TTL logic samples do simulate, so the fault is in the 8086 side
  (`MODELS\i8086.DLL` reports file version 7.5.21171). **Confirmed by the owner on 2026-11-15:** a manual
  run shows the same dark display and log, so this install needs a clean reinstall from a properly licensed
  installer before any lab can be checked by simulation. (Original request, kept for reference:) Please open `labs/2025/lab5/mert_guler/tasarım.pdsprj` by hand, press Play, and
  check whether the 7-segment display shows a digit. If it stays dark, Proteus needs a clean reinstall from a
  properly licensed installer; the MCP cannot fix the install.

- **Proteus missing-symbol warnings on every project load.** Opening any schematic, including Labcenter's own
  8086 sample, shows `Symbol "$MKRORIGIN" used but not found in libraries.` (sometimes also `$MKRLABEL`)
  before the schematic appears. The library folder setting is correct and `SYSTEM.LIB` contains the symbols,
  so this is an install problem (details in `docs/QUIRKS.md`). Please check whether you see it when you open
  a sample from the Start menu yourself; if so, a repair install from your own installer may fix it. The MCP
  reads and records these warnings and stops on anything else. Note: during the investigation on 2026-11-14,
  three of my test launches left such a dialog on screen at the same time before I closed them; that is why
  several appeared at once.
