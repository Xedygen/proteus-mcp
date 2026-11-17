# proteus-mcp

An MCP server that lets Claude (Opus 5.5) work inside **Proteus 8.6**: draw schematics around ICs such as the
8086, attach firmware, run the simulation and check the result.

The acceptance target is the YTU BLM3061 (Mikroişlemci Sistemleri ve Assembly Dili) lab set: given a lab's
`tanım.pdf` and its `taslak.pdsprj`, Claude should be able to produce a working `.pdsprj` and show that it meets
the "İstenenler" list.

Status: **usable.** Every 2025 Proteus lab and odev2 has been completed through these tools by a fresh Claude
session (below). [TODO.md](TODO.md) records the work and what still needs the owner,
[docs/FORMAT.md](docs/FORMAT.md) the file format notes, [docs/QUIRKS.md](docs/QUIRKS.md) the Proteus and MASM
quirks the tools work around.

## Status

Working and tested through a real MCP client (`scripts/smoke_mcp.py`), on this machine:

- open a lab project into a workspace copy, read and replace its 8086 source;
- build the firmware with MASM 5.10 (headless DOSBox, about 1 s) and store it in the project;
- launch Proteus, run, pause and stop the simulation, read the simulation log, take screenshots of the
  window, of instrument popups and of Debug popups (registers, memory, 8255 status);
