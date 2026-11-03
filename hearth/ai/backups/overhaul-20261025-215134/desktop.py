"""Open Hearth as a desktop app window (no browser chrome).

Starts the Hearth server in the background if it is not running yet, then opens an app window:
  1. a frameless pywebview window, full screen over the Windows taskbar by default; the page's own taskbar holds
     the window buttons (minimize / full screen / close), and in a window it can be dragged and resized
  2. Microsoft Edge / Chrome in app mode (python desktop.py --browser, or when pywebview is missing)
  3. the default browser as a last resort

The pywebview window lives in the tray: closing it only hides it (the page stays loaded, so it reopens instantly),
and starting Hearth again brings the running window back instead of opening a second one.
--hidden starts straight into the tray (used by "Start with Windows").
"""

import argparse
import ctypes
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from hearth.config import DATA_DIR, IS_WINDOWS, SETTINGS  # noqa: E402

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0)
INSTANCE_FILE = os.path.join(DATA_DIR, "desktop-instance.json")
WINDOW_FILE = os.path.join(DATA_DIR, "desktop-window.json")  # {"fullscreen": bool}


# ---------- one window: a second start asks the running one to show itself ----------

def signal_running(path):
    """Ask an already running Hearth window to come forward (and open `path`). True when one answered."""
    try:
        with open(INSTANCE_FILE, encoding="utf-8") as fh:
            info = json.load(fh)
        with socket.create_connection(("127.0.0.1", int(info["port"])), timeout=1.5) as conn:
            if IS_WINDOWS:
                # only the process the user just started may take the foreground: hand that right over
                ctypes.windll.user32.AllowSetForegroundWindow(int(info["pid"]))
            conn.sendall(("show %s\n" % path).encode("utf-8"))
            return conn.recv(16).startswith(b"ok")
    except (OSError, ValueError, KeyError):
        return False  # nothing running (or a stale file from a crash)


def listen_for_instances(on_show, on_quit=None):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    with open(INSTANCE_FILE, "w", encoding="utf-8") as fh:
        json.dump({"port": server.getsockname()[1], "pid": os.getpid()}, fh)

    def serve():
        while True:
            conn, _addr = server.accept()
            with conn:
                try:
                    conn.settimeout(2)
                    line = conn.recv(1024).decode("utf-8", "replace").strip()
                    if line.startswith("show"):
                        conn.sendall(b"ok")
                        on_show(line[4:].strip() or "/")
                    elif line == "quit" and on_quit:  # Scorp Server's Stop / Restart app buttons
                        conn.sendall(b"ok")
                        on_quit()
                except OSError:
                    pass

    threading.Thread(target=serve, daemon=True, name="instances").start()


def forget_instance():
    try:
        with open(INSTANCE_FILE, encoding="utf-8") as fh:
            if json.load(fh).get("pid") == os.getpid():
                os.remove(INSTANCE_FILE)
    except (OSError, ValueError):
        pass


def server_up(url):
    try:
        urllib.request.urlopen(url + "/api/auth/status", timeout=1.5).read()
        return True
    except Exception:
        return False


def start_server(url):
    if server_up(url):
        return True
    python = sys.executable
    if IS_WINDOWS and python.lower().endswith("python.exe") and os.path.exists(python[:-10] + "pythonw.exe"):
        python = python[:-10] + "pythonw.exe"  # no console window
    log = open(os.path.join(DATA_DIR, "server.log"), "a", encoding="utf-8")
    subprocess.Popen([python, os.path.join(HERE, "server.py")], cwd=HERE, stdout=log, stderr=log,
                     creationflags=(DETACHED | NO_WINDOW) if IS_WINDOWS else 0, start_new_session=not IS_WINDOWS)
    for _ in range(120):
        if server_up(url):
            return True
        time.sleep(0.25)
    return False


def find_app_browser():
    candidates = []
    if IS_WINDOWS:
        for base in (os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                     os.environ.get("LOCALAPPDATA", "")):
            candidates += [os.path.join(base, r"Microsoft\Edge\Application\msedge.exe"), os.path.join(base, r"Google\Chrome\Application\chrome.exe")]
    candidates += [shutil.which(n) for n in ("msedge", "microsoft-edge", "google-chrome", "chromium", "chromium-browser", "chrome")]
    return next((c for c in candidates if c and os.path.exists(c)), None)


def open_app_mode(exe, url):
    profile = os.path.join(DATA_DIR, "app-window")
    args = [exe, "--app=" + url, "--user-data-dir=" + profile, "--window-size=1320,880", "--no-first-run",
            "--no-default-browser-check", "--disable-features=TranslateUI"]
    proc = subprocess.Popen(args)
    proc.wait()


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RECT), ("rcWork", _RECT), ("dwFlags", ctypes.c_ulong)]


