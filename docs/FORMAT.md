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

Proteus also writes junctions holding only one or two wires; used slots come first, zeros last.

**First-holder rule** (held for all 6128 junction wires in the samples, `scripts/probe_junctions.py`): a wire's
`points[0]` lies at whichever of its two holders comes first in the file (object order, then pointer slot);
`points[-1]` at the other. Proteus enforces it on load: a reversed wire is drawn with diagonal lines from each
holder to the wrong end. Edits that rewrite a wire keep this orientation.

### Telling records apart

The kind byte is ambiguous: `0x01` is a junction and also the flag byte of a bus wire. Wires are recognised
by structure instead: flag byte 0 or 1, u32 style length L, and the style ending in `WIRE\0` (`BUS WIRE\0`)
at offset +1+L. Wire style lengths seen: 0x1d (wire), 0x21 (bus wire).

Other kinds seen but not decoded yet (2026-11-15):

| Kind | Where | What it seems to be |
| --- | --- | --- |
| `0xBB` | lab6 (all), 2024 lab6/7, LordofNights13 lab6/lab8 | Generator: `$DCLOCK` device, label `CLOCK` (8251 clock source) |
| `0x20` | lab7, odev2 | 2D graphic (box/line with colour `c8 c8 aa`): border, title block, note boxes |
| `0x38` | 2024 lab8 | unknown |

### Ordering rule

A wire is referenced by the two objects at its ends. The wire record is written right after the object that
holds the *second* reference to it. When one object completes several wires they follow in pointer order.

## ROOT.CDB

Starts with a small header and `ROOT` / `Master Sheet`, then pin names, parts (reference, device, package)
and their `{KEY=VALUE}` properties as plain text. Annotated parts take their properties and values from here,
not from ROOT.DSN. Proteus 8.6 hangs on load when this file is rewritten, so the tools never change it:
property and value edits are limited to parts without a CDB record, and parts placed through the GUI get their
record from Proteus's own save.

## Status 2026-11-15 (supersedes the notes above where they differ)

`src/proteus_mcp/dsn.py` parses the object section of all 27 sample files to the real end and rebuilds them
byte for byte (`tests/test_dsn.py`). Records: component, wire, junction, terminal, instrument (generators
`$DCLOCK0`/`$DCGEN` with id `M...`, probes `$VPROBE` with id `P...`; the kind byte varies, so they are
recognised by structure), 2D graphic (kind 0x20, length-prefixed), script block (kind 0x02).

- Properties: the 4th text record holds the visible property text; a u32 full length follows; the full text
  follows only if its length differs from the visible text's length.
- Instruments: kind, xy, transform, u16, pstr id, pstr device, 2 text records, property rule as above,
  u16 flags, u32 wire pointer, 4 bytes; generators then add u8, u32 n, n-4 bytes.
- Sheet directory (8.6): u16 count, per sheet u32, **u8** name length + name (`CCT000`, `__DEFAULT__`), u16,
  u32 sheet offset. Its own offset is the u32 just before the root sheet signature. Later sheets (template
  sheet with frame and title block) move when the root sheet grows; sheets without any `WIRE\0` are copied
  as is (kind 0x21 records there are not decoded).
- Wire order: each wire follows the object holding its second reference, in that object's pointer order.
  Reproduces all two-ended wires in the samples. Wires referenced once (rare) stay where they were.
- Transforms (components, pins, terminals): i16 angle in tenths of a degree + u16 mirror (1 = x, 2 = y).
- Pin tables (`devices.py`): found in the embedded library by device name; record = 4 flag bytes, i32 x, i32 y,
  i16 angle, u16 mirror, C strings glyph / name / number; connection point = anchor + glyph length along the
  pin direction (`$PINSHORT` 254000, others 508000). Pin order = pointer slot order.
- Terminal connection points (from origin): `$TERDEFAULT` +1 grid x (mirrored: -1), `$TERBUS` same,
  `$TERPOWER` 1 grid down, `$TERGROUND` 1 grid up.
- Unannotated parts: Proteus's netlist calls them `DEVICE#ID`, where ID is the record's identity in hex.
- ROOT.CDB: not needed for edits; Proteus rebuilds what it needs on load (verified with added wires and
  terminals).
