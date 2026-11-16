"""Drive the Proteus 8.6 window: launch, dialogs, menu commands, simulation state, screenshots, clicks.

Proteus 8.6 has native Win32 menus, so commands are sent as WM_COMMAND with the menu item id, found by the
item's text (the menu-driven idea comes from jackmaikr4-sketch/keil-proteus-automation-skill-mcp, MIT).

Only processes started by this module are ever touched.  Every modal dialog is read; only dialogs on the
KNOWN list are answered automatically, anything else raises UnknownDialog with the dialog text.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import io
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import win32api
import win32con
import win32gui
import win32process
import win32ui
from PIL import Image

from . import paths

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

user32 = ctypes.windll.user32

import os as _os
# Background mode: Proteus starts without activation and is moved off-screen, so it never covers the owner's
# screen.  Everything driven by window messages and window rendering keeps working; real mouse clicks
# (pressing parts, placing library devices) are refused.  PROTEUS_MCP_BACKGROUND=0 turns it off.
BACKGROUND = {"on": _os.environ.get("PROTEUS_MCP_BACKGROUND", "1") != "0"}

# Dialog texts that are answered automatically: (title, regex on the message text, button to press).
KNOWN_DIALOGS = [
    ("Schematic Capture", re.compile(r'^Symbol "(?P<symbol>[^"]+)" used but not found in libraries\.$'), "OK"),
]
_JUNK = ("\x87", "\x93", "\x97", "\x9d", "\xaf", "\xa6")


class GuiError(Exception):
    pass


class UnknownDialog(GuiError):
    def __init__(self, title: str, texts: list[str], buttons: list[str]):
        super().__init__(f"unexpected dialog {title!r}: {texts} buttons={buttons}")
        self.title, self.texts, self.buttons = title, texts, buttons


@dataclass
class MenuItem:
    path: str
    command: int
    enabled: bool
    checked: bool


def _windows(pid: int, visible_only: bool = True) -> list[int]:
    out: list[int] = []

    def cb(h, _):
        if win32process.GetWindowThreadProcessId(h)[1] == pid and (not visible_only or win32gui.IsWindowVisible(h)):
            out.append(h)
    win32gui.EnumWindows(cb, None)
    return out


def _children(h: int) -> list[tuple[int, str, str]]:
    out: list[tuple[int, str, str]] = []
    try:
        win32gui.EnumChildWindows(h, lambda c, _: out.append((c, win32gui.GetClassName(c), win32gui.GetWindowText(c))), None)
    except win32gui.error:
        pass
    return out


def dialog_info(h: int) -> dict:
    kids = _children(h)
    texts = [t for _, c, t in kids if c in ("Static", "Edit") and t and not t.startswith(_JUNK)]
    buttons = [t.replace("&", "") for _, c, t in kids if c == "Button" and t and not t.startswith(_JUNK)]
    return {"hwnd": h, "title": win32gui.GetWindowText(h), "texts": texts, "buttons": buttons}


def _click_button(h: int, label: str, post: bool = False) -> bool:
    """Click a dialog button.  post=True for a button that opens another modal box (Save -> "already
    exists, replace?"): SendMessage would not return until someone answered that box."""
    for c, cls, t in _children(h):
        if cls == "Button" and t.replace("&", "") == label:
            (win32gui.PostMessage if post else win32gui.SendMessage)(c, win32con.BM_CLICK, 0, 0)
            return True
    return False


def capture(hwnd: int) -> Image.Image:
    """Image of a window via PrintWindow (works when the window is covered by others)."""
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    w, h = r - l, b - t
    hdc = win32gui.GetWindowDC(hwnd)
    src = win32ui.CreateDCFromHandle(hdc)
    mem = src.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(src, w, h)
    mem.SelectObject(bmp)
    try:
        user32.PrintWindow(hwnd, mem.GetSafeHdc(), 2)
        info = bmp.GetInfo()
        img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1)
        return img.copy()
    finally:
        win32gui.DeleteObject(bmp.GetHandle())
        mem.DeleteDC()
        src.DeleteDC()
        win32gui.ReleaseDC(hwnd, hdc)