WM_NCLBUTTONDOWN = 0xA1
HTCAPTION = 2
HT_EDGES = {"left": 10, "right": 11, "top": 12, "top-left": 13, "top-right": 14, "bottom": 15, "bottom-left": 16, "bottom-right": 17}
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010


class WindowControls:
    """The page's title bar calls these as window.pywebview.api.* (Windows only; names with _ stay private)."""

    def __init__(self):
        self._window = None
        self._tray = None
        self._restore = None  # (x, y, w, h) while full screen

    def _hwnd(self):
        return self._window.native.Handle.ToInt64()

    def _on_ui(self, fn):
        # window messages that start a mouse loop must come from the window's own thread
        from System import Action
        self._window.native.BeginInvoke(Action(fn))

    def _monitor(self, hwnd):
        """The whole monitor the window is on: full screen covers the Windows taskbar too."""
        user32 = ctypes.windll.user32
        user32.MonitorFromWindow.restype = ctypes.c_void_p
        info = _MONITORINFO(cbSize=ctypes.sizeof(_MONITORINFO))
        user32.GetMonitorInfoW(ctypes.c_void_p(user32.MonitorFromWindow(ctypes.c_void_p(hwnd), 2)), ctypes.byref(info))
        r = info.rcMonitor
        return r.left, r.top, r.right - r.left, r.bottom - r.top

    @staticmethod
    def _wants_fullscreen():
        try:
            with open(WINDOW_FILE, encoding="utf-8") as f:
                return bool(json.load(f).get("fullscreen", True))
        except (OSError, ValueError):
            return True

    @staticmethod
    def _remember(fullscreen):
        try:
            with open(WINDOW_FILE, "w", encoding="utf-8") as f:
                json.dump({"fullscreen": fullscreen}, f)
        except OSError:
            pass

    def _enter_fullscreen(self):
        """Called once the window is shown: start full screen unless it was last left as a window."""
        if self._wants_fullscreen() and not self._restore:
            self.toggle_maximize(remember=False)
            # the page may have asked frame() before this ran
            code = "window.frameEvent && frameEvent({maximized: true})"
            threading.Thread(target=_safe(lambda: self._window.evaluate_js(code)), daemon=True).start()

    def _place(self, hwnd, x, y, w, h):
        ctypes.windll.user32.SetWindowPos(ctypes.c_void_p(hwnd), None, x, y, w, h, SWP_NOZORDER | SWP_NOACTIVATE)

    def minimize(self):
        self._window.minimize()

    def close(self):
        if self._tray:
            self._tray.hide()  # keeps running in the tray
        else:
            self._window.destroy()

    def scorpi(self, action="state", x=None, y=None):
        """Scorpi lives on the prompt box or, detached, in its own overlay window over every program.
        action: state | detach (x, y: where on screen it jumps out, CSS pixels) | dock."""
        overlay = self._tray.ember if self._tray else None
        if not overlay or overlay._window is None:
            return {"desktop": False, "detached": False}
        if action == "detach":
            if x is not None and y is not None:
                s = overlay._scale()
                overlay._center = (int(float(x) * s), int(float(y) * s))
                overlay._mode = "compact"
                overlay._place_compact()
                overlay._save(pos=list(overlay._center))
            overlay._set_visible(True)
        elif action == "dock":
            overlay._set_visible(False)
        return {"desktop": True, "detached": bool(overlay._prefs.get("detached", False))}

    def frame(self):
        """Whether the page should draw its own window buttons, and whether the window is full screen."""
        return {"framed": IS_WINDOWS, "maximized": self._restore is not None}

    def toggle_maximize(self, remember=True):
        """Full screen (the whole monitor, over the Windows taskbar) or back to a normal window."""
        hwnd = self._hwnd()
        if self._restore:
            x, y, w, h = self._restore
            self._restore = None
        else:
            r = _RECT()
            ctypes.windll.user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r))
            self._restore = (r.left, r.top, r.right - r.left, r.bottom - r.top)
            x, y, w, h = self._monitor(hwnd)
        self._place(hwnd, x, y, w, h)
        if remember:
            self._remember(self._restore is not None)
        return self._restore is not None

    def drag(self):
        """Move the window with the mouse like a native title bar. Returns whether it is still maximized."""
        hwnd = self._hwnd()
        if self._restore:
            # pull a full-screen window back to its normal size under the cursor
            pt = _POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            ax, ay, aw, _ah = self._monitor(hwnd)
            w, h = self._restore[2], self._restore[3]
            x = pt.x - int(w * (pt.x - ax) / max(1, aw))
            self._restore = None
            self._remember(False)
            self._place(hwnd, x, max(ay, pt.y - h + 30), w, h)
        self._on_ui(lambda: (ctypes.windll.user32.ReleaseCapture(),
                             ctypes.windll.user32.SendMessageW(ctypes.c_void_p(hwnd), WM_NCLBUTTONDOWN, HTCAPTION, 0)))
        return False

    def resize(self, edge):
        hit = HT_EDGES.get(edge)
        if not hit or self._restore:
            return
        hwnd = self._hwnd()
        self._on_ui(lambda: (ctypes.windll.user32.ReleaseCapture(),
                             ctypes.windll.user32.SendMessageW(ctypes.c_void_p(hwnd), WM_NCLBUTTONDOWN, hit, 0)))