- get Proteus's own netlist and ask which pins are on a net;
- **edit schematics** without the GUI: list parts and pins, wire two pins together, attach GND / power /
  named-net terminals, change properties and values. Edits are written straight into the project file and
  were confirmed in Proteus 8.6 (opened without new warnings, connections present in Proteus's netlist);
- place new device types from the libraries, delete and move parts, change annotated parts (8086 clock,
  program file, values) through Proteus's own dialog, and render the sheet as an image to check the layout.

Proteus was reinstalled on 2026-11-15; simulation of the 8086 labs now works (the earlier install could not
drive 8086 I/O, see `docs/QUIRKS.md`).

**Fresh-session test (the real acceptance check):** a new Claude Opus 5.5 session, run headless with only
these tools and the Read tool and given just the lab's PDF path, completed lab5 (all 8 keys show their digit),
lab6 (8251 with a full 0158H decoder; DEF / NO / DEF on terminal 2), odev2 (ROM/RAM map; RAM shows the
PDF's bytes) and lab8 (interrupt-driven ADC samples printed in hex, changing with the pot) on its own, including exporting
the project and reporting how each requirement was checked. With only "do the lab in this PDF" as the prompt,
a session also finished lab7 (LDR-controlled motor speed) with no tool errors. Prompts: `e2e/prompt_*.txt`. The tool problems
those sessions reported are fixed. Run one yourself:

```powershell
cd e2e
claude -p (Get-Content prompt_lab5.txt -Raw) --model claude-opus-5-5 --mcp-config mcp.json --strict-mcp-config `
  --allowedTools "mcp__proteus__*" "Read" --disallowedTools advisor
```

**Worked example:** `scripts/lab5_example.py` completes the 2025 lab5 template (8255 + 7-segment) entirely
through the MCP tools: address wiring, eight added series resistors, port A to the display, port B to the
buttons with pull-ups, a MASM 5.10 program, then 21 connectivity checks against Proteus's own netlist, all
passing, and the 8086 loading the new program. It then runs the simulation and presses the eight buttons;
the display shows 0 to 7 (`docs/evidence/lab5_buttons.png`).

`scripts/lab6_example.py` does lab6 (8251 USART): a full address decoder for 0158H/015AH built from the 74154,
two 74HC4078 and four NOT gates, the 8251 and terminal wiring, and the program. Typing the PDF's three
scenarios into terminal 1 prints `DEFNODEF` on terminal 2 (`docs/evidence/lab6_terminals.png`).

`scripts/lab7_example.py` does lab7 part 1 (DAC0830 at 200H, 100 Hz 32-sample sine): renames the YA1..YA6
placeholders, grounds the ADC pins the template leaves open, writes the program. Netlist checks pass and the
simulation runs cleanly. The DAC was checked with the template's voltmeter and a slowed copy of the program:
it follows the sine between 0 and 12 V (`docs/evidence/lab7_dac_slow.png`); the 100 Hz rate comes from the
cycle count. That was before the scope could be used (see Limitations); today the scope shows it directly.

`scripts/lab8_example.py` does lab8 (8259 + ADC0804 + 8251) starting from the bare template: replaces the
static POT with an interactive POT-HG, builds the 74154 decoder, wires 8259, 8251, ADC, terminal and two
9600 Hz clock generators, and writes an interrupt-driven program (vectors 51H/52H, edge triggered). The
terminal, switched to Hex Display Mode by the tool, shows the samples; turning the pot changes them
(e.g. 80 -> 66 -> 99, `docs/evidence/lab8_pot_terminal.png`).

`scripts/odev2_example.py` does odev2 (64 KB ROM at 20000H, 64 KB RAM at 60000H): two 74LS138 bank decoders
(even on A0, odd on /BHE), an OR gate placed from the library plus three copies and a NOT for the A15 split,
all ROM/RAM address, data and control lines, the ROM images copied with the project, and a program calling
the two FAR routines. Paused, RAM00 shows 34 / CD and RAM01 12 / AB (`docs/evidence/odev2_ram.png`).

`scripts/scratch_example.py` builds a circuit that is in no template. It starts from lab6, deletes its 8251
(an annotated part, so Proteus itself deletes it and the leftover terminals are tidied), places an 8255A and
a LED-RED from the libraries, copies the LED seven times, decodes the 8255 at 20H..26H with the 74154 and
writes 0A5H to port A. LEDs PA0, PA2, PA5 and PA7 light (`docs/evidence/scratch_8255_leds.png`).

Wires are routed by a grid maze router (`route.py`): it never passes through a part body, pin, wire end,
junction or terminal, never runs along an existing wire, and only crosses wires at right angles. Junction
taps, wire deletion and moving wired parts were checked in Proteus with `scripts/smoke_junction.py` (its own
netlist shows the intended nets; the file opens without a dialog).

## Limitations

Verified on this machine (Proteus 8.6 SP2, build 23525) unless marked otherwise.

- **Scope:** the Digital Oscilloscope works through the tools (2026-11-17): a 1 kHz generator on channel A
  of the 2025 lab7 solution draws its square wave and `screenshot(window="Oscilloscope")` captures it
  (`docs/evidence/scope_1khz.png`). The scope's settings are part properties (`CHAACDC`, `CHAVAL`,
  `HORIZVAL` ...) set with `schematic_set_property` while Proteus is closed; a saved design may have its
  channels switched off.
- **Bus wires** are not drawn between pins. Buses are joined by named terminals (`AD0` joins the `AD[0..7]`
  line) and wire labels, which is how the templates do it.
- **Annotated parts** (U1, R3, ...) keep their properties in ROOT.CDB, which only Proteus may write: offline
  edits are refused, and `proteus_edit_component(save=True)` changes them through Proteus instead.
- **New device types** are placed through the Pick Devices dialog with the real mouse; button presses, the
  pot and terminal hex mode use it too. The user should not move the mouse while these run.
- **Firmware:** 8086 programs only (MASM 5.10 in DOSBox). PIC/AVR HEX firmware is not handled (unverified,
  no lab needs it).
- **Generators** are left out of Proteus's netlist (Proteus's behaviour); `net_of` adds them from the sheet.
- Only Proteus 8.6 project files (`FILEVER` 814) have been decoded. Other versions are untested; there is no
  explicit version check, so an edit on such a file may fail with a parse error.
- The plain assembly labs (1 to 4, odev1) are out of scope: they have no Proteus project.

## Install

Requirements: Windows, Proteus 8.6 SP2, DOSBox 0.74-3 (for the assembler), and
[uv](https://docs.astral.sh/uv/). MASM 5.10 and LINK ship inside the package.

The server runs straight from the folder with `uvx`; no venv to manage. Check the setup first:

```powershell
uvx --from D:\Projects\proteus-mcp proteus-mcp doctor
```

Every line should say `True` except `labs_found` (see below). Once the project is on GitHub the same works
with `--from git+https://github.com/<owner>/proteus-mcp`. After changing the code, add `--reinstall` once
so uvx rebuilds its cached copy.

Where things live when installed:

| What | Default | Override |
| --- | --- | --- |
| Proteus | `C:\Program Files (x86)\Labcenter Electronics\Proteus 8 Professional` | `PROTEUS_DIR` |
| DOSBox | `C:\Program Files (x86)\DOSBox-0.74-3\DOSBox.exe` | `DOSBOX_EXE` |
| Workspace (project copies) | `%LOCALAPPDATA%\proteus-mcp\work` | `PROTEUS_MCP_WORK` |
| Lab folders for `lab_list` | `%LOCALAPPDATA%\proteus-mcp\labs` | `PROTEUS_MCP_LABS` |

Run from a source checkout (`python -m proteus_mcp`), the workspace and labs are `work\` and `labs\` in the
repo instead. `lab_list` is a convenience; `project_open` takes any `.pdsprj` path.

Proteus runs visibly on the desktop. `sim_click`, `terminal_mode` and `schematic_place_devices` move the
real mouse, so leave it alone while they run. (`PROTEUS_MCP_BACKGROUND=1` hides Proteus off-screen; it was a
development aid, and the oscilloscope does not draw there.)

## Connect a client

Claude Code (add `--scope user` to have it in every project):

```powershell
claude mcp add proteus -- uvx --from D:\Projects\proteus-mcp proteus-mcp
```

Claude Desktop, in `%APPDATA%\Claude\claude_desktop_config.json`, then restart Claude Desktop:

```json
{
  "mcpServers": {
    "proteus": {
      "command": "uvx",
      "args": ["--from", "D:\Projects\proteus-mcp", "proteus-mcp"],
      "env": { "PROTEUS_MCP_LABS": "D:\Projects\proteus-mcp\labs" }
    }
  }
}
```

If Claude Desktop cannot find `uvx`, use its full path (`where uvx`, usually
`C:\Users\<you>\.local\bin\uvx.exe`). The first start downloads the dependencies and takes a few seconds.

## Tools

| Tool | What it does |
| --- | --- |
| `proteus_doctor` | Install, versions, DOSBox and MASM, running Proteus processes |
| `quirks` | Rules for code that builds and simulates here (MASM 5.10 limits, model quirks) |
| `project_open`, `project_info`, `project_export` | Workspace copies, one folder per project under `work/projects/<name>/` (with the ROM images etc. it refers to); originals are never touched |
| `firmware_get_source`, `firmware_build` | Read `main.asm`; assemble and link (optionally with new source), store EXE in the project; errors with line numbers |
| `proteus_launch`, `proteus_close` | One Proteus instance at a time, only one this server started |
| `sim_start`, `sim_pause`, `sim_stop`, `sim_status`, `sim_log` | `sim_start` also checks that the 8086 loaded the EXE stored in the project |
| `screenshot`, `debug_window` | Main window or a named window; Debug-menu popups while paused (memory popups scrolled to an `address`) |
| `sheet_image` | The whole sheet rendered by Proteus's Export Bitmap at 100 to 600 DPI (sharp at any zoom), optionally cropped and saved |
| `debug_memory` | A memory popup (8086 Memory Dump, a RAM/ROM part's Memory Contents) as hex rows from any address, through the popup's own Copy All (checked on odev2: RAM00 34/CD) |
| `sim_click`, `screenshot_part` | Press a button / toggle a switch during a run (real mouse, guarded); close-up of one part |
| `terminal_type`, `terminal_screenshot` | Type into Virtual Terminals during a run; capture what they show |
| `library_search` | Offline search of the installed device libraries (about 55,000 devices: name, library, category, description), e.g. "4-to-16 decoder" |
| `schematic_place_devices` | Place new device types from the Proteus libraries (GUI, then saved by Proteus); unknown names, spots outside the sheet border or on an existing part or wire are refused before Proteus starts |
| `proteus_background` | Development aid, off by default: hides Proteus off-screen (mouse-based tools then refuse) |
| `netlist`, `net_of` | Proteus's own connectivity (Netlist Compiler, logical mode) |
| `schematic_components` | Parts, values, properties, pins with grid positions and connection state (unannotated parts as `RES#0010`, the netlist's names) |
| `schematic_connect` | Orthogonal wire between two unconnected pins, e.g. `U2.Q1` to `U3.A0`; if one of them is already wired it taps that net instead |
| `schematic_tap` | Wire a free pin onto an existing net (a wired pin, terminal or wire label) with a junction dot |
| `schematic_delete_wire` | Delete the wire on a pin or at a point; a junction left with two wires is merged away |
| `schematic_add_part` | Place a copy of a part already in the design (e.g. a resistor or a 74HC373) with its own reference, optionally rotated or mirrored |
| `schematic_add_terminal`, `schematic_add_terminals` | GND, power or named-net terminal on a pin (`AD0` joins the AD bus line). Supply names (`+12V`, `-5V`, `VEE`, `V+`) become a labelled power terminal, so Proteus drives that rail. The plural takes a list |
| `schematic_add_wire_label` | Name a net with a wire label; wires and terminals with the same name are one net (the label is placed clear of pins and of bodies estimated from pin positions; a body that extends past its pins, like the 7-segment display, can still be overlapped, so pass `at_grid` then) |
| `schematic_rename_label` | Rename terminals / wire labels (e.g. lab7's YA1..YA6) |
| `schematic_set_property`, `schematic_set_value` | Properties / value of unannotated parts, offline (annotated ones live in ROOT.CDB: use `proteus_edit_component` with `save=True`) |
| `schematic_add_generator` | Digital clock generator on a pin, e.g. 9600 Hz on the 8251's TxC/RxC |
| `schematic_delete`, `schematic_move` | Delete a part or generator with its wires (annotated parts such as an 8251 are deleted by Proteus itself, then the terminals, generators and junctions left at the far ends are tidied); move a part, dragging its wires along (re-routed where needed) |
| `terminal_mode` | Virtual Terminal options during a run, e.g. Hex Display Mode (real mouse) |
| `lab_list` | Lab folders with their PDF, template and reference solutions |
| `schematic_labels` | Offline list of net names (terminals, wire labels) and the pins wired to them |
| `schematic_check` | Offline: unconnected pins per part, terminals whose partner is missing or misspelled, parts outside the sheet border (they simulate but cannot be seen or clicked) |
| `dialog_answer` | Click a button in a Proteus dialog the server does not answer itself |
| `proteus_edit_component` | Change any part's properties or value (annotated too, e.g. the 8086 clock, `VALUE` of a resistor) through Proteus's own dialog, for the current session, or lasting with `save=True` (Proteus compiles once and saves) |

## Use it on a lab PDF

1. Connect the server to Claude Code or Claude Desktop (above) and restart the client; `/mcp` in Claude Code
   or the tools icon in Desktop should list `proteus`.
2. Leave Proteus closed; the server starts its own instance. Mouse-based steps (placing new device types,
   pressing buttons, the pot, terminal hex mode) move the real mouse; leave it alone while they run.
3. Attach the lab's `tanım.pdf` to the chat (Desktop: drag it in; Claude Code: give its path) and ask, e.g.:

   *"Use the proteus tools to do this lab. Find its template with lab_list, read quirks() first, wire what
   the PDF asks for, write and build the 8086 program, simulate it, show me evidence that every item of
   İstenenler works, and export the finished project to D:\Odev\lab8.pdsprj."*

The finished project is a normal `.pdsprj`; open it in Proteus to look at or present it.

## What is in this folder

| Path | Contents |
| --- | --- |
| `README.md` | This file |
| `TODO.md` | Work order by milestone, and things needed from the owner |
| `docs/FORMAT.md` | What is known about the Proteus 8.6 project file format |
| `docs/QUIRKS.md` | Proteus, model and MASM 5.10 quirks (also served by the `quirks` tool) |
| `docs/evidence/` | Screenshots from the worked examples |
| `src/proteus_mcp/` | The server package |
| `tests/` | Tests that run without Proteus (`.venv\Scripts\python.exe -m pytest -q`) |
| `scripts/` | Worked lab examples, smoke tests, lab download and sample extraction |
| `e2e/` | Prompts and client config for the fresh-session acceptance runs |
| `tools/masm/` | MASM 5.10 toolchain for 8086 firmware |
| `labs/2024/`, `labs/2025/` | Lab material pulled from the YTU archive repo (not in git) |

`labs/` is a copy of
[`lablar/`](https://github.com/baselkelziye/YTU_Bilgisayar_Muhendisligi_Arsiv/tree/main/3-1/Mikroi%C5%9Flemci%20Sistemleri%20ve%20Assembly%20Dili/lablar)
from the archive. It holds the assignment PDFs, the instructor templates (`taslak.pdsprj`, `proteus_sablon.pdsprj`)
and past student solutions. Re-create it with `python -X utf8 scripts/pull_labs.py labs get`.

## The target labs

Proteus-based assignments (these are what the MCP must handle):

| Lab | Topic | Parts | What "working" looks like |
| --- | --- | --- | --- |
| 2025 lab5 | 8255 + 7-segment | 8086, 74HC373, 8255A, common-anode 7-seg, 8 buttons, resistors | Pressing button N shows digit N |
| 2025 lab6 | 8251 USART | 8086, 3x 74273, 74154, 8251A, 2 virtual terminals, gates | Type `A B C 0` in terminal 1, terminal 2 prints `DEF`; 8251 decoded at I/O 0158H |
| 2025 lab7 | DAC0830 / ADC0804 | 8086, latches, 74154, DAC0830, op-amp, scope (plus ADC, LDR, motors for part 2) | 100 Hz, 32-step sine on the scope; or motor speed follows the LDR |
| 2025 lab8 | 8259 + ADC0804 + 8251 | 8086, 3x 74273, 74154, 8251A, 8259, ADC0804, pot, virtual terminal | Five ADC samples printed as hex bytes, interrupt driven (vectors 51H, 52H) |
| 2025 odev2 | Memory map and address decoding | 8086, 3x 74273, 2x 74LS138, 4x 27C128, 2x 62256 | ROM at 20000H, RAM at 60000H, RAM00/RAM01 show the bytes written by the ROM routines |
| 2024 lab 5 to 8 | Same family of topics | Templates and solutions included | Extra test cases; lab 5 (8255 password, 7-segment) and lab 8 (DAC waveforms) done by fresh sessions |

Labs 1 to 4 and odev1 (both years) ship only `.asm` / `.cpp` files and no Proteus project. They are plain
assembly exercises with no circuit, so they are out of scope (decided in [TODO.md](TODO.md));
`firmware_build` still assembles them.

Two things stand out across all of them:

1. Every Proteus lab starts from a template where the parts are already placed. The work is **wiring, net labels,
   address decoding logic, part properties and the assembly program**. Drawing from a blank sheet is rarely needed.
2. The result is checked by **looking**: a 7-segment digit, virtual terminal text, a scope trace, a memory window.
   The MCP needs a way to read those back, not only to start the simulation.

## What this machine has

| Item | Finding |
| --- | --- |
| Proteus | 8.6 SP2, build 8.6.23525, at `C:\Program Files (x86)\Labcenter Electronics\Proteus 8 Professional` |
| 8086 model | Present (`MODELS\i8086.DLL`), plus the `VSM for 8086` demo board sample |
| Device libraries | 162 `.LIB` files in `LIBRARY\` |
| 8086 assembler | **MASM 5.10** (`MASM.EXE`, `LINK.EXE`, `debug.exe`) in `D:\Projects\2627G_BLM3061_MSAD\lab2` and `lab3` (same set in `C:\masm`). These are 16-bit DOS programs, so they run through DOSBox |
| DOSBox | 0.74-3 at `C:\Program Files (x86)\DOSBox-0.74-3` |
| Proteus's own Build button | Will not work: it expects MASM32 (`ml.exe` + `link16.exe`) in `C:\masm32` or `%PROGRAMFILES%\8086 Compilers Bundle`, and neither exists. The MCP builds outside Proteus instead |
| Python / uv / Node | Python 3.10.11, `uv`, Node are installed |

## How a Proteus 8.6 project is stored

A `.pdsprj` is a ZIP archive. Lab 5's template contains:

| Entry | Format | Role |
| --- | --- | --- |
| `PROJECT.XML` | XML | Version stamp (`RELEASE="806" FILEVER="814"`) |
| `ROOT.DSN` | Binary (`ISIS SCHEMATIC FILE`) | The drawing: parts, wires, labels, positions |
| `ROOT.CDB` | Binary with plain-text properties | Parts database: pins, nets, properties such as `{PROGRAM=...}`, `{CLOCK=1MHz}`, `{LOAD_SEG=0x0000}` |
| `FIRMWARE.XML`, `FIRMWARE/8086.XML` | XML | VSM Studio firmware project (compiler MASM32) |
| `FIRMWARE/8086/main.asm` | Text | The assembly source |
| `FIRMWARE/8086/Debug/Debug.exe` | DOS EXE | The built firmware the 8086 model loads |

So the firmware half is easy to edit from outside Proteus (text and XML in a ZIP). The schematic half is a binary
format with no public specification, and `ROOT.DSN` and `ROOT.CDB` must stay consistent with each other.

Proteus 8.6 has no official automation API.

## Prior art

| Repo | How it works | Why it was not used as is |
| --- | --- | --- |
| [chen0416ccc-cpu/Proteus-MCP](https://github.com/chen0416ccc-cpu/Proteus-MCP) | Writes `.pdsprj` files directly, live edits through Frida | Proteus 9 containers only (`FILEVER` 900 to 907), bridge pinned to build 9.0.40482, no 8086 firmware. Its DSN/CDB notes were the starting point for decoding 8.6 (credited in `dsn.py`) |
| [jackmaikr4-sketch/keil-proteus-automation-skill-mcp](https://github.com/jackmaikr4-sketch/keil-proteus-automation-skill-mcp) | Drives the Proteus window through UI Automation | Cannot draw schematics; its menu-driven simulation control and Edit Component approach carried over (no code copied) |

## How it works

Python MCP server over stdio (official `mcp` SDK, managed with `uv`), Windows only. Three layers:

1. **Project files, offline.** A `.pdsprj` is opened as a ZIP in a workspace copy; `dsn.py` parses and rebuilds
   ROOT.DSN byte for byte, `schematic.py` edits it (wires from a grid maze router, terminals, labels, part
   copies, generators) and `netlist.py` reads Proteus's own netlist. Templates are never written.
2. **Firmware.** The 8086 source is assembled with the course's MASM 5.10 in headless DOSBox and the DOS EXE
   is stored where the project's firmware entry expects it.
3. **Proteus GUI.** `gui.py` starts its own Proteus instance and drives
   it through menu commands and dialogs: simulation control, logs, Export Bitmap sheet images, Debug popups
   read as text, Edit Component, Pick Devices, button presses and terminal typing. Unknown dialogs are
   reported, never clicked through.

Results are checked against Proteus itself: its netlist for connectivity, its simulation log for the loaded
program, and images or popup text for what the circuit does.
