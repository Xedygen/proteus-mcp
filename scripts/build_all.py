"""Build every FIRMWARE/*/main.asm under an extracted sample folder and report the result per file."""
import glob
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from proteus_mcp.firmware import build  # noqa: E402

base = sys.argv[1]
for src in sorted(glob.glob(os.path.join(base, "*", "FIRMWARE", "*", "main.asm"))):
    tag = os.path.relpath(src, base).split(os.sep)[0]
    r = build(open(src, encoding="latin-1").read())
    shipped = os.path.join(os.path.dirname(src), "Debug", "Debug.exe")
    ship_len = os.path.getsize(shipped) if os.path.exists(shipped) else 0
    print(f"{tag:45} ok={r.ok!s:5} exe={len(r.exe) if r.exe else 0:5} shipped={ship_len:5}")
    for e in r.errors + r.warnings:
        print(f"      line {e.get('line', '-')}: {e['code']} {e['message']}")