def png_bytes(img: Image.Image, max_width: int | None = None) -> bytes:
    if max_width and img.width > max_width:
        img = img.resize((max_width, round(img.height * max_width / img.width)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


_JOB: list = []


def _tie_to_server(proc: subprocess.Popen) -> None:
    """Put a Proteus we started into a job object that ends it when this server process ends, however the
    server dies (an MCP client restarting it, a crash): no invisible Proteus is left holding a project."""
    try:
        import win32job
        if not _JOB:
            job = win32job.CreateJobObject(None, "")
            info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
            info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
            _JOB.append(job)
        handle = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, proc.pid)
        win32job.AssignProcessToJobObject(_JOB[0], handle)
    except Exception:
        pass          # best effort: the atexit hook in the server still closes it on a normal exit


def _listview_rows(lv: int) -> list[dict]:
    """Message / source rows of a two-column SysListView32 (simulation log, compiler errors)."""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from pywinauto.controls.common_controls import ListViewWrapper
        from pywinauto.controls.hwndwrapper import HwndWrapper
        w = ListViewWrapper(HwndWrapper(lv).element_info)
        return [{"message": w.get_item(i, 0).text(), "source": w.get_item(i, 1).text()}
                for i in range(w.item_count())]


@dataclass
class Session:
    project: Path
    proc: subprocess.Popen
    main: int | None = None
    warnings: list[str] = field(default_factory=list)  # symbols named in known start-up warnings

    @property
    def pid(self) -> int:
        return self.proc.pid

    # ----- process and windows -------------------------------------------------------------
    @classmethod
    def launch(cls, project: str | Path, timeout: float = 90.0) -> "Session":
        project = Path(project).resolve()
        if not project.is_file():
            raise FileNotFoundError(project)
        # Proteus keeps per-project window state in "<project>.<host>.<user>.workspace"; a stale one can
        # reopen the project on another tab or with popups, so start clean.
        for ws in project.parent.glob(project.name + ".*.workspace"):
            ws.unlink(missing_ok=True)
        startup = None
        if BACKGROUND["on"]:
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 4          # SW_SHOWNOACTIVATE
        proc = subprocess.Popen([str(paths.PDS_EXE), str(project)], cwd=str(paths.PDS_EXE.parent),
                                startupinfo=startup)
        _tie_to_server(proc)
        s = cls(project, proc)
        try:
            s.wait_main(timeout)
        except Exception:
            s.kill()
            raise
        return s

    @classmethod
    def attach(cls, pid: int, project: str | Path) -> "Session":
        """Re-attach to a Proteus this package launched earlier (pid recorded by the caller)."""
        class _P:  # minimal Popen stand-in
            def __init__(self, pid):
                self.pid, self.returncode = pid, None
                self._h = win32api.OpenProcess(win32con.SYNCHRONIZE | win32con.PROCESS_TERMINATE
                                               | win32con.PROCESS_QUERY_INFORMATION, False, pid)

            def poll(self):
                import win32event
                if win32event.WaitForSingleObject(self._h, 0) == win32event.WAIT_OBJECT_0:
                    self.returncode = 0
                    return 0
                return None

            def kill(self):
                win32api.TerminateProcess(self._h, 1)

            def wait(self, timeout=None):
                import win32event
                win32event.WaitForSingleObject(self._h, int((timeout or 30) * 1000))
        s = cls(Path(project).resolve(), _P(pid))  # type: ignore[arg-type]
        s.main = next(h for h in _windows(pid) if win32gui.GetClassName(h).startswith("LX_APPLN"))
        return s

    def alive(self) -> bool:
        return self.proc.poll() is None

    def windows(self) -> list[dict]:
        return [{"hwnd": h, "class": win32gui.GetClassName(h), "title": win32gui.GetWindowText(h),
                 "rect": win32gui.GetWindowRect(h)} for h in _windows(self.pid)]

    def dialogs(self) -> list[dict]:
        return [dialog_info(h) for h in _windows(self.pid) if win32gui.GetClassName(h) == "#32770"]

    def handle_dialogs(self, raise_unknown: bool = True) -> list[dict]:
        """Answer known dialogs; raise on the first unknown one.  Returns the unknown ones if not raising."""
        unknown = []
        for d in self.dialogs():
            for title, pat, button in KNOWN_DIALOGS:
                m = pat.match(d["texts"][0]) if d["title"] == title and len(d["texts"]) >= 1 else None
                if m and _click_button(d["hwnd"], button):
                    self.warnings.append(m.groupdict().get("symbol") or d["texts"][0])
                    break
            else:
                if raise_unknown:
                    raise UnknownDialog(d["title"], d["texts"], d["buttons"])
                unknown.append(d)
        return unknown

    def wait_main(self, timeout: float = 90.0) -> int:
        stem = self.project.stem
        t0, quiet = time.monotonic(), 0
        while time.monotonic() - t0 < timeout:
            if not self.alive():
                raise GuiError(f"Proteus exited with code {self.proc.returncode}")
            self.handle_dialogs()
            self._decline_crash_reports()
            mains = [h for h in _windows(self.pid) if win32gui.GetClassName(h).startswith("LX_APPLN")
                     and stem in win32gui.GetWindowText(h)]
            # The title ends with the active tab, which Proteus restores per project (Schematic Capture,
            # Source Code, ...), so any "<stem> - Proteus 8 Professional - <tab>" title counts.
            if mains and " - Proteus 8 Professional - " in win32gui.GetWindowText(mains[0]):
                quiet += 1
                if quiet >= 4:  # stable for 2 s with no dialog
                    self.main = mains[0]
                    if BACKGROUND["on"]:
                        self.hide()
                    self.show_schematic()
                    return self.main
            else:
                quiet = 0
            time.sleep(0.5)
        raise GuiError(f"main window for {stem} did not appear in {timeout}s; windows={self.windows()}")

    def _decline_crash_reports(self) -> None:
        """After a Proteus crash, the next start asks (Qt window "Reports Uploading") whether to upload crash
        reports to Labcenter, and blocks until answered.  Always answer No: nothing is sent anywhere."""
        for h in _windows(self.pid):
            if win32gui.GetWindowText(h) != "Reports Uploading" or win32gui.GetClassName(h) != "QWidget":
                continue
            l, t, r, b = win32gui.GetWindowRect(h)
            # Qt draws its own buttons (no Win32 children); "No" sits at the bottom right of the 344x231 box
            x, y = l + round((r - l) * 0.80), t + round((b - t) * 0.84)
            if not BACKGROUND["on"]:
                try:
                    win32gui.SetForegroundWindow(h)
                except Exception:
                    pass
                win32api.SetCursorPos((x, y))
                time.sleep(0.2)
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                time.sleep(0.08)
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            else:
                # Escape rejects a Qt dialog (= No) and needs no mouse
                win32gui.PostMessage(h, win32con.WM_KEYDOWN, win32con.VK_ESCAPE, 0)
                win32gui.PostMessage(h, win32con.WM_KEYUP, win32con.VK_ESCAPE, 0)
            time.sleep(1.0)
            self.warnings.append("declined Proteus's crash-report upload prompt (an earlier run crashed)")

    def hide(self) -> None:
        """Move the main window off-screen (to the right of all monitors) without activating it.  Its
        rendering, menus and dialogs still work; it simply is not visible."""
        l, t, r, b = win32gui.GetWindowRect(self.main)
        right = user32.GetSystemMetrics(76) + user32.GetSystemMetrics(78)   # virtual screen left + width
        flags = win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER | win32con.SWP_NOSIZE
        win32gui.ShowWindow(self.main, 4)                                   # SW_SHOWNOACTIVATE (un-minimise)
        win32gui.SetWindowPos(self.main, 0, right + 50, t, 0, 0, flags)

    def hide_popups(self) -> None:
        """Background mode: move Proteus's own popup windows (oscilloscope, virtual terminals ...) that are
        on a monitor off-screen as well.  Dialogs are left alone (they are answered, not looked at)."""
        if not BACKGROUND["on"]:
            return
        right = user32.GetSystemMetrics(76) + user32.GetSystemMetrics(78)
        flags = win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER | win32con.SWP_NOSIZE
        for h in _windows(self.pid):
            if h == self.main or win32gui.GetClassName(h) == "#32770":
                continue
            l, t, r, b = win32gui.GetWindowRect(h)
            if l < right:
                win32gui.SetWindowPos(h, 0, right + 60 + (l % 400), t, 0, 0, flags)

    def show_popups(self) -> None:
        """Undo hide_popups: bring popups that sit beyond the right edge of the desktop back on screen."""
        right = user32.GetSystemMetrics(76) + user32.GetSystemMetrics(78)
        flags = win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER | win32con.SWP_NOSIZE
        for i, h in enumerate(_windows(self.pid)):
            if h == self.main or win32gui.GetClassName(h) == "#32770":
                continue
            l, t, r, b = win32gui.GetWindowRect(h)
            if l >= right:
                win32gui.SetWindowPos(h, 0, 120 + 40 * (i % 8), max(t, 80), 0, 0, flags)

    def show_schematic(self, timeout: float = 5.0) -> None:
        """Bring the Schematic Capture tab to the front.  Proteus restores the last tab per project and
        switches to Source Code when a simulation pauses; that tab has no Tool menu."""
        if "Schematic Capture" in win32gui.GetWindowText(self.main):
            return
        # The document tab strip is an untitled Labcenter window (class LX_APPLN_*), about 37 px high,
        # spanning the main window just below the toolbars.  Its first tab is Schematic Capture.
        ml, mt, mr, mb = win32gui.GetWindowRect(self.main)
        strips = []
        for c, cls, t in _children(self.main):
            if win32gui.GetParent(c) != self.main or t or not cls.startswith("LX_APPLN"):
                continue
            if not win32gui.IsWindowVisible(c):
                continue
            l, t_, r, b = win32gui.GetWindowRect(c)
            if 20 <= b - t_ <= 50 and r - l > (mr - ml) // 2:
                strips.append((t_, c))
        if not strips:
            raise GuiError("document tab strip not found")
        bar = min(strips)[1]
        l, t, r, b = win32gui.GetWindowRect(bar)
        lp = win32api.MAKELONG(60, (b - t) // 2)
        win32gui.PostMessage(bar, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lp)
        win32gui.PostMessage(bar, win32con.WM_LBUTTONUP, 0, lp)
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            if "Schematic Capture" in win32gui.GetWindowText(self.main):
                return
            time.sleep(0.2)
        raise GuiError(f"could not switch to Schematic Capture (title {win32gui.GetWindowText(self.main)!r})")

    def kill(self) -> None:
        if self.alive():
            self.proc.kill()
            self.proc.wait(10)

    def close(self, timeout: float = 20.0) -> dict:
        """File > Exit.  A save prompt is answered 'No' only if it is the standard save question."""
        if not self.alive():
            return {"closed": True}
        # the main window can be re-created (seen after Save As), so look it up again
        mains = [h for h in _windows(self.pid) if win32gui.GetClassName(h).startswith("LX_APPLN")]
        if mains:
            self.main = mains[0]
        try:
            self.command(r"^E&xit Application")
        except GuiError:
            win32gui.PostMessage(self.main, win32con.WM_CLOSE, 0, 0)
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            if not self.alive():
                return {"closed": True}
            if not _windows(self.pid):
                # Seen on this machine: after Exit the windows close but PDS.EXE sometimes lingers.
                try:
                    self.proc.wait(15)
                except Exception:
                    pass
                if self.alive():
                    self.kill()
                    return {"closed": True, "note": "windows closed; process lingered and was terminated"}
                return {"closed": True}
            for d in self.dialogs():
                text = " ".join(d["texts"])
                if re.search(r"save changes|Save .*\?", text, re.I) and "No" in d["buttons"]:
                    _click_button(d["hwnd"], "No")
                else:
                    raise UnknownDialog(d["title"], d["texts"], d["buttons"])
            time.sleep(0.5)
        return {"closed": False}

    # ----- menus ---------------------------------------------------------------------------
    def menus(self) -> list[MenuItem]:
        hm = user32.GetMenu(self.main)
        items: list[MenuItem] = []

        def walk(menu, prefix):
            for i in range(user32.GetMenuItemCount(menu)):
                buf = ctypes.create_unicode_buffer(256)
                user32.GetMenuStringW(menu, i, buf, 256, win32con.MF_BYPOSITION)
                state = user32.GetMenuState(menu, i, win32con.MF_BYPOSITION)
                sub = user32.GetSubMenu(menu, i)
                name = buf.value
                if sub:
                    # Proteus refreshes enable state on WM_INITMENUPOPUP, so ask it to before reading.
                    win32gui.SendMessage(self.main, win32con.WM_INITMENUPOPUP, sub, i)
                    walk(sub, prefix + name.replace("&", "") + "/")
                elif name:
                    items.append(MenuItem(prefix + name, user32.GetMenuItemID(menu, i),
                                          not state & (win32con.MF_GRAYED | win32con.MF_DISABLED),
                                          bool(state & win32con.MF_CHECKED)))
        if hm:
            walk(hm, "")
        return items

    def find_menu(self, pattern: str) -> MenuItem:
        rx = re.compile(pattern)
        hits = [m for m in self.menus() if rx.search(m.path.split("/")[-1])]
        if len(hits) != 1:
            raise GuiError(f"menu {pattern!r} matched {[h.path for h in hits]}")
        return hits[0]

    def command(self, pattern: str, require_enabled: bool = True) -> MenuItem:
        m = self.find_menu(pattern)
        if require_enabled and not m.enabled:
            raise GuiError(f"menu {m.path!r} is disabled")
        win32gui.PostMessage(self.main, win32con.WM_COMMAND, m.command, 0)
        return m

    # ----- simulation ----------------------------------------------------------------------
    def sim_state(self) -> str:
        items = {m.path.split("/")[-1].split("\t")[0]: m for m in self.menus()}
        run, pause, stop = (items.get("Run Simulation"), items.get("Pause VSM Debugging"),
                            items.get("Stop VSM Debugging"))
        if not (run and pause and stop):
            return "unknown"
        if stop.enabled and pause.enabled:
            return "running"
        if stop.enabled:
            return "paused"
        return "stopped"

    def sim(self, action: str, settle: float = 1.5) -> str:
        pattern = {"start": r"^Run Simulation\t", "pause": r"^Pause VSM Debugging",
                   "stop": r"^Stop VSM Debugging"}[action]
        self.handle_dialogs()
        self.command(pattern)
        # popups appear shortly after the start; move them off-screen early and again after settling
        for _ in range(4):
            time.sleep(min(settle, 1.0) / 4)
            self.hide_popups()
        time.sleep(max(settle - min(settle, 1.0), 0))
        self.handle_dialogs()
        self.hide_popups()
        # a slow start (netlist compile, big design) or stop can take longer than `settle`: wait for the
        # state the command asks for, a few more seconds at most; a failed start stays "stopped"
        want = {"start": "running", "pause": "paused", "stop": "stopped"}[action]
        deadline = time.monotonic() + 8
        state = self.sim_state()
        while state != want and time.monotonic() < deadline:
            if action == "start" and self.compiler_errors():
                break
            time.sleep(0.4)
            state = self.sim_state()
        return state

    def find_child(self, title: str, cls: str | None = None) -> int | None:
        for c, k, t in _children(self.main):
            if t == title and (cls is None or k == cls):
                return c
        return None

    def _log_window(self) -> int | None:
        """The Simulation Log popup: docked as a child of the main window in most projects, a separate
        top-level window in others (seen in the 2024 templates)."""
        h = self.find_child("Simulation Log")
        if h is None:
            h = next((w for w in _windows(self.pid) if win32gui.GetWindowText(w) == "Simulation Log"), None)
        return h

    def sim_log(self) -> list[dict]:
        """Rows of the Simulation Log.  The log popup only exists while the simulation is paused (or after it
        was opened during a pause); if it is not open, the simulation is paused, the popup opened, and the
        previous state restored."""
        import warnings
        lv_parent = self._log_window()
        resume = False
        if lv_parent is None:
            state = self.sim_state()
            if state == "running":
                self.sim("pause")
                resume = True
            elif state != "paused":
                raise GuiError("simulation log is only reachable while a simulation is running or paused")
            self.command(r"Simulation Log$")
            time.sleep(1.0)
            lv_parent = self._log_window()
        if lv_parent is None:
            raise GuiError("Simulation Log popup not found")
        lv = next((c for c, k, _ in _children(lv_parent) if k == "SysListView32"), None)
        rows = _listview_rows(lv)
        if resume:
            self.sim("start")
        return rows

    def compiler_errors(self) -> list[dict]:
        """Rows of the NETLIST COMPILER ERRORS tab Proteus opens when a simulation fails to start
        (e.g. "Duplicate part reference"), or [] when there is none."""
        for c, k, _ in _children(self.main):
            if k == "SysListView32":
                try:
                    rows = _listview_rows(c)
                except Exception:
                    continue
                if any(k in r["message"].lower() for r in rows for k in ("netlist compiler", "failed to start", "can't load")):
                    return rows
        return []

    # ----- netlist -------------------------------------------------------------------------
    def netlist(self, out_path: str | Path, timeout: float = 30.0) -> Path:
        """Tool > Netlist Compiler, SDF, Logical, whole design, flattened, written to out_path.

        Logical mode keeps unannotated parts (Physical mode drops them).  out_path must not exist: Windows
        names are case-insensitive and an existing file triggers an overwrite prompt.
        """
        out = Path(out_path).resolve()
        if out.exists():
            out.unlink()
        if self.sim_state() != "stopped":
            raise GuiError("stop the simulation before compiling a netlist")
        self.show_schematic()
        self.command(r"^Netlist Compiler")
        d = self._wait_dialog(lambda d: d["title"] == "Netlist Compiler", 10)
        for label in ("File(s)", "SDF", "Logical", "Whole Design", "Flatten"):
            if not _click_button(d["hwnd"], label):
                raise GuiError(f"Netlist Compiler: no {label!r} option")
        _click_button(d["hwnd"], "OK")
        save = self._wait_dialog(lambda d: d["title"].startswith("Compl"), 10)  # Labcenter's own spelling
        edit = next((c for c, cls, _ in _children(save["hwnd"])
                     if cls == "Edit" and win32gui.GetDlgCtrlID(c) == 1148), None)
        if edit is None:
            raise GuiError("netlist save dialog: file name box not found")
        win32gui.SendMessage(edit, win32con.WM_SETTEXT, 0, str(out))
        _click_button(save["hwnd"], "Save")
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            # the output name gets an upper-case .SDF extension; the filesystem does not care
            if out.exists() and out.stat().st_size > 0 and not self.dialogs():
                time.sleep(0.3)
                return out
            self.handle_dialogs()
            time.sleep(0.3)
        raise GuiError(f"netlist was not written to {out}")

    def _wait_dialog(self, pred, timeout: float) -> dict:
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            for d in self.dialogs():
                if pred(d):
                    return d
            time.sleep(0.2)
        raise GuiError(f"expected dialog did not appear; open dialogs: {self.dialogs()}")

    def debug_window(self, name: str) -> int:
        """Open a Debug-menu popup (e.g. '8255 Internal Status', 'Registers - U1', 'Memory Contents', 'Virtual
        Terminal') and return its window handle.  Popups exist only while a simulation is running or paused."""
        rx = re.compile(re.escape(name), re.I)
        hits = [m for m in self.menus() if m.path.startswith("Debug/") and rx.search(m.path)]
        if not hits:
            avail = [m.path.split("/", 1)[1] for m in self.menus() if m.path.startswith("Debug/")]
            raise GuiError(f"no Debug menu item matching {name!r}; available: {avail}")
        win32gui.PostMessage(self.main, win32con.WM_COMMAND, hits[0].command, 0)
        time.sleep(1.0)
        self.hide_popups()
        title = re.sub(r"^&?\d+\.\s*|&", "", hits[0].path.split("/")[-1])
        # docked panels are children titled with the submenu in front ("8086 Registers - U1"); several
        # panels exist hidden from the start, so prefer the visible match
        kids = [(c, t.replace("&", "")) for c, _, t in _children(self.main)]
        for want_visible in (True, False):
            for c, t in kids:
                if (t == title or t.endswith(" " + title)) and \
                        (not want_visible or win32gui.IsWindowVisible(c)):
                    return c
        for h in _windows(self.pid):
            if rx.search(win32gui.GetWindowText(h)):
                return h
        raise GuiError(f"opened {hits[0].path!r} but could not find its window")

    # ----- screenshots and popups ----------------------------------------------------------
    def screenshot(self, title: str | None = None) -> Image.Image:
        if title is None:
            return capture(self.main)
        rx = re.compile(title, re.I)
        for h in _windows(self.pid):
            if rx.search(win32gui.GetWindowText(h)):
                return capture(h)
        raise GuiError(f"no window matching {title!r}; windows={[w['title'] for w in self.windows()]}")

    # ----- editing window geometry and mouse -----------------------------------------------
    def edit_window(self) -> int:
        """The schematic editing canvas: the largest child window of the main window."""
        best, area = None, 0
        for c, cls, _ in _children(self.main):
            if not win32gui.IsWindowVisible(c):
                continue
            l, t, r, b = win32gui.GetWindowRect(c)
            if (r - l) * (b - t) > area and cls.startswith("LX_APPLN"):
                best, area = c, (r - l) * (b - t)
        if not best:
            raise GuiError("editing window not found")
        return best

    def foreground(self) -> None:
        if BACKGROUND["on"]:
            return
        win32gui.ShowWindow(self.main, win32con.SW_RESTORE)
        try:
            # Alt key trick lets SetForegroundWindow succeed from a background process.
            win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
            win32gui.SetForegroundWindow(self.main)
        finally:
            win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.3)

    def mouse(self, x: int, y: int, action: str = "click", hold: float = 0.0) -> None:
        """Real mouse input at screen pixel (x, y): 'click', 'down', 'up', 'move'."""
        self.foreground()
        win32api.SetCursorPos((x, y))
        time.sleep(0.15)
        if action in ("click", "down"):
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        if action == "click":
            time.sleep(max(hold, 0.08))
        if action in ("click", "up"):
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

    def sheet_frame(self, size: tuple[int, int] | None = None) -> tuple[int, int, int, int]:
        """Zoom to the entire sheet and return the sheet border's screen rectangle (left, top, right, bottom).

        The border is the outermost blue (0,0,255-ish) line in the editing window."""
        self.show_schematic()
        self.command(r"^Zoom To View Entire Sheet")
        time.sleep(1.0)
        canvas = self.edit_window()
        l, t, r, b = win32gui.GetWindowRect(canvas)
        img = capture(self.main)
        ml, mt, _, _ = win32gui.GetWindowRect(self.main)
        crop = img.crop((l - ml, t - mt, r - ml, b - mt)).convert("RGB")
        px = crop.load()
        w, h = crop.size
        # the plain sheet border is blue; templates with a drawn frame (e.g. lab7) use black
        blue = lambda x, y: (lambda c: (c[2] > 180 and c[0] < 90 and c[1] < 90) or max(c) < 45)(px[x, y])
        # the border is the only blue line spanning a large part of the view; bus wires and icons are short
        cols = [x for x in range(w) if sum(blue(x, y) for y in range(0, h, 2)) > h * 0.15]
        rows = [y for y in range(h) if sum(blue(x, y) for x in range(0, w, 2)) > w * 0.15]
        if not cols or not rows:
            raise GuiError("sheet border not found on screen")
        left, top, right, bottom = l + min(cols), t + min(rows), l + max(cols), t + max(rows)
        if size:
            # the scanned window also covers the side panels, so the left edge is the least reliable one;
            # derive it from the other three and the sheet's aspect ratio
            left = right - round((bottom - top) * size[0] / size[1])
        return left, top, right, bottom

    def sheet_to_screen(self, frame, size, point) -> tuple[int, int]:
        """Sheet coordinates (10 nm, y up, origin at the sheet centre) to screen pixels."""
        left, top, right, bottom = frame
        w, h = size
        x = left + (point[0] + w / 2) / w * (right - left)
        y = top + (h / 2 - point[1]) / h * (bottom - top)
        return round(x), round(y)

    def canvas_click(self, x: int, y: int, hold: float = 0.3) -> None:
        """Press and release the left button at screen pixel (x, y) by posting messages to the editing canvas.
        Needs neither focus nor the real mouse, so it also works from a background process."""
        canvas = self.edit_window()
        cx, cy = win32gui.ScreenToClient(canvas, (x, y))
        lp = win32api.MAKELONG(cx & 0xFFFF, cy & 0xFFFF)
        win32gui.PostMessage(canvas, win32con.WM_MOUSEMOVE, 0, lp)
        time.sleep(0.05)
        win32gui.PostMessage(canvas, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lp)
        time.sleep(hold)
        win32gui.PostMessage(canvas, win32con.WM_LBUTTONUP, 0, lp)

    def real_click(self, x: int, y: int, hold: float = 0.3, button: str = "left") -> None:
        """Real mouse press at screen pixel (x, y) on this Proteus.  Proteus actuators read the real mouse,
        so posted messages do not work.  The window is raised above all others first (topmost on, then off,
        which needs no foreground rights), and the click is refused if anything else is under the point."""
        if BACKGROUND["on"]:
            raise GuiError("background mode is on (Proteus is kept off-screen so it does not disturb the owner); "
                           "real mouse clicks are not possible. Turn it off with proteus_background(false).")
        flags = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
        win32gui.ShowWindow(self.main, win32con.SW_RESTORE)
        win32gui.SetWindowPos(self.main, win32con.HWND_TOPMOST, 0, 0, 0, 0, flags)
        try:
            # The Alt-key trick in foreground() opens Proteus's menu bar when Proteus already has focus, and
            # the next click then only closes the menu; so use it only when another window is in front.
            if win32gui.GetForegroundWindow() != self.main:
                try:
                    self.foreground()
                except Exception:
                    pass
            time.sleep(0.2)
            # transient system overlays (the Alt-key "Task Switching" strip, tooltips) go away by themselves
            for _ in range(10):
                under = win32gui.WindowFromPoint((x, y))
                if win32process.GetWindowThreadProcessId(under)[1] == self.pid:
                    break
                win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)   # release a stuck Alt
                time.sleep(0.5)
            if win32process.GetWindowThreadProcessId(under)[1] != self.pid:
                raise GuiError(f"another window ({win32gui.GetWindowText(win32gui.GetAncestor(under, 2))!r}) "
                               "covers the click point; not clicking")
            # approach the point with real moves (Proteus tracks hover), press, release, then park the
            # cursor off the canvas so no tooltip or hover state lingers over the next target
            for dx in (12, 6, 0):
                win32api.SetCursorPos((x + dx, y + dx))
                time.sleep(0.05)
            win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, 1, 0, 0, 0)
            win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, -1, 0, 0, 0)
            time.sleep(0.15)
            down, up = ((win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP) if button == "right"
                        else (win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP))
            win32api.mouse_event(down, 0, 0, 0, 0)
            time.sleep(max(hold, 0.08))
            win32api.mouse_event(up, 0, 0, 0, 0)
            time.sleep(0.1)
            if button == "left":
                l, t, r, b = win32gui.GetWindowRect(self.main)
                win32api.SetCursorPos((l + 40, t + 10))   # title bar: off the drawing
        finally:
            win32gui.SetWindowPos(self.main, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, flags)

    def edit_component(self, ref: str, fields: dict[str, list[str]], values: dict[str, str],
                       timeout: float = 10.0) -> dict:
        """Set properties of any part (annotated ones included) in Proteus's own Edit Component dialog, in
        the running session.  `fields` maps each property key to the dialog labels it may have (taken from
        the device definitions in ROOT.DSN, e.g. PROGRAM -> ["Program File"]); the control right after the
        label shown in the dialog gets the value.  Nothing else in the dialog is touched."""
        def get_text(h):
            n = win32gui.SendMessage(h, win32con.WM_GETTEXTLENGTH, 0, 0)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.SendMessageW(h, win32con.WM_GETTEXT, n + 1, buf)
            return buf.value

        self.show_schematic()
        self.command(r"^Edit Component\t")        # the menu label is "Find/Edit Component", split at the slash
        d = self._wait_dialog(lambda d: d["title"] == "Find & Edit Component", timeout)["hwnd"]
        edit = next(c for c, cls, _ in _children(d) if cls == "Edit")
        user32.SendMessageW(edit, win32con.WM_SETTEXT, 0, ctypes.c_wchar_p(ref))
        _click_button(d, "OK")
        try:
            d = self._wait_dialog(lambda d: d["title"] == "Edit Component", timeout)["hwnd"]
        except GuiError:
            for x in self.dialogs():
                _click_button(x["hwnd"], "OK") or _click_button(x["hwnd"], "Cancel")
            raise GuiError(f"no part {ref!r} to edit")
        kids = [(c, cls, t.replace("&", "").rstrip(": ").strip()) for c, cls, t in _children(d)]
        shown = [t for _, cls, t in kids if cls == "Static" and t]
        plan = []
        for key, value in values.items():
            labels = {l.lower() for l in fields.get(key, [])}
            at = next((i for i, (_, cls, t) in enumerate(kids) if cls == "Static" and t.lower() in labels), None)
            ctl = next((c for c, cls, _ in kids[at + 1:] if cls in ("Edit", "ComboBox")), None) if at is not None else None
            if ctl is None:
                _click_button(d, "Cancel")
                raise GuiError(f"{ref}'s dialog has no field for {key} (labels tried {sorted(labels)}); fields "
                               f"shown: {shown}")
            plan.append((key, ctl, value))
        before = {key: get_text(ctl) for key, ctl, _ in plan}
        for key, ctl, value in plan:
            user32.SendMessageW(ctl, win32con.WM_SETTEXT, 0, ctypes.c_wchar_p(value))
        after = {key: get_text(ctl) for key, ctl, _ in plan}
        _click_button(d, "OK", post=True)
        time.sleep(1.0)
        self.handle_dialogs()
        return {"before": before, "after": after}

    def place_device(self, device: str, frame, size, xy, timeout: float = 10.0) -> None:
        """Library > Pick parts: search `device`, pick the exact match, and place it at sheet point `xy`
        (two clicks: the first attaches the outline to the cursor, the second drops it)."""
        import warnings
        self.show_schematic()
        self.command(r"^Pick parts from libraries")
        d = self._wait_dialog(lambda d: d["title"] == "Pick Devices", timeout)["hwnd"]
        kids = _children(d)
        edit = next(c for c, cls, _ in kids if cls == "Edit")
        lv = next(c for c, cls, _ in kids if cls == "SysListView32")
        win32gui.SendMessage(edit, win32con.WM_SETTEXT, 0, device)
        time.sleep(1.5)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from pywinauto.controls.common_controls import ListViewWrapper
            from pywinauto.controls.hwndwrapper import HwndWrapper
            lw = ListViewWrapper(HwndWrapper(lv).element_info)
            names = []
            for i in range(lw.item_count()):
                try:
                    names.append(lw.get_item(i, 0).text())
                except (UnicodeDecodeError, OSError):   # some library entries are not valid UTF-8
                    names.append("")
            hits = [i for i, n in enumerate(names) if n.upper() == device.upper()]
            if not hits:
                _click_button(d, "Cancel")
                raise GuiError(f"device {device!r} not found in the libraries; close matches: {names[:10]}")
            lw.get_item(hits[0]).select()
        time.sleep(0.3)
        _click_button(d, "OK")
        time.sleep(1.0)
        self.handle_dialogs()
        x, y = self.sheet_to_screen(frame, size, xy)
        self.real_click(x, y, hold=0.1)
        time.sleep(0.6)
        self.real_click(x, y, hold=0.1)
        time.sleep(0.8)
        self.handle_dialogs()

    def save(self, timeout: float = 30.0) -> None:
        """File > Save Project.  Proteus answers with a Save As dialog for projects it opened from a lab
        file; that dialog gets this project's own path, and the overwrite question is answered Yes."""
        before = self.project.stat().st_mtime
        self.command(r"^Save Project")
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            ds = self.dialogs()
            # the overwrite question first: while it is up, the Save As dialog behind it is disabled
            ds.sort(key=lambda d: not re.search(r"exist|replace|overwrite|confirm save", " ".join(d["texts"] + [d["title"]]), re.I))
            for d in ds:
                if not win32gui.IsWindow(d["hwnd"]) or not win32gui.IsWindowEnabled(d["hwnd"]):
                    continue
                if d["title"] == "Save Proteus Project File":
                    edit = next((c for c, cls, _ in _children(d["hwnd"])
                                 if cls == "Edit" and win32gui.GetDlgCtrlID(c) == 1148), None)
                    if edit is None:
                        raise GuiError("Save As dialog: file name box not found")
                    win32gui.SendMessage(edit, win32con.WM_SETTEXT, 0, str(self.project))
                    _click_button(d["hwnd"], "Save", post=True)
                    time.sleep(0.8)
                elif re.search(r"exist|replace|overwrite|confirm save", " ".join(d["texts"] + [d["title"]]), re.I):
                    # overwrite question (wording/buttons vary: Yes/No, Replace/Cancel, OK/Cancel)
                    button = next((b for b in ("Yes", "Replace", "OK") if b in d["buttons"]), None)
                    if button is None:
                        raise UnknownDialog(d["title"], d["texts"], d["buttons"])
                    _click_button(d["hwnd"], button, post=True)
                    time.sleep(0.5)
                else:
                    self.handle_dialogs()
            if self.project.stat().st_mtime != before and not self.dialogs():
                time.sleep(0.8)
                return
            time.sleep(0.3)
        raise GuiError(f"project file did not change after Save Project; dialogs: {self.dialogs()}")

    def terminal_windows(self) -> list[int]:
        """Top-level Virtual Terminal popup windows (they exist while a simulation runs)."""
        return [h for h in _windows(self.pid) if win32gui.GetWindowText(h).startswith("Virtual Terminal")]

    def terminal_mode(self, item: str = "Hex Display Mode", on: bool = True, window: int | None = None) -> dict:
        """Set a check item of a Virtual Terminal's right-click menu (e.g. "Hex Display Mode", "Echo Typed
        Characters") with real mouse clicks.  Returns the menu items seen and the item's state before."""
        h = window or (self.terminal_windows() or [None])[0]
        if h is None:
            raise GuiError("no Virtual Terminal window (is the simulation running?)")
        l, t, r, b = win32gui.GetWindowRect(h)
        win32gui.SetWindowPos(h, win32con.HWND_TOPMOST, 0, 0, 0, 0, win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
        try:
            self.real_click((l + r) // 2, (t + b) // 2, hold=0.1, button="right")
            deadline = time.time() + 3
            menu = None
            while time.time() < deadline and menu is None:
                found = []
                win32gui.EnumWindows(lambda w, _: found.append(w) if win32gui.GetClassName(w) == "#32768"
                                     and win32gui.IsWindowVisible(w)
                                     and win32process.GetWindowThreadProcessId(w)[1] == self.pid else None, None)
                menu = found[0] if found else None
                time.sleep(0.1)
            if menu is None:
                raise GuiError("the terminal's context menu did not open")
            hm = win32gui.SendMessage(menu, 0x01E1, 0, 0)          # MN_GETHMENU
            items, index, checked = [], None, None
            for i in range(user32.GetMenuItemCount(hm)):
                buf = ctypes.create_unicode_buffer(256)
                user32.GetMenuStringW(hm, i, buf, 256, win32con.MF_BYPOSITION)
                name = buf.value.replace("&", "").split("	")[0]
                if name:
                    items.append(name)
                if name.lower() == item.lower():
                    index = i
                    checked = bool(user32.GetMenuState(hm, i, win32con.MF_BYPOSITION) & win32con.MF_CHECKED)
            if index is None or checked == on:
                win32gui.PostMessage(menu, win32con.WM_KEYDOWN, win32con.VK_ESCAPE, 0)
                if index is None:
                    raise GuiError(f"no menu item {item!r}; items: {items}")
                return {"item": item, "was_on": checked, "on": on, "items": items}
            rect = ctypes.wintypes.RECT()
            if not user32.GetMenuItemRect(None, hm, index, ctypes.byref(rect)) or rect.right <= rect.left:
                win32gui.PostMessage(menu, win32con.WM_KEYDOWN, win32con.VK_ESCAPE, 0)
                raise GuiError(f"could not locate the menu item {item!r} on screen; not clicking")
            x, y = (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
            win32api.SetCursorPos((x, y))
            time.sleep(0.2)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            time.sleep(0.08)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            time.sleep(0.3)
            return {"item": item, "was_on": checked, "on": on, "items": items}
        finally:
            win32gui.SetWindowPos(h, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                                  win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)

    def terminal_type(self, text: str, delay: float = 0.08, windows: list[int] | None = None) -> int:
        """Type `text` into Virtual Terminal windows by posting WM_CHAR (no focus needed).  Returns the
        number of windows typed into."""
        targets = windows if windows is not None else self.terminal_windows()
        for ch in text:
            for h in targets:
                win32gui.PostMessage(h, win32con.WM_CHAR, ord(ch), 0)
                for c, _, _ in _children(h):
                    win32gui.PostMessage(c, win32con.WM_CHAR, ord(ch), 0)
            time.sleep(delay)
        return len(targets)
