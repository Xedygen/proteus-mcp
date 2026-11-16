# proteus-mcp

An MCP server that lets Claude (Opus 5.5) work inside **Proteus 8.6**: draw schematics around ICs such as the
8086, attach firmware, run the simulation and check the result.

The acceptance target is the YTU BLM3061 (Mikroişlemci Sistemleri ve Assembly Dili) lab set: given a lab's
`tanım.pdf` and its `taslak.pdsprj`, Claude should be able to produce a working `.pdsprj` and show that it meets
the "İstenenler" list.

Status: **in progress.** [TODO.md](TODO.md) is the work order, [docs/FORMAT.md](docs/FORMAT.md) the file
format notes.

## Status

Working and tested through a real MCP client (`scripts/smoke_mcp.py`), on this machine:

- open a lab project into a workspace copy, read and replace its 8086 source;
- build the firmware with MASM 5.10 (headless DOSBox, about 1 s) and store it in the project;
- launch Proteus, run, pause and stop the simulation, read the simulation log, take screenshots of the
  window, of instrument popups and of Debug popups (registers, memory, 8255 status);
- get Proteus's own netlist and ask which pins are on a net;
- **edit schematics** without the GUI: list parts and pins, wire two pins together, attach GND / power /
  named-net terminals, change properties and values. Edits are written straight into the project file and
  were confirmed in Proteus 8.6 (opened without new warnings, connections present in Proteus's netlist).

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
simulation runs cleanly. The scope draws nothing on this install (not even for the student's solution), so
the DAC was checked with the template's voltmeter and a slowed copy of the program: it follows the sine
between 0 and 12 V (`docs/evidence/lab7_dac_slow.png`); the 100 Hz rate comes from the cycle count.

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

Not built yet: editing annotated parts' properties/values (ROOT.CDB), tidy layout of crowded terminals,
bus wires drawn between pins.

## Install

Requirements: Windows, Proteus 8.6 SP2, DOSBox 0.74-3, Python 3.10+, `uv`.

```powershell
cd D:\Projects\proteus-mcp
uv venv .venv
uv pip install -e . --python .venv\Scripts\python.exe
.venv\Scripts\python.exe -m proteus_mcp doctor
```

Paths can be overridden with `PROTEUS_DIR`, `DOSBOX_EXE`, `PROTEUS_MCP_MASM` and `PROTEUS_MCP_WORK`.
`PROTEUS_MCP_BACKGROUND=0` starts with background mode off (the `proteus_background` tool switches it at run
time; MCP clients pass only a few environment variables, so set it in the client config's `env` block).

## Connect a client

Claude Code:

```powershell
claude mcp add proteus -- D:\Projects\proteus-mcp\.venv\Scripts\python.exe -X utf8 -m proteus_mcp serve
```

Claude Desktop, in `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "proteus": {
      "command": "D:\\Projects\\proteus-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-X", "utf8", "-m", "proteus_mcp", "serve"]
    }
  }
}
```

## Tools

| Tool | What it does |
| --- | --- |
| `proteus_doctor` | Install, versions, DOSBox and MASM, running Proteus processes |
| `quirks` | Rules for code that builds and simulates here (MASM 5.10 limits, model quirks) |
| `project_open`, `project_info`, `project_export` | Workspace copies under `work/projects`; originals are never touched |
| `firmware_get_source`, `firmware_build` | Read `main.asm`; assemble and link (optionally with new source), store EXE in the project; errors with line numbers |
| `proteus_launch`, `proteus_close` | One Proteus instance at a time, only one this server started |
| `sim_start`, `sim_pause`, `sim_stop`, `sim_status`, `sim_log` | `sim_start` also checks that the 8086 loaded the EXE stored in the project |
| `screenshot`, `debug_window` | Main window or a named window; Debug-menu popups while paused |
| `sim_click`, `screenshot_part` | Press a button / toggle a switch during a run (real mouse, guarded); close-up of one part |
| `terminal_type`, `terminal_screenshot` | Type into Virtual Terminals during a run; capture what they show |
| `schematic_place_devices` | Place new device types from the Proteus libraries (GUI, then saved by Proteus); spots outside the sheet border or on an existing part or wire are refused before Proteus starts |
| `proteus_background` | Background mode (default): Proteus runs off-screen without taking focus; mouse-based tools pause |
| `netlist`, `net_of` | Proteus's own connectivity (Netlist Compiler, logical mode) |
| `schematic_components` | Parts, values, properties, pins with grid positions and connection state (unannotated parts as `RES#0010`, the netlist's names) |
| `schematic_connect` | Orthogonal wire between two unconnected pins, e.g. `U2.Q1` to `U3.A0`; if one of them is already wired it taps that net instead |
| `schematic_tap` | Wire a free pin onto an existing net (a wired pin, terminal or wire label) with a junction dot |
| `schematic_delete_wire` | Delete the wire on a pin or at a point; a junction left with two wires is merged away |
| `schematic_add_part` | Place a copy of an unannotated part already in the design (e.g. a resistor) with its own reference |
| `schematic_add_terminal`, `schematic_add_terminals` | GND, power or named-net terminal on a pin (`AD0` joins the AD bus line). Supply names (`+12V`, `-5V`, `VEE`, `V+`) become a labelled power terminal, so Proteus drives that rail. The plural takes a list |
| `schematic_add_wire_label` | Name a net with a wire label; wires and terminals with the same name are one net (the label is placed clear of pins and of bodies estimated from pin positions; a body that extends past its pins, like the 7-segment display, can still be overlapped, so pass `at_grid` then) |
| `schematic_rename_label` | Rename terminals / wire labels (e.g. lab7's YA1..YA6) |
| `schematic_set_property`, `schematic_set_value` | Properties / value of unannotated parts (annotated ones live in ROOT.CDB, refused) |
| `schematic_add_generator` | Digital clock generator on a pin, e.g. 9600 Hz on the 8251's TxC/RxC |
| `schematic_delete`, `schematic_move` | Delete a part or generator with its wires (annotated parts such as an 8251 are deleted by Proteus itself, then the terminals, generators and junctions left at the far ends are tidied); move a part, dragging its wires along (re-routed where needed) |
| `terminal_mode` | Virtual Terminal options during a run, e.g. Hex Display Mode (real mouse) |
| `lab_list` | Lab folders with their PDF, template and reference solutions |
| `schematic_labels` | Offline list of net names (terminals, wire labels) and the pins wired to them |
| `schematic_check` | Offline: unconnected pins per part, terminals whose partner is missing or misspelled, parts outside the sheet border (they simulate but cannot be seen or clicked) |
| `dialog_answer` | Click a button in a Proteus dialog the server does not answer itself |
| `proteus_edit_component` | Change any part's properties (annotated too, e.g. the 8086 clock) through Proteus's own dialog, for the current session |

## Use it on a lab PDF

1. Connect the server to Claude Code or Claude Desktop (above) and restart the client; `/mcp` in Claude Code
   or the tools icon in Desktop should list `proteus`.
2. Leave Proteus closed; the server starts its own instance. Mouse-based steps (placing new device types,
   pressing buttons, the pot, terminal hex mode) move the real mouse, so either allow that
   ("you may use the mouse", the model then calls `proteus_background(false)`) or keep the default background
   mode and accept that those steps are skipped.
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
| `src/proteus_mcp/` | The server package |
| `scripts/` | Lab download, sample extraction, format exploration |
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
| 2024 lab 5 to 8 | Same family of topics | Templates and solutions included | Useful as extra test cases |

Labs 1 to 4 and odev1 (both years) ship only `.asm` / `.cpp` files and no Proteus project. They are plain
assembly exercises. Whether the MCP should cover them is an open decision in [TODO.md](TODO.md).

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

## Existing Proteus MCPs on GitHub

| Repo | How it works | Usable for 8.6? |
| --- | --- | --- |
| [chen0416ccc-cpu/Proteus-MCP](https://github.com/chen0416ccc-cpu/Proteus-MCP) | Python. Semantic circuit model, writes `.pdsprj` files directly, and injects into the running process with Frida for live edits and simulation | **Not as is.** File writing accepts only Proteus 9 containers (`FILEVER` 900 to 907). The in-process bridge is pinned to one build, 9.0.40482, by module hash and refuses anything else. Firmware support is Keil C51 only, no 8086. Needs Python 3.11+ |
| [jackmaikr4-sketch/keil-proteus-automation-skill-mcp](https://github.com/jackmaikr4-sketch/keil-proteus-automation-skill-mcp) | TypeScript + PowerShell. Drives the Proteus window through Windows UI Automation: finds menu commands by name, reads enabled state to tell running / paused / stopped, sets part properties through the Edit Component dialog, takes screenshots | **Approach carries over.** It does not depend on the Proteus build. It cannot draw schematics |
| [3023073486/agent-Proteus](https://github.com/3023073486/agent-Proteus) | Thin wrapper that forwards to other desktop automation backends | Little to reuse |

Conclusion: nothing can be dropped in for 8.6. The plan is to build our own server and borrow designs:

- from chen's project: the semantic model, the `.LIB` library reader, the "write, then read back and verify"
  discipline, and its documentation of the DSN/CDB layout as a starting point for decoding the 8.x format;
- from the keil project: menu-driven simulation control and dialog handling.

Both declare an MIT licence. Confirm that before copying any code, and keep attribution if code is reused.

## Design

Python MCP server over stdio (official `mcp` SDK, managed with `uv`), Windows only.

Three layers:

1. **Project file layer (offline, no GUI).** Open a `.pdsprj` as a ZIP. Read parts, pins, nets and properties.
   Replace `main.asm` and the built firmware. Edit part properties. Always work on a copy, never the template.
2. **Schematic editing layer.** Add wires, net labels, terminals and parts. This is the hard part and has two
   candidate routes, to be settled by a spike (TODO phase 1):
   - *Route A, write `ROOT.DSN` / `ROOT.CDB` directly.* Fast, repeatable, no screen needed. Requires decoding the
     8.6 binary format. The 22 sample projects in `labs/` (template and solution pairs) are the decoding corpus.
   - *Route B, drive the ISIS window.* Keyboard and mouse automation against the real editor. No format work,
     but slower, sensitive to window state, and it takes over the desktop while it runs.
   Net labels and terminals are preferred over long drawn wires either way, since they make connections by name.
3. **Simulation and observation layer (GUI).** Start, pause, stop through menu commands. Press buttons and type
   into virtual terminals. Read back results with screenshots (Claude reads images), the simulation log, and where
   possible text from popup windows (virtual terminal, memory contents).

Firmware build: assemble the 8086 source outside Proteus with the course's MASM 5.10, run inside DOSBox without
a visible prompt (mount a build folder, run `masm` and `link` with output redirected to a log, exit), then place
the resulting DOS EXE where the project expects it. The 8086 model loads DOS EXE files, which is also what the
lab templates ship. One thing to watch: the templates were written for MASM32's `ml.exe` (MASM 6), so a source
file may occasionally use syntax MASM 5.10 rejects. The firmware spike in TODO checks this on real lab code.

The same DOSBox + `debug.exe` pair can run and inspect the plain assembly labs (lab 1 to 4) if those are in scope.

### Original tool plan (see Tools above for what exists)

| Group | Tools |
| --- | --- |
| Environment | `proteus_doctor` (install, version, assembler, running instances) |
| Library | `library_search`, `device_info` (pins, default properties) |
| Project | `project_open`, `project_copy`, `project_summary`, `netlist_read`, `project_save_as` |
| Schematic | `component_add`, `component_set_property`, `wire_connect`, `label_add`, `terminal_add`, `component_delete`, `schematic_screenshot` |
| Firmware | `firmware_set_source`, `firmware_build`, `firmware_attach` |
| Simulation | `sim_start`, `sim_pause`, `sim_stop`, `sim_status`, `sim_log` |
| Interaction | `button_press`, `terminal_type`, `pot_set` |
| Observation | `screenshot`, `terminal_read`, `memory_read`, `probe_read` |
| Verification | `net_check` (is pin X on net Y), `erc_run` |

## Known risks

- **The schematic format is undocumented.** If Route A proves too costly, the project falls back to Route B and
  becomes slower and less reliable.
- **Reading results depends on the screen.** Scope traces and 7-segment displays can only be checked from
  screenshots. Virtual terminal and memory windows may or may not expose their text.
- **GUI automation needs the desktop.** While a simulation run is being driven, the mouse and keyboard are in use.
- **Simulation is not real time.** The 8086 model runs slower than wall clock, so timing checks (100 Hz sine,
  9600 baud) need simulated time, not elapsed time.
- **Model quirks.** The lab sheets already mention one: after `IN AL, DATA_PORT` on the 8251, run `SHR AL, 1`.
  Notes like this need to live somewhere Claude will see them.
