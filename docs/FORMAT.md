# Proteus 8.6 project format notes

Working notes from decoding the lab projects (all stamped `RELEASE="806" FILEVER="814"`). Anything marked
**unverified** is a guess that still needs a test. Update this file as facts are confirmed.

## Container

A `.pdsprj` is a ZIP archive:

| Entry | Role |
| --- | --- |
| `PROJECT.XML` | Version stamp, list of scripts |
| `ROOT.DSN` | Schematic (binary) |
| `ROOT.CDB` | Parts and net database (binary, properties in plain text) |
| `FIRMWARE.XML` | Lists firmware projects; `COMPILER="MASM32"` |
| `FIRMWARE/<name>.XML` | VSM Studio project; `<name>` is `8086` or `8086_` |
| `FIRMWARE/<name>/main.asm` | Source |
| `FIRMWARE/<name>/Debug/Debug.exe` | Built DOS EXE |
| `SCRIPTS/PWRRAILS.DAT`, `SCRIPTS/REQUIRES.DAT` | Power rail config, product requirements |
| `PROJNOTES.PN`, `ROOT.LYT` | Optional notes and PCB layout |

The 8086 part carries `{PROGRAM=<absolute temp path>\Debug\Debug.exe}` in its properties, pointing at a temp
folder on the author's PC. **Unverified:** how Proteus 8.6 resolves that when the project is opened elsewhere
(it probably unpacks the firmware to a new temp folder and rewrites the path, or VSM Studio feeds the model).

## ROOT.DSN layout

```
"ISIS SCHEMATIC FILE\x1a" header (title, author, dates ...)
"DESIGN CONFIGURATION DATA\x1a" styles, colours, fonts
library section: embedded copies of every device and symbol used
u32 pointer to the sheet directory            <- 4 bytes before the first circuit signature
"ISIS CIRCUIT FILE\x1a" + sheet header        <- root sheet
"OBJECT DATA\x00"
  object records ...
0xFF terminator
"ISIS CIRCUIT FILE\x1a" ... "OBJECT DATA\x00" <- second, usually empty, sheet
sheet directory
```

In `2025/lab5/taslak.pdsprj`: circuit signatures at `0x1551d` and `0x1a94d`, object data from `0x1554b`,
directory pointer `0x1a97d` stored at `0x15519`.

**Pointers inside records are absolute file offsets.** Inserting or resizing any record means every later
pointer, the directory pointer and the directory entries must be relocated.

The same structure is documented for Proteus 9 in `chen0416ccc-cpu/Proteus-MCP` (`proteus_mcp/native9.py`,
MIT). The 8.6 differences found so far: string lengths are one byte (v9 uses 0xFF + u32), and a component's
property text is its own u32-length block.

### Units

Coordinates are little-endian signed 32-bit, in units of 10 nm. One 0.1 inch grid step is `254000`.
Example: 8086 `U1` origin `(-11176000, 508000)` = grid `(-44, 2)`.

### Text record

```
0xFF
u8  length, bytes (Latin-1)
i32 x, i32 y
6 bytes  transform / flags
u32 style_length          (counts itself)
style_length - 4 bytes    style: font metrics, "Default Font\0", style name such as "COMPONENT ID\0"
```

### Component (first byte 0x00 or 0x08, second byte 0xFF)

```
u8  kind
text  reference          (style "COMPONENT ID")
text  value              (style "COMPONENT VALUE")
text  subcircuit name    (style "SUBCKT NAME")
text  properties label   (style "PROPERTIES", text usually empty)
u32 length, bytes        property block: "{KEY=VALUE}\n" lines, sometimes padded with "\n"
u16 pin_count
u8  length, bytes        device name
i32 x, i32 y             origin
4 bytes                  transform (rotation / mirror)
u32 identity             unique per component
9 bytes                  unknown, zero so far
pin_count * u32          pointer to the wire attached to each pin, 0 when unconnected
```

Pin order is the device's own pin order (from the embedded library definition), not the pin number.

### Terminal (first byte 0x10)

```
0x10
i32 x, i32 y
4 bytes  transform
u8 length, bytes         "$TERGROUND", "$TERPOWER", and others for input/output/bus terminals
6 bytes  attributes
text                     label (style "TERMINAL LABEL")
u32                      pointer to the attached wire
```

### Wire (appears only where a pointer says so)

```
u8  flags                0 = wire, 1 = bus wire
u32 style_length, style  ends with "WIRE\0" or "BUS WIRE\0"
u16 label_count
label_count * text       wire labels (style "WIRE LABEL")
u16 point_count
point_count * (i32 x, i32 y)
```

### Junction dot (first byte 0x01)

```
0x01
i32 x, i32 y
4 * u32                  pointers to attached wires
```

### Ordering rule

A wire is referenced by the two objects at its ends. The wire record is written right after the object that
holds the *second* reference to it. When one object completes several wires they follow in pointer order.

## What is not decoded yet

`scripts/probe_dsn.py` implements exactly the above. It walks 2 of the 22 sample files to the end
(`2024/lab 5/proteus_sablon`, `2024/lab 7/sinem_sarak`) and stops early on the other 20. The stops are
mis-alignments, so the cause is in the record just before the reported offset. Known suspects:

- wires that carry labels (most stops are near an `AD0`, `AD9` style label);
- bus wires and bus pins, bus entries;
- junctions on buses;
- object kinds not seen yet: 2D graphics and text boxes, probes, generators, virtual instruments,
  sub-circuits, markers;
- components whose kind byte is not 0 or 8.

## ROOT.CDB

Starts with a small header and `ROOT` / `Master Sheet`, then pin names, parts (reference, device, package)
and their `{KEY=VALUE}` properties as plain text. Not decoded.

**Unverified, and it decides the design:** whether Proteus 8.6 rebuilds this database from `ROOT.DSN` when it
loads a project, or trusts the stored copy. The Proteus 9 project found it must be kept consistent and could
also be reset to an empty database (`EMPTY_CDB` in `native9.py`) and regenerated by Proteus.
