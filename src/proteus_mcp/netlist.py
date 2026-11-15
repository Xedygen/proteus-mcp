"""Parse the SDF netlist that Proteus's own Netlist Compiler writes (Tool > Netlist Compiler, format SDF,
mode Logical).  This is Proteus's view of connectivity, so it is the reference for every check.

Logical mode is required: Physical mode silently drops parts that have no reference designator, which in the
lab files includes every resistor, button and display.

Format (ISIS SCHEMATIC DESCRIPTION FORMAT 8.0):

    *PARTLIST,<n>
    <ref>,<device>,<value>,KEY=VALUE,...
    *NETLIST,<n>
    <netname>,<count>[,CLASS=POWER]
    <ref>,<pin type>,<pin name>        one line per member, blank line between nets

Unannotated parts appear as `<device>#<id>` (for example `RES#001D`).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Part:
    ref: str
    device: str
    value: str
    properties: dict[str, str] = field(default_factory=dict)


@dataclass
class Netlist:
    parts: dict[str, Part]
    nets: dict[str, list[tuple[str, str]]]   # net name -> [(ref, pin name)]; power nets include rails
    power: set[str]

    def net_of(self, ref: str, pin: str) -> str | None:
        want = (ref.upper(), _norm(pin))
        for name, members in self.nets.items():
            if any((r.upper(), _norm(p)) == want for r, p in members):
                return name
        return None

    def connected(self, a: tuple[str, str], b: tuple[str, str]) -> bool:
        na = self.net_of(*a)
        return na is not None and na == self.net_of(*b)

    def pins_of(self, ref: str) -> dict[str, str]:
        """pin name -> net name for one part."""
        out = {}
        for name, members in self.nets.items():
            for r, p in members:
                if r.upper() == ref.upper():
                    out[p] = name
        return out

    def summary(self, include_single: bool = False) -> dict:
        nets = {n: [f"{r}.{p}" for r, p in m] for n, m in self.nets.items()
                if include_single or len(m) > 1 or n in self.power}
        return {"parts": {r: {"device": p.device, "value": p.value} for r, p in self.parts.items()},
                "nets": nets}


def _norm(pin: str) -> str:
    """Pin names use $...$ for overbars (`$WR$/$LOCK$`); compare without them, case-insensitive."""
    return pin.replace("$", "").upper()


def _split_props(fields: list[str]) -> dict[str, str]:
    props = {}
    for f in fields:
        if "=" in f:
            k, v = f.split("=", 1)
            props[k] = v
    return props


def parse(text: str) -> Netlist:
    if "*PARTLIST" not in text or "*NETLIST" not in text:
        raise ValueError("not an SDF netlist")
    parts: dict[str, Part] = {}
    part_block = text.split("*PARTLIST", 1)[1].split("*NETLIST", 1)[0]
    for line in part_block.splitlines()[1:]:
        line = line.strip()
        if not line or line.startswith("*"):
            continue
        f = line.split(",")
        # Values may contain commas inside quotes; the first three fields never do.
        parts[f[0]] = Part(f[0], f[1] if len(f) > 1 else "", f[2] if len(f) > 2 else "", _split_props(f[3:]))
    nets: dict[str, list[tuple[str, str]]] = {}
    power: set[str] = set()
    body = text.split("*NETLIST", 1)[1].split("\n", 1)[1]
    for block in body.replace("\r\n", "\n").split("\n\n"):
        lines = [l.strip() for l in block.strip().splitlines() if l.strip()]
        if not lines or lines[0].startswith("*"):
            continue
        head = lines[0].split(",")
        name = head[0]
        if "CLASS=POWER" in lines[0]:
            power.add(name)
        members = []
        for l in lines[1:]:
            f = l.split(",")
            if len(f) >= 3:
                members.append((f[0], ",".join(f[2:])))
            # two-field lines are the net's own label/rail entries (e.g. "AD0,GT", "GND,PR")
        nets[name] = members
    return Netlist(parts, nets, power)