# ---------- Ember: a small always-on-top companion that can attach to any program's window ----------

GW_HWNDPREV = 3
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x80
WS_EX_TRANSPARENT = 0x20
WS_EX_APPWINDOW = 0x40000
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
VK_LBUTTON = 0x01
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
SWP_ASYNC = 0x4000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
HWND_TOPMOST = -1
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
EMBER_PREFS = os.path.join(DATA_DIR, "ember.json")
# window sizes in CSS pixels (scaled by the display's DPI); ORB is the distance from the orb's centre to the window edge.
# WebView2 windows ignore window regions and colour keys, so Ember is a small tile with rounded corners rather than a
# see-through shape; while it is dragged the tile widens to name the window underneath.
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2
EMBER_SIZES = {"compact": (64, 64), "drag": (248, 104)}
ORB = 32
SKIP_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Windows.UI.Core.CoreWindow"}


def _win32():
    """user32/dwmapi with 64-bit-safe signatures for the calls Ember makes."""
    from ctypes import wintypes
    # private handles, so these signatures don't leak into other code using ctypes.windll.user32
    user32, dwm = ctypes.WinDLL("user32"), ctypes.WinDLL("dwmapi")
    H = wintypes.HWND
    for name, args, res in (
        ("GetTopWindow", [H], H), ("GetWindow", [H, ctypes.c_uint], H),
        ("IsWindow", [H], wintypes.BOOL), ("IsWindowVisible", [H], wintypes.BOOL), ("IsIconic", [H], wintypes.BOOL),
        ("GetWindowRect", [H, ctypes.POINTER(_RECT)], wintypes.BOOL), ("GetWindowTextW", [H, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
        ("GetClassNameW", [H, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int), ("GetWindowLongW", [H, ctypes.c_int], ctypes.c_long),
        ("SetWindowLongW", [H, ctypes.c_int, ctypes.c_long], ctypes.c_long), ("ShowWindowAsync", [H, ctypes.c_int], wintypes.BOOL),
        ("SetWindowPos", [H, H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint], wintypes.BOOL),
        ("GetWindowThreadProcessId", [H, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD), ("GetDpiForWindow", [H], ctypes.c_uint),
        ("MonitorFromPoint", [_POINT, ctypes.c_uint], ctypes.c_void_p), ("GetMonitorInfoW", [ctypes.c_void_p, ctypes.POINTER(_MONITORINFO)], wintypes.BOOL),
        ("GetWindowDC", [H], ctypes.c_void_p), ("ReleaseDC", [H, ctypes.c_void_p], ctypes.c_int),
        ("PrintWindow", [H, ctypes.c_void_p, ctypes.c_uint], wintypes.BOOL),
    ):
        fn = getattr(user32, name)
        fn.argtypes, fn.restype = args, res
    dwm.DwmGetWindowAttribute.argtypes = [H, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    dwm.DwmSetWindowAttribute.argtypes = [H, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    return user32, dwm


class EmberOverlay:
    """Ember's overlay window; ember.html calls these as window.pywebview.api.* (names with _ stay private).

    Ember is voice-only: the page listens and speaks. Python owns the window's geometry, and drags run here, natively,
    so Ember can be dropped onto any program's window. Attached, it rides along with that window (keeping the spot it
    was dropped on), hides while the window is minimized or covered, and can take a snapshot of it for the model.
    """

    def __init__(self, show_main, on_change=None):
        self._window = None
        self._show_main = show_main
        self._on_change = on_change  # tells the main window whether Scorpi is out on the desktop
        self._u, self._dwm = _win32()
        self._mode = "compact"
        self._center = None  # the orb's centre on screen (physical pixels)
        self._target = None  # attached window handle
        self._info = None
        self._anchor = (0, 0)  # where on the attached window Ember sits, from its top-left corner
        self._hover = {"dragging": False, "target": None}
        self._shown = True
        self._quitting = False
        self._prefs = self._load()

    # ---------- helpers ----------

    def _load(self):
        try:
            with open(EMBER_PREFS, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _save(self, **patch):
        self._prefs.update(patch)
        try:
            with open(EMBER_PREFS, "w", encoding="utf-8") as fh:
                json.dump(self._prefs, fh)
        except OSError:
            pass

    def _hwnd(self):
        return self._window.native.Handle.ToInt64()

    def _scale(self):
        return (self._u.GetDpiForWindow(self._hwnd()) or 96) / 96

    def _size(self, mode):
        s = self._scale()
        w, h = EMBER_SIZES[mode]
        return int(w * s), int(h * s), int(ORB * s)

    def _work_at(self, x, y):
        info = _MONITORINFO(cbSize=ctypes.sizeof(_MONITORINFO))
        self._u.GetMonitorInfoW(self._u.MonitorFromPoint(_POINT(int(x), int(y)), 2), ctypes.byref(info))
        r = info.rcWork
        return r.left, r.top, r.right, r.bottom

    def _place(self, x, y, w, h):
        self._u.SetWindowPos(self._hwnd(), None, int(x), int(y), int(w), int(h), SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNC)

    def _place_compact(self):
        w, h, orb = self._size("compact")
        cx, cy = self._center
        l, t, r, b = self._work_at(cx, cy)
        cx, cy = min(max(cx, l + orb), r - orb), min(max(cy, t + orb), b - orb)
        self._center = (cx, cy)
        self._place(cx - orb, cy - orb, w, h)

    def _show(self, on):
        if on != self._shown:
            self._shown = on
            self._u.ShowWindowAsync(self._hwnd(), SW_SHOWNOACTIVATE if on else SW_HIDE)
        if on:
            # a window that starts hidden loses on_top: put Scorpi above every program again
            self._u.SetWindowPos(self._hwnd(), ctypes.c_void_p(HWND_TOPMOST), 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_ASYNC)

    def _emit(self, event):
        code = "window.emberEvent && emberEvent(%s)" % json.dumps(event)
        threading.Thread(target=_safe(lambda: self._window.evaluate_js(code)), daemon=True).start()

    def _rect(self, hwnd):
        """The window's visible bounds (without the invisible resize borders Windows 10/11 add)."""
        r = _RECT()
        if self._dwm.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)) != 0:
            self._u.GetWindowRect(hwnd, ctypes.byref(r))
        return r.left, r.top, r.right, r.bottom

    def _text(self, fn, hwnd):
        buf = ctypes.create_unicode_buffer(512)
        fn(hwnd, buf, 512)
        return buf.value

    def _process(self, hwnd):
        from ctypes import wintypes
        pid = wintypes.DWORD()
        self._u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = ctypes.c_void_p
        handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            k32.QueryFullProcessImageNameW(ctypes.c_void_p(handle), 0, buf, ctypes.byref(size))
            return os.path.basename(buf.value)
        finally:
            k32.CloseHandle(ctypes.c_void_p(handle))

    def _real(self, hwnd):
        """A visible, uncloaked application window with a title (not the desktop, taskbar or Ember itself)."""
        if not hwnd or hwnd == self._hwnd() or not self._u.IsWindowVisible(hwnd) or self._u.IsIconic(hwnd):
            return False
        cloaked = ctypes.c_int(0)
        self._dwm.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if cloaked.value or self._text(self._u.GetClassNameW, hwnd) in SKIP_CLASSES or not self._text(self._u.GetWindowTextW, hwnd).strip():
            return False
        if self._u.GetWindowLongW(hwnd, GWL_EXSTYLE) & (WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT):
            return False  # tool palettes, and click-through overlays the mouse passes straight through
        l, t, r, b = self._rect(hwnd)
        return r - l > 80 and b - t > 60

    def _window_at(self, x, y):
        """The topmost real window under a screen point, looking through Ember and click-through overlays."""
        hwnd = self._u.GetTopWindow(None)
        while hwnd:
            if self._real(hwnd):
                l, t, r, b = self._rect(hwnd)
                if l <= x < r and t <= y < b:
                    return hwnd
            hwnd = self._u.GetWindow(hwnd, 2)  # GW_HWNDNEXT: one step down the z-order
        return None

    def _describe(self, hwnd):
        l, t, r, b = self._rect(hwnd)
        return {"hwnd": int(hwnd), "title": self._text(self._u.GetWindowTextW, hwnd)[:200], "process": self._process(hwnd),
                "rect": {"x": l, "y": t, "w": r - l, "h": b - t}}

    def _covered(self, hwnd, x, y):
        """Whether another window above the attached one covers the spot where Ember sits."""
        above = self._u.GetWindow(hwnd, GW_HWNDPREV)
        while above:
            if self._real(above):
                l, t, r, b = self._rect(above)
                if l <= x < r and t <= y < b:
                    return True
            above = self._u.GetWindow(above, GW_HWNDPREV)
        return False

    # ---------- following the attached window ----------

    def _follow(self):
        while True:
            time.sleep(0.12)
            _safe(self._follow_once)()

    def _follow_once(self):
        own = self._hwnd()
        if self._shown and self._u.IsIconic(own):
            # Win+M and friends minimize every window; Ember comes straight back, on top, at its spot
            self._u.ShowWindowAsync(own, SW_SHOWNOACTIVATE)
            self._u.SetWindowPos(own, ctypes.c_void_p(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | SWP_NOACTIVATE | SWP_ASYNC)  # HWND_TOPMOST, no move/size
            if self._mode == "compact":
                self._place_compact()
        hwnd = self._target
        if not hwnd or self._mode == "drag":
            return
        if not self._u.IsWindow(hwnd):
            self._target = self._info = None
            self._show(True)
            self._emit({"type": "detached"})
            return
        l, t, r, b = self._rect(hwnd)
        _w, _h, orb = self._size("compact")
        cx = min(max(l + self._anchor[0], l + orb // 2), r - orb // 2)
        cy = min(max(t + self._anchor[1], t + orb // 2), b - orb // 2)
        visible = self._u.IsWindowVisible(hwnd) and not self._u.IsIconic(hwnd) and not self._covered(hwnd, cx, cy)
        self._show(visible)
        if visible and (cx, cy) != self._center:
            self._center = (cx, cy)
            self._place_compact()

    def _attach(self, hwnd):
        l, t, r, b = self._rect(hwnd)
        cx, cy = self._center
        self._anchor = (min(max(cx, l), r) - l, min(max(cy, t), b) - t)
        self._target = hwnd
        self._info = self._describe(hwnd)
        return self._info

    # ---------- setup (called by desktop.py once the window exists) ----------

    def _setup(self):
        hwnd = self._hwnd()
        ex = self._u.GetWindowLongW(hwnd, GWL_EXSTYLE)
        # a tool window: no taskbar button and no Alt+Tab entry
        self._u.SetWindowLongW(hwnd, GWL_EXSTYLE, (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)
        corner = ctypes.c_int(DWMWCP_ROUND)  # Windows 11 rounds the tile's corners (older Windows keeps them square)
        self._dwm.DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(corner), ctypes.sizeof(corner))
        self._u.ShowWindowAsync(hwnd, SW_HIDE)
        pos = self._prefs.get("pos")
        if pos:
            self._center = tuple(pos)
        else:
            ctypes.windll.user32.MonitorFromWindow.restype = ctypes.c_void_p
            info = _MONITORINFO(cbSize=ctypes.sizeof(_MONITORINFO))
            ctypes.windll.user32.GetMonitorInfoW(ctypes.c_void_p(ctypes.windll.user32.MonitorFromWindow(ctypes.c_void_p(hwnd), 1)), ctypes.byref(info))
            s = self._scale()
            self._center = (info.rcWork.right - int(64 * s), info.rcWork.bottom - int(150 * s))
        self._place_compact()
        self._shown = None
        self._show(bool(self._prefs.get("detached", False)))  # Scorpi starts on the prompt box unless it was out
        threading.Thread(target=self._follow, daemon=True, name="ember-follow").start()

    def _set_visible(self, on):
        """Out on the desktop (on) or back on the main window's prompt box (off)."""
        self._save(detached=bool(on))
        if not on:
            self._target = self._info = None
        self._show(bool(on))
        if self._on_change:
            self._on_change(bool(on))

    # ---------- the page's API ----------

    def drag(self):
        """Move Ember with the mouse until the button is released. Dropped on a window, Ember attaches to it."""
        pt = _POINT()
        self._u_cursor(pt)
        start = (pt.x, pt.y)
        off = (self._center[0] - pt.x, self._center[1] - pt.y)
        moved = False
        target = None
        threshold = 5 * self._scale()
        while ctypes.windll.user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000:
            self._u_cursor(pt)
            if not moved and abs(pt.x - start[0]) + abs(pt.y - start[1]) > threshold:
                moved = True
                self._mode = "drag"
                self._target = self._info = None
                self._hover = {"dragging": True, "target": None}
            if moved:
                self._center = (pt.x + off[0], pt.y + off[1])
                w, h, orb = self._size("drag")
                self._place(self._center[0] - w // 2, self._center[1] - orb, w, h)
                hit = self._window_at(pt.x, pt.y)
                if hit != target:
                    target = hit
                    self._hover = {"dragging": True, "target": self._describe(hit) if hit else None}
            time.sleep(0.012)
        self._hover = {"dragging": False, "target": None}
        if not moved:
            return {"moved": False}
        self._mode = "compact"
        self._place_compact()
        self._save(pos=list(self._center))
        info = self._attach(target) if target else None
        return {"moved": True, "target": info}

    def _u_cursor(self, pt):
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))

    def hover(self):
        return self._hover

    def attached(self):
        return self._info if self._target else None

    def detach(self):
        self._target = self._info = None
        self._show(True)
        return True

    def open_main(self, path="/"):
        self._show_main(path)

    def dock(self):
        """Right-click on the desktop Scorpi: back onto the prompt box."""
        self._set_visible(False)
        self._show_main(None)
        return True

    def snapshot(self):
        """A JPEG (base64) of the attached window, even when other windows cover it."""
        hwnd = self._target
        if not hwnd or not self._u.IsWindow(hwnd):
            return ""
        import base64
        import io
        img = self._print(hwnd)
        if img is None or img.convert("L").getextrema()[1] < 10:
            img = self._grab(hwnd)  # GPU-drawn windows can print black: take the pixels off the screen instead
        if img is None:
            return ""
        if img.width > 1600:
            img = img.resize((1600, int(img.height * 1600 / img.width)))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=80)
        return base64.b64encode(buf.getvalue()).decode()

    def _print(self, hwnd):
        try:
            from PIL import Image
        except ImportError:
            return None
        gdi = ctypes.WinDLL("gdi32")  # own handle: the signatures below stay local
        gdi.CreateCompatibleDC.restype = gdi.CreateCompatibleBitmap.restype = gdi.SelectObject.restype = ctypes.c_void_p
        gdi.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
        gdi.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        wr = _RECT()
        self._u.GetWindowRect(hwnd, ctypes.byref(wr))
        w, h = wr.right - wr.left, wr.bottom - wr.top
        if w <= 0 or h <= 0:
            return None
        wdc = self._u.GetWindowDC(hwnd)
        mdc = gdi.CreateCompatibleDC(wdc)
        bmp = gdi.CreateCompatibleBitmap(wdc, w, h)
        old = gdi.SelectObject(ctypes.c_void_p(mdc), ctypes.c_void_p(bmp))
        try:
            if not self._u.PrintWindow(hwnd, mdc, 2):  # PW_RENDERFULLCONTENT
                return None

            class BITMAPINFOHEADER(ctypes.Structure):
                _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32), ("biHeight", ctypes.c_int32),
                            ("biPlanes", ctypes.c_uint16), ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                            ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32), ("biYPelsPerMeter", ctypes.c_int32),
                            ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32)]

            bih = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=w, biHeight=-h, biPlanes=1, biBitCount=32)
            buf = ctypes.create_string_buffer(w * h * 4)
            gdi.GetDIBits(ctypes.c_void_p(mdc), ctypes.c_void_p(bmp), 0, h, buf, ctypes.byref(bih), 0)
            img = Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)
            l, t, r, b = self._rect(hwnd)  # trim the invisible borders
            return img.crop((l - wr.left, t - wr.top, r - wr.left, b - wr.top))
        finally:
            gdi.SelectObject(ctypes.c_void_p(mdc), ctypes.c_void_p(old))
            gdi.DeleteObject(ctypes.c_void_p(bmp))
            gdi.DeleteDC(ctypes.c_void_p(mdc))
            self._u.ReleaseDC(hwnd, wdc)

    def _grab(self, hwnd):
        try:
            import mss
            from PIL import Image
        except ImportError:
            return None
        l, t, r, b = self._rect(hwnd)
        was = self._shown
        self._show(False)  # Ember stays out of its own picture
        time.sleep(0.12)
        try:
            with mss.mss() as sct:
                shot = sct.grab({"left": l, "top": t, "width": r - l, "height": b - t})
                return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        finally:
            self._show(was)


def _allow_permissions(window, base):
    """Let Hearth's own pages use the microphone and notifications without a WebView2 prompt every time."""
    try:
        from System import Action
        from Microsoft.Web.WebView2.Core import CoreWebView2PermissionState

        def on_request(_sender, args):
            if str(args.Uri).startswith(base):
                args.State = CoreWebView2PermissionState.Allow

        def attach():
            window.native.browser.webview.CoreWebView2.PermissionRequested += on_request

        window.native.Invoke(Action(attach))
    except Exception:
        pass  # WebView2 then asks the usual way


ICON_PATH = os.path.join(HERE, "public", "icons", "hearth.ico")


def _set_window_icon(window):
    """Show Hearth's icon in the taskbar and title instead of the Python one."""
    try:
        from System import Action
        from System.Drawing import Icon

        def apply():
            window.native.Icon = Icon(ICON_PATH)

        window.native.Invoke(Action(apply))
    except Exception:
        pass  # keeps the default icon


STARTUP_LINK = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup\Hearth.lnk")


def _set_startup(enabled):
    """Start Hearth hidden in the tray at sign-in (a shortcut in the Startup folder), or stop doing so."""
    if not enabled:
        try:
            os.remove(STARTUP_LINK)
        except OSError:
            pass
        return
    pythonw = os.path.join(HERE, ".venv", "Scripts", "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    q = lambda v: v.replace("'", "''")  # noqa: E731
    script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut('%s'); $s.TargetPath = '%s'; "
              "$s.Arguments = '\"%s\" --hidden'; $s.WorkingDirectory = '%s'; $s.IconLocation = '%s'; "
              "$s.Description = 'Hearth in the tray'; $s.Save()") % (
        q(STARTUP_LINK), q(pythonw), q(os.path.join(HERE, "desktop.py")), q(HERE), q(ICON_PATH))
    subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, timeout=30, creationflags=NO_WINDOW)


def _safe(fn):
    """Run a tray/window callback without ever letting an error reach WinForms (it would show a crash dialog)."""
    def run(*args):
        try:
            return fn(*args)
        except Exception:
            import traceback
            try:
                with open(os.path.join(DATA_DIR, "desktop.log"), "a", encoding="utf-8") as fh:
                    fh.write("%s\n%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), traceback.format_exc()))
            except OSError:
                pass
    return run


class Tray:
    """Hearth's tray icon. Closing the window hides it here; the page keeps running (reminders, notifications)."""

    def __init__(self, window, base):
        self.window = window
        self.base = base
        self.icon = None
        self.quitting = False
        self.hinted = False
        self.ember = None  # Ember's overlay window, when it runs

    def _ui(self, fn):
        from System import Action
        form = self.window.native
        if form is not None and not form.IsDisposed:
            form.BeginInvoke(Action(_safe(fn)))

    def _js(self, code):
        # evaluate_js waits for the page; never call it on the window's own thread
        threading.Thread(target=_safe(lambda: self.window.evaluate_js(code)), daemon=True).start()

    def create(self):
        import System.Windows.Forms as WinForms
        from System.Drawing import Font, FontStyle, Icon

        form = self.window.native
        icon = WinForms.NotifyIcon()
        icon.Icon = Icon(ICON_PATH)
        icon.Text = "Scorp"
        menu = WinForms.ContextMenuStrip()

        def add(label, action, bold=False):
            item = WinForms.ToolStripMenuItem(label)
            item.Click += _safe(lambda _s, _e: action())
            if bold:
                item.Font = Font(item.Font, FontStyle.Bold)
            menu.Items.Add(item)
            return item

        add("Open Scorp", self.show, bold=True)
        add("Talk to Scorp", lambda: self.show("/voice"))
        add("Open in browser", lambda: webbrowser.open(self.base))
        menu.Items.Add(WinForms.ToolStripSeparator())
        startup = add("Start with Windows", lambda: None)
        startup.CheckOnClick = True
        startup.Checked = os.path.exists(STARTUP_LINK)
        startup.Click += _safe(lambda _s, _e: threading.Thread(target=_safe(_set_startup), args=(startup.Checked,), daemon=True).start())
        menu.Opening += _safe(lambda _s, _e: setattr(startup, "Checked", os.path.exists(STARTUP_LINK)))  # changed elsewhere too
        if self.ember:
            ember = add("Scorpi on the desktop", lambda: None)
            ember.CheckOnClick = True
            ember.Checked = self.ember._prefs.get("detached", False)
            ember.Click += _safe(lambda _s, _e: self.ember._set_visible(ember.Checked))
            menu.Opening += _safe(lambda _s, _e: setattr(ember, "Checked", self.ember._prefs.get("detached", False)))
        menu.Items.Add(WinForms.ToolStripSeparator())
        add("Quit Scorp", self.quit)
        icon.ContextMenuStrip = menu

        def on_click(_s, args):
            if args.Button == WinForms.MouseButtons.Left:
                self.show()

        icon.MouseClick += _safe(on_click)
        icon.Visible = True
        self.icon = icon

        def on_closing(_s, args):
            # the close button and Alt+F4 only hide; quitting, Windows shutting down or Task Manager really close
            if self.quitting or args.CloseReason != WinForms.CloseReason.UserClosing:
                return
            args.Cancel = True
            self.hide()

        def on_closed(_s, _e):
            icon.Visible = False
            icon.Dispose()

        form.FormClosing += _safe(on_closing)
        form.FormClosed += _safe(on_closed)

    def show(self, path=None):
        import System.Windows.Forms as WinForms

        def run():
            form = self.window.native
            form.Show()
            if form.WindowState == WinForms.FormWindowState.Minimized:
                form.WindowState = WinForms.FormWindowState.Normal
            form.Activate()
            ctypes.windll.user32.SetForegroundWindow(ctypes.c_void_p(form.Handle.ToInt64()))

        self._ui(run)
        if path and path != "/":
            self._js("history.pushState({}, '', %s); dispatchEvent(new PopStateEvent('popstate'))" % json.dumps(path))

    def hide(self):
        import System.Windows.Forms as WinForms

        def run():
            self.window.native.Hide()
            if not self.hinted and self.icon:
                self.hinted = True
                self.icon.ShowBalloonTip(4000, "Hearth is still running", "Click the tray icon to open it again. Quit from its menu.",
                                       WinForms.ToolTipIcon.Info)  # pythonnet 3 takes no plain ints for enums

        # the page stops listening to the microphone before it disappears
        self._js("dispatchEvent(new Event('hearth:hidden'))")
        self._ui(run)

    def quit(self):
        self.quitting = True
        if self.ember:
            self.ember._quitting = True
        self.window.destroy()


def open_ember(webview, base, show_main, on_change=None):
    """Scorpi's overlay: a small always-on-top window that floats over every program."""
    ember = EmberOverlay(show_main, on_change)
    win = webview.create_window("Scorpi", base + "/ember.html", js_api=ember, width=64, height=64, min_size=(40, 40),
                                frameless=True, easy_drag=False, on_top=True, shadow=False, resizable=False,
                                background_color="#000000")
    ember._window = win
    ready = []

    def on_loaded():
        if ready:
            return
        ready.append(True)
        _allow_permissions(win, base)  # Ember listens through the microphone
        _safe(ember._setup)()

    def on_closing():
        if ember._quitting:
            return None
        ember._set_visible(False)  # Alt+F4 sends Scorpi back to the prompt box
        return False

    win.events.loaded += on_loaded
    win.events.closing += on_closing
    return ember


def open_webview(url, base, hidden=False):
    # audio may start without a click first: Ember's window listens for its wake word from the moment it opens
    # (this variable replaces pywebview's own browser arguments, so they are repeated here)
    os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
                          "--disable-features=ElasticOverscroll --autoplay-policy=no-user-gesture-required")
    import webview  # pip install pywebview
    controls = WindowControls()
    window = webview.create_window("Scorp", url, js_api=controls, width=1320, height=880, min_size=(380, 560),
                                   background_color="#000000", text_select=True, hidden=hidden,
                                   frameless=IS_WINDOWS, easy_drag=False)
    controls._window = window
    if IS_WINDOWS:
        tray = controls._tray = Tray(window, base)

        def setup_tray():
            for _ in range(200):  # the native window exists shortly after start
                if window.native is not None:
                    break
                time.sleep(0.05)
            from System import Action
            window.native.Invoke(Action(_safe(tray.create)))
            listen_for_instances(tray.show, tray.quit)

        threading.Thread(target=setup_tray, daemon=True, name="tray").start()
        granted = []

        def on_shown():
            _set_window_icon(window)
            _safe(controls._enter_fullscreen)()

        window.events.shown += on_shown

        def on_loaded():
            if not granted:  # WebView2 is ready once the first page has loaded
                granted.append(True)
                _allow_permissions(window, base)

        window.events.loaded += on_loaded
        def scorpi_moved(detached):
            code = "window.scorpiEvent && scorpiEvent(%s)" % json.dumps({"detached": detached})
            threading.Thread(target=_safe(lambda: window.evaluate_js(code)), daemon=True).start()

        tray.ember = open_ember(webview, base, tray.show, scorpi_moved)
    try:
        webview.start(private_mode=False, storage_path=os.path.join(DATA_DIR, "webview"))
    finally:
        forget_instance()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", action="store_true", help="use Edge/Chrome app mode instead of the frameless window")
    parser.add_argument("--webview", action="store_true", help=argparse.SUPPRESS)  # the default now; kept for old shortcuts
    parser.add_argument("--path", default="/", help="page to open, e.g. /voice")
    parser.add_argument("--hidden", action="store_true", help="start in the tray without showing the window")
    args = parser.parse_args()
    if not args.browser and signal_running(args.path):
        return  # Hearth was already open: it is in front now
    if IS_WINDOWS:
        # own taskbar identity, so the window isn't grouped under (and shown as) Python
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Hearth.Desktop")
    base = "http://127.0.0.1:%d" % SETTINGS["port"]
    if not start_server(base):
        webbrowser.open(base)
        return
    url = base + args.path
    if not args.browser:
        try:
            open_webview(url, base, hidden=args.hidden)
            return
        except ImportError:
            pass
    exe = find_app_browser()
    if exe:
        open_app_mode(exe, url)
        return
    webbrowser.open(url)


if __name__ == "__main__":
    main()
