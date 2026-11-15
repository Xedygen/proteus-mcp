"""Drive the Proteus 8.6 window: launch, dialogs, menu commands, simulation state, screenshots, clicks.

Proteus 8.6 has native Win32 menus, so commands are sent as WM_COMMAND with the menu item id, found by the
item's text (the menu-driven idea comes from jackmaikr4-sketch/keil-proteus-automation-skill-mcp, MIT).

Only processes started by this module are ever touched.  Every modal dialog is read; only dialogs on the
KNOWN list are answered automatically, anything else raises UnknownDialog with the dialog text.
"""
from __future__ import annotations

import ctypes
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


def _click_button(h: int, label: str) -> bool:
    for c, cls, t in _children(h):
        if cls == "Button" and t.replace("&", "") == label:
            win32gui.SendMessage(c, win32con.BM_CLICK, 0, 0)
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
        proc = subprocess.Popen([str(paths.PDS_EXE), str(project)], cwd=str(paths.PDS_EXE.parent))
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
            mains = [h for h in _windows(self.pid) if win32gui.GetClassName(h).startswith("LX_APPLN")
                     and stem in win32gui.GetWindowText(h)]
            if mains and "Schematic Capture" in win32gui.GetWindowText(mains[0]):
                quiet += 1
                if quiet >= 4:  # stable for 2 s with no dialog
                    self.main = mains[0]
                    return self.main
            else:
                quiet = 0
            time.sleep(0.5)
        raise GuiError(f"main window for {stem} did not appear in {timeout}s; windows={self.windows()}")

    def kill(self) -> None:
        if self.alive():
            self.proc.kill()
            self.proc.wait(10)

    def close(self, timeout: float = 20.0) -> dict:
        """File > Exit.  A save prompt is answered 'No' only if it is the standard save question."""
        if not self.alive():
            return {"closed": True}
        self.command(r"^E&xit Application")
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
        time.sleep(settle)
        self.handle_dialogs()
        return self.sim_state()

    def find_child(self, title: str, cls: str | None = None) -> int | None:
        for c, k, t in _children(self.main):
            if t == title and (cls is None or k == cls):
                return c
        return None

    def sim_log(self) -> list[dict]:
        """Rows of the Simulation Log.  The log popup only exists while the simulation is paused (or after it
        was opened during a pause); if it is not open, the simulation is paused, the popup opened, and the
        previous state restored."""
        import warnings
        lv_parent = self.find_child("Simulation Log")
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
            lv_parent = self.find_child("Simulation Log")
        if lv_parent is None:
            raise GuiError("Simulation Log popup not found")
        lv = next((c for c, k, _ in _children(lv_parent) if k == "SysListView32"), None)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from pywinauto.controls.common_controls import ListViewWrapper
            from pywinauto.controls.hwndwrapper import HwndWrapper
            w = ListViewWrapper(HwndWrapper(lv).element_info)
            rows = [{"message": w.get_item(i, 0).text(), "source": w.get_item(i, 1).text()}
                    for i in range(w.item_count())]
        if resume:
            self.sim("start")
        return rows

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
