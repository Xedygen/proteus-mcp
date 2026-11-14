"""Build 8086 firmware with MASM 5.10 + LINK inside DOSBox, without showing a window.

The source is copied to a fresh temp folder as X.ASM (8.3 name), MASM.EXE and LINK.EXE are copied next to it,
and DOSBox runs a generated conf whose autoexec assembles, links and exits.  Output of both tools is redirected
to log files, which are parsed for errors.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import paths

CONF = """\
[sdl]
fullscreen=false
output=surface
autolock=false
waitonerror=false
[dosbox]
machine=svga_s3
memsize=16
[render]
frameskip=10
[cpu]
core=auto
cputype=auto
cycles=max
[mixer]
nosound=true
[midi]
mpu401=none
mididevice=none
[sblaster]
sbtype=none
[gus]
gus=false
[speaker]
pcspeaker=false
tandy=off
disney=false
[joystick]
joysticktype=none
[serial]
serial1=disabled
serial2=disabled
serial3=disabled
serial4=disabled
[dos]
xms=true
ems=true
umb=true
[autoexec]
@echo off
mount c "{dir}"
c:
MASM {masm_opts} X.ASM,X.OBJ,X.LST,NUL; > MASM.LOG
LINK {link_opts} X.OBJ,X.EXE,NUL,; > LINK.LOG
echo done > DONE.FLG
exit
"""

_ERR = re.compile(r"^(?P<file>[^\s(]+)\((?P<line>\d+)\)\s*:\s*(?P<kind>error|warning)\s+(?P<code>\w+)\s*:?\s*(?P<msg>.*)$",
                  re.IGNORECASE)
_LINKMSG = re.compile(r"^(?:LINK\s*:\s*)?(?P<kind>error|warning|fatal error)\s+(?P<code>L\d+)\s*:?\s*(?P<msg>.*)$",
                      re.IGNORECASE)


@dataclass
class BuildResult:
    ok: bool
    exe: bytes | None
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    masm_log: str = ""
    link_log: str = ""
    listing: str = ""
    seconds: float = 0.0

    def summary(self) -> str:
        lines = [f"ok={self.ok} exe_bytes={len(self.exe) if self.exe else 0} time={self.seconds:.1f}s"]
        for e in self.errors:
            lines.append(f"  error   line {e.get('line', '?')}: {e['code']} {e['message']}")
        for w in self.warnings:
            lines.append(f"  warning line {w.get('line', '?')}: {w['code']} {w['message']}")
        return "\n".join(lines)

    def as_dict(self, with_logs: bool = False) -> dict:
        d = {"ok": self.ok, "exe_bytes": len(self.exe) if self.exe else 0, "errors": self.errors,
             "warnings": self.warnings, "seconds": round(self.seconds, 1)}
        if with_logs or not self.ok:
            d["masm_log"], d["link_log"] = self.masm_log, self.link_log
        return d


def normalise_source(text: str) -> bytes:
    """CRLF line ends, cp437-ish bytes (unencodable characters, e.g. in comments, become '?')."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.endswith("\n"):
        text += "\n"
    return text.replace("\n", "\r\n").encode("cp437", errors="replace")


def parse_log(log: str) -> tuple[list[dict], list[dict]]:
    errors, warnings = [], []
    for raw in log.splitlines():
        s = raw.strip()
        m = _ERR.match(s)
        if m:
            item = {"line": int(m["line"]), "code": m["code"], "message": m["msg"].strip()}
            (errors if m["kind"].lower() == "error" else warnings).append(item)
            continue
        m = _LINKMSG.match(s)
        if m:
            item = {"code": m["code"], "message": m["msg"].strip()}
            (warnings if m["kind"].lower() == "warning" else errors).append(item)
    return errors, warnings


def build(source: str, *, debug_info: bool = False, timeout: float = 60.0, keep_dir: str | None = None) -> BuildResult:
    """Assemble and link one source file.  Returns the EXE bytes on success.

    debug_info=True passes /Zi to MASM and /CO to LINK (CodeView info, like the shipped Debug.exe).
    """
    if not paths.DOSBOX_EXE.is_file():
        raise FileNotFoundError(f"DOSBox not found at {paths.DOSBOX_EXE}")
    t0 = time.monotonic()
    tmp = (Path(keep_dir) if keep_dir else Path(tempfile.mkdtemp(prefix="pmcp_asm_"))).resolve()
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        for exe in ("MASM.EXE", "LINK.EXE"):
            shutil.copy2(paths.MASM_DIR / exe, tmp / exe)
        (tmp / "X.ASM").write_bytes(normalise_source(source))
        conf = CONF.format(dir=str(tmp), masm_opts="/Zi" if debug_info else "",
                           link_opts="/CO" if debug_info else "")
        (tmp / "build.conf").write_text(conf, encoding="ascii")
        env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen([str(paths.DOSBOX_EXE), "-noconsole", "-conf", str(tmp / "build.conf")],
                                cwd=str(tmp), env=env, creationflags=flags,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            return BuildResult(False, None, [{"code": "TIMEOUT", "message": f"DOSBox did not finish in {timeout}s"}],
                               seconds=time.monotonic() - t0)
        rd = lambda n: (tmp / n).read_text("cp437", errors="replace") if (tmp / n).exists() else ""
        masm_log, link_log, listing = rd("MASM.LOG"), rd("LINK.LOG"), rd("X.LST")
        errors, warnings = parse_log(masm_log)
        le, lw = parse_log(link_log)
        if errors:  # MASM failed, so LINK's "object not found" is only a consequence
            le = [e for e in le if e["code"] != "L1093"]
        errors += le
        warnings += lw
        exe = (tmp / "X.EXE").read_bytes() if (tmp / "X.EXE").exists() else None
        if exe is not None and len(exe) == 0:
            exe = None
            errors.append({"code": "EMPTY", "message": "linker produced an empty EXE (no code or no END <label>)"})
        if not (tmp / "DONE.FLG").exists():
            errors.append({"code": "DOSBOX", "message": "build script did not run to the end"})
        ok = exe is not None and not errors
        return BuildResult(ok, exe if ok else None, errors, warnings, masm_log, link_log, listing,
                           time.monotonic() - t0)
    finally:
        if not keep_dir:
            shutil.rmtree(tmp, ignore_errors=True)
