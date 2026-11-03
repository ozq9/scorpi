"""Scorpi, free on the Windows desktop.

Scorpi is a native see-through window (per-pixel alpha through UpdateLayeredWindow, drawn by scorpi_paint): no box
around it, and clicks pass through everywhere except Scorpi itself. It floats on a little cloud: bobs gently, drifts
around the screen now and then, stays wherever you drop it, naps when you are away and looks at the mouse. Once out,
it stays out (Win+D, "minimize all" and other always-on-top windows can't make it vanish) until you send it back.

Talking happens in a small glass card next to it (a WebView2 window running companion.html), which listens, asks Scorp
and speaks the answer. A push-to-talk key works from any program: hold it and talk ("hey Scorpi, what's this?"); the
window you are looking at (title, program, the browser's address and a picture of it) goes along with the question.
Tap the key to talk hands-free. Right-click Scorpi for its menu.

Preferences live in data/ember.json (the name is older than Scorpi).
"""

import base64
import ctypes
import io
import json
import math
import os
import random
import threading
import time
from ctypes import wintypes

import scorpi_paint as paint

# ---------- Win32 ----------

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32")
dwmapi = ctypes.WinDLL("dwmapi")
kernel32 = ctypes.WinDLL("kernel32")
H = wintypes.HWND


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte), ("SourceConstantAlpha", ctypes.c_byte), ("AlphaFormat", ctypes.c_byte)]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


for _name, _args, _res in (
    ("GetAsyncKeyState", [ctypes.c_int], ctypes.c_short), ("GetCursorPos", [ctypes.POINTER(POINT)], wintypes.BOOL),
    ("GetForegroundWindow", [], H), ("GetTopWindow", [H], H), ("GetWindow", [H, ctypes.c_uint], H),
    ("IsWindow", [H], wintypes.BOOL), ("IsWindowVisible", [H], wintypes.BOOL), ("IsIconic", [H], wintypes.BOOL),
    ("IsZoomed", [H], wintypes.BOOL), ("GetWindowRect", [H, ctypes.POINTER(RECT)], wintypes.BOOL),
    ("GetWindowTextW", [H, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int), ("GetClassNameW", [H, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
    ("GetWindowLongW", [H, ctypes.c_int], ctypes.c_long), ("SetWindowLongW", [H, ctypes.c_int, ctypes.c_long], ctypes.c_long),
    ("ShowWindowAsync", [H, ctypes.c_int], wintypes.BOOL), ("ShowWindow", [H, ctypes.c_int], wintypes.BOOL),
    ("SetWindowPos", [H, H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint], wintypes.BOOL),
    ("GetWindowThreadProcessId", [H, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD), ("GetDpiForWindow", [H], ctypes.c_uint),
    ("MonitorFromPoint", [POINT, ctypes.c_uint], ctypes.c_void_p), ("GetMonitorInfoW", [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)], wintypes.BOOL),
    ("GetDC", [H], ctypes.c_void_p), ("ReleaseDC", [H, ctypes.c_void_p], ctypes.c_int), ("GetWindowDC", [H], ctypes.c_void_p),
    ("PrintWindow", [H, ctypes.c_void_p, ctypes.c_uint], wintypes.BOOL),
    ("UpdateLayeredWindow", [H, ctypes.c_void_p, ctypes.POINTER(POINT), ctypes.POINTER(SIZE), ctypes.c_void_p, ctypes.POINTER(POINT),
                             wintypes.DWORD, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD], wintypes.BOOL),
    ("GetLastInputInfo", [ctypes.POINTER(LASTINPUTINFO)], wintypes.BOOL), ("SetForegroundWindow", [H], wintypes.BOOL),
    ("keybd_event", [ctypes.c_ubyte, ctypes.c_ubyte, wintypes.DWORD, ctypes.c_void_p], None),
):
    _fn = getattr(user32, _name)
    _fn.argtypes, _fn.restype = _args, _res
gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
kernel32.GetTickCount.restype = ctypes.c_uint32
dwmapi.DwmGetWindowAttribute.argtypes = [H, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
dwmapi.DwmSetWindowAttribute.argtypes = [H, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x80000
WS_EX_TOOLWINDOW = 0x80
WS_EX_TOPMOST = 0x8
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TRANSPARENT = 0x20
WS_EX_APPWINDOW = 0x40000
GW_HWNDNEXT = 2
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_ASYNC = 0x1, 0x2, 0x4, 0x10, 0x4000
HWND_TOPMOST = -1
ULW_ALPHA = 2
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34
DWMWCP_ROUND = 2
VK_LBUTTON = 0x01
SKIP_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Windows.UI.Core.CoreWindow"}
BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe", "arc.exe", "zen.exe", "librewolf.exe", "chromium.exe"}

# push-to-talk keys: name -> virtual-key codes (any of them counts)
KEYS = {
    "ctrl": [0x11], "lctrl": [0xA2], "rctrl": [0xA3], "alt": [0x12], "lalt": [0xA4], "ralt": [0xA5], "shift": [0x10],
    "win": [0x5B, 0x5C], "space": [0x20], "capslock": [0x14], "tab": [0x09], "xbutton1": [0x05], "xbutton2": [0x06],
    **{"f%d" % i: [0x6F + i] for i in range(1, 25)},
    **{chr(c).lower(): [c] for c in range(0x41, 0x5B)}, **{str(d): [0x30 + d] for d in range(10)},
}
# keys that, pressed while the push-to-talk keys go down, mean "this is a shortcut, not push-to-talk"
OTHER_KEYS = [0x08, 0x09, 0x0D, 0x1B, 0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x01, 0x02, 0x04,
              *range(0x30, 0x3A), *range(0x41, 0x5B), *range(0x60, 0x70), *range(0x70, 0x88), *range(0xBA, 0xC1), *range(0xDB, 0xE0)]
HOLD_MS = 260  # hold this long before it counts as push-to-talk (shorter is a tap)
SIZES = {"small": 76, "normal": 96, "large": 124}  # the canvas in CSS pixels (Scorpi is 80% of it)
DEFAULTS = {"detached": False, "ptt": True, "hotkey": "ctrl+win", "roam": True, "peek": True, "card": True, "size": "normal"}


def _text(fn, hwnd):
    buf = ctypes.create_unicode_buffer(512)
    fn(hwnd, buf, 512)
    return buf.value


def window_rect(hwnd):
    """The visible bounds (without the invisible resize borders of Windows 10/11)."""
    r = RECT()
    if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def process_name(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(0x1000, False, pid.value)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        kernel32.QueryFullProcessImageNameW(ctypes.c_void_p(handle), 0, buf, ctypes.byref(size))
        return os.path.basename(buf.value)
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def work_area(x, y):
    info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    user32.GetMonitorInfoW(user32.MonitorFromPoint(POINT(int(x), int(y)), 2), ctypes.byref(info))
    r = info.rcWork
    return r.left, r.top, r.right, r.bottom


def cursor():
    p = POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def key_down(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def idle_seconds():
    li = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
    user32.GetLastInputInfo(ctypes.byref(li))
    return ((kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 1000.0


def parse_hotkey(text):
    groups = []
    for part in str(text or "").lower().replace(" ", "").split("+"):
        if part in KEYS:
            groups.append(KEYS[part])
    return groups


def browser_url(hwnd):
    """The address of the page a browser window shows (UI Automation), or ''."""
    try:
        import re
        import uiautomation as auto
    except ImportError:
        return ""
    url = ""
    with auto.UIAutomationInitializerInThread():
        try:
            root = auto.ControlFromHandle(hwnd)
            looks = re.compile(r"^(https?://|file:|chrome://|edge://|about:)?[\w.-]+\.[a-z]{2,}(?::\d+)?([/?#].*)?$|^localhost(:\d+)?(/.*)?$", re.I)
            # the address bar is an Edit near the top; its name depends on browser and language, so test the value
            for ctrl, _depth in auto.WalkControl(root, maxDepth=14):
                if ctrl.ControlTypeName != "EditControl":
                    continue
                try:
                    value = ctrl.GetValuePattern().Value.strip()
                except Exception:
                    continue
                if value and " " not in value and looks.match(value):
                    url = value if "://" in value or value.startswith("about:") else "https://" + value
                    break
        except Exception:
            return ""
    return url


def snapshot(hwnd):
    """A JPEG (base64) of a window, even when other windows cover it; GPU-drawn windows are taken off the screen."""
    try:
        from PIL import Image
    except ImportError:
        return ""
    img = _print_window(hwnd, Image)
    if img is None or img.convert("L").getextrema()[1] < 10:
        img = _grab_screen(hwnd, Image)
    if img is None:
        return ""
    if img.width > 1600:
        img = img.resize((1600, int(img.height * 1600 / img.width)))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=82)
    return base64.b64encode(buf.getvalue()).decode()


def _print_window(hwnd, Image):
    wr = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(wr))
    w, h = wr.right - wr.left, wr.bottom - wr.top
    if w <= 0 or h <= 0:
        return None
    wdc = user32.GetWindowDC(hwnd)
    mdc = gdi32.CreateCompatibleDC(wdc)
    bmp = gdi32.CreateCompatibleBitmap(wdc, w, h)
    old = gdi32.SelectObject(mdc, bmp)
    try:
        if not user32.PrintWindow(hwnd, mdc, 2):  # PW_RENDERFULLCONTENT
            return None

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32), ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                        ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32), ("biSizeImage", ctypes.c_uint32),
                        ("biXPelsPerMeter", ctypes.c_int32), ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32)]

        bih = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=w, biHeight=-h, biPlanes=1, biBitCount=32)
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bih), 0)
        img = Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)
        l, t, r, b = window_rect(hwnd)
        return img.crop((l - wr.left, t - wr.top, r - wr.left, b - wr.top))
    finally:
        gdi32.SelectObject(mdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(hwnd, wdc)


def _grab_screen(hwnd, Image):
    try:
        import mss
    except ImportError:
        return None
    l, t, r, b = window_rect(hwnd)
    with mss.mss() as sct:
        shot = sct.grab({"left": l, "top": t, "width": r - l, "height": b - t})
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


class Companion:
    """The desktop Scorpi plus its talk card. companion.html calls these as window.pywebview.api.* (names with _ stay
    private); the main window reaches the same object through WindowControls.companion()."""

    def __init__(self, prefs_path, show_main, on_change=None, safe=lambda f: f):
        self._prefs_path = prefs_path
        self._show_main = show_main
        self._on_change = on_change  # tells the main window whether Scorpi is out on the desktop
        self._safe = safe
        self._prefs = {**DEFAULTS, **self._load()}
        self._card = None  # the pywebview window of the talk card
        self._form = None  # the native Scorpi window (WinForms)
        self._painter = None
        self._bmp = None
        self._own = set()  # our own window handles (never the subject of a question)
        self._quitting = False
        # where and how Scorpi is
        self._fx = self._fy = 0.0  # feet, physical pixels
        self._vx = self._vy = 0.0
        self._state = "hidden"  # hidden | float | drag
        self._target = None  # where the cloud drifts to (x, y)
        self._keep_at = 0.0  # next check that the window is still shown and on top
        self._facing = 1
        self._mood = "idle"  # what the card says Scorpi is doing (listening, thinking, talking…)
        self._flash = None  # (mood, until)
        self._talk = 0.0
        self._blink_at = time.time() + 2
        self._blink = 0.0
        self._gaze = [0.0, 0.0]
        self._wander_at = 0.0
        self._decide_at = time.time() + 4
        self._summoned = False  # it just came out for the push-to-talk key (it stays out; the menu offers to send it back)
        self._busy_until = 0.0
        self._last_tick = time.time()
        self._t0 = time.time()
        self._drag = None
        self._hover = False
        self._pet = (0, 0, 0)  # (last x, direction, turns)
        self._glance = None
        self._card_h = 120
        self._card_mode = ""
        self._card_shown = False

    # ---------- preferences ----------

    def _load(self):
        try:
            with open(self._prefs_path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _save(self, **patch):
        self._prefs.update(patch)
        try:
            with open(self._prefs_path, "w", encoding="utf-8") as fh:
                json.dump(self._prefs, fh)
        except OSError:
            pass

    def public_prefs(self):
        return {k: self._prefs.get(k, v) for k, v in DEFAULTS.items()}

    # ---------- setup (desktop.py calls these once the windows exist) ----------

    def _attach_card(self, window):
        self._card = window

    def _setup_card(self):
        hwnd = self._card_hwnd()
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, (ex | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE) & ~WS_EX_APPWINDOW)
        corner = ctypes.c_int(DWMWCP_ROUND)
        dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(corner), ctypes.sizeof(corner))
        border = ctypes.c_uint(0x00302C2A)  # a dark hairline (COLORREF is BGR)
        dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_BORDER_COLOR, ctypes.byref(border), ctypes.sizeof(border))
        user32.ShowWindowAsync(hwnd, SW_HIDE)
        self._own.add(int(hwnd))

    def _create_form(self, ui_form):
        """Create Scorpi's own window on the UI thread of `ui_form` (the main window's WinForms form)."""
        import System.Windows.Forms as WF
        from System import Action

        def make():
            form = WF.Form()
            form.FormBorderStyle = getattr(WF.FormBorderStyle, "None")
            form.TopMost = True  # WinForms keeps it above other windows
            form.ShowInTaskbar = False
            form.StartPosition = WF.FormStartPosition.Manual
            form.Text = "Scorpi"
            hwnd = form.Handle.ToInt64()
            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, (ex | WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOACTIVATE) & ~WS_EX_APPWINDOW)
            form.MouseDown += self._safe(self._on_down)
            form.MouseUp += self._safe(self._on_up)
            form.MouseMove += self._safe(self._on_move)
            form.MouseDoubleClick += self._safe(lambda _s, _e: self.open_main("/"))
            form.MouseEnter += self._safe(self._on_enter)
            form.MouseLeave += self._safe(lambda _s, _e: setattr(self, "_hover", False))
            self._form = form
            self._own.add(int(hwnd))
            self._painter = paint.Painter()
            timer = WF.Timer()
            timer.Interval = 33
            timer.Tick += self._safe(lambda _s, _e: self._tick())
            timer.Start()
            self._timer = timer
            start = self._prefs.get("pos")
            if start:
                self._fx, self._fy = float(start[0]), float(start[1])
            else:
                l, t, r, b = work_area(*cursor())
                self._fx, self._fy = r - 180.0, b - 120.0
            self._clamp()
            if self._prefs.get("detached"):
                self._show(True)

        ui_form.Invoke(Action(make))
        threading.Thread(target=self._safe(self._keys), daemon=True, name="scorpi-keys").start()

    # ---------- geometry ----------

    def _scale(self):
        try:
            return (user32.GetDpiForWindow(self._form.Handle.ToInt64()) or 96) / 96.0
        except Exception:
            return 1.0

    def _canvas(self):
        return int(SIZES.get(self._prefs.get("size"), 96) * self._scale())

    def _feet_offset(self):
        return self._canvas() * 68 / 80.0  # Scorpi's feet (in the cloud) from the top of its window

    def _form_hwnd(self):
        return self._form.Handle.ToInt64() if self._form is not None else 0

    def _card_hwnd(self):
        return self._card.native.Handle.ToInt64()

    def _real(self, hwnd):
        if not hwnd or int(hwnd) in self._own or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return False
        cloaked = ctypes.c_int(0)
        dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if cloaked.value or _text(user32.GetClassNameW, hwnd) in SKIP_CLASSES or not _text(user32.GetWindowTextW, hwnd).strip():
            return False
        if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & (WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT):
            return False
        l, t, r, b = window_rect(hwnd)
        return r - l > 160 and b - t > 100

    def _window_at(self, x, y):
        hwnd = user32.GetTopWindow(None)
        while hwnd:
            if self._real(hwnd):
                l, t, r, b = window_rect(hwnd)
                if l <= x < r and t <= y < b:
                    return hwnd
            hwnd = user32.GetWindow(hwnd, GW_HWNDNEXT)
        return None

    def _windows(self):
        out = []
        hwnd = user32.GetTopWindow(None)
        while hwnd and len(out) < 40:
            if self._real(hwnd) and not user32.IsZoomed(hwnd):
                out.append((hwnd, window_rect(hwnd)))
            hwnd = user32.GetWindow(hwnd, GW_HWNDNEXT)
        return out

    def _clamp(self):
        """Keep the cloud inside the screen it is on."""
        size = self._canvas()
        l, t, r, b = work_area(self._fx, self._fy)
        self._fx = float(min(max(self._fx, l + size * 0.45), r - size * 0.45))
        self._fy = float(min(max(self._fy, t + size * 0.85), b - size * 0.12))

    # ---------- showing ----------

    def _show(self, on):
        hwnd = self._form_hwnd()
        if not hwnd:
            return
        if on and self._state == "hidden":
            self._state = "float"
            self._render()
            user32.ShowWindowAsync(hwnd, SW_SHOWNOACTIVATE)
            user32.SetWindowPos(hwnd, ctypes.c_void_p(HWND_TOPMOST), 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
            self._flash = ("happy", time.time() + 1.4)
        elif not on and self._state != "hidden":
            self._state = "hidden"
            user32.ShowWindowAsync(hwnd, SW_HIDE)
            self.card(False)

    def _set_detached(self, on):
        self._save(detached=bool(on))
        self._summoned = False
        if on:
            self._show(True)
        else:
            self._show(False)
        if self._on_change:
            self._on_change(bool(on))

    def _summon(self):
        """The push-to-talk key while Scorpi lives in the app: it floats out next to the mouse, and stays out."""
        if self._state != "hidden":
            return
        x, y = cursor()
        size = self._canvas()
        self._fx, self._fy = float(x + size), float(y + size * 0.4)
        self._clamp()
        self._set_detached(True)
        self._summoned = True

    def _keep_shown(self, now):
        """Win+D, "minimize all" or another always-on-top window must not make Scorpi vanish."""
        if now < self._keep_at:
            return
        self._keep_at = now + 1.0
        hwnd = self._form_hwnd()
        if user32.IsIconic(hwnd) or not user32.IsWindowVisible(hwnd):
            user32.ShowWindowAsync(hwnd, 9)  # SW_RESTORE
            user32.ShowWindowAsync(hwnd, SW_SHOWNOACTIVATE)
        user32.SetWindowPos(hwnd, ctypes.c_void_p(HWND_TOPMOST), 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_ASYNC)

    # ---------- the animation loop (UI thread) ----------

    def _tick(self):
        now = time.time()
        dt = min(0.1, now - self._last_tick)
        self._last_tick = now
        if self._state == "hidden" or self._form is None:
            return
        self._keep_shown(now)
        self._move(now, dt)
        self._render()

    def _current_mood(self, now):
        if self._state == "drag":
            return "lifted"
        if self._flash and now < self._flash[1]:
            return self._flash[0]
        if self._mood != "idle":
            return self._mood
        if idle_seconds() > 180:
            return "sleeping"
        return "idle"

    def _move(self, now, dt):
        s = self._scale()
        if self._state == "drag":
            x, y = cursor()
            nx, ny = x + self._drag["ox"], y + self._drag["oy"]
            if abs(nx - self._fx) > 1:
                self._facing = 1 if nx > self._fx else -1
            self._fx, self._fy = float(nx), float(ny)
            if not key_down(VK_LBUTTON):
                self._drop()
            return
        # drifting: a soft spring toward the target, never faster than a slow breeze
        if self._target is not None:
            tx, ty = self._target
            ax, ay = (tx - self._fx) * 1.6, (ty - self._fy) * 1.6
            self._vx = (self._vx + ax * dt) * (1 - min(0.9, 2.2 * dt))
            self._vy = (self._vy + ay * dt) * (1 - min(0.9, 2.2 * dt))
            speed = math.hypot(self._vx, self._vy)
            top = 70 * s
            if speed > top:
                self._vx, self._vy = self._vx * top / speed, self._vy * top / speed
            self._fx += self._vx * dt
            self._fy += self._vy * dt
            if abs(self._vx) > 12 * s:
                self._facing = 1 if self._vx > 0 else -1
            if math.hypot(tx - self._fx, ty - self._fy) < 3 * s and speed < 8 * s:
                self._target = None
                self._vx = self._vy = 0.0
                self._save(pos=[self._fx, self._fy])
        self._decide(now)

    def _decide(self, now):
        """Now and then, while nothing else is going on: drift somewhere nearby, turn around, or make a face."""
        if now < self._decide_at:
            return
        self._decide_at = now + random.uniform(7, 16)
        if self._target is not None or self._current_mood(now) != "idle" or self._card_shown or self._hover or not self._prefs.get("roam", True):
            return
        roll = random.random()
        s = self._scale()
        if roll < 0.5:
            size = self._canvas()
            l, t, r, b = work_area(self._fx, self._fy)
            tx = self._fx + random.uniform(-420, 420) * s
            ty = self._fy + random.uniform(-160, 160) * s
            tx = min(max(tx, l + size * 0.6), r - size * 0.6)
            ty = min(max(ty, t + (b - t) * 0.3), b - size * 0.2)  # mostly the lower part of the screen, out of the way
            if math.hypot(tx - self._fx, ty - self._fy) > 30 * s:
                self._target = (tx, ty)
        elif roll < 0.65:
            self._facing = -self._facing
        elif roll < 0.8:
            self._flash = (random.choice(["curious", "happy", "shy"]), now + 1.6)

    def _render(self):
        if self._form is None or self._painter is None:
            return
        now = time.time()
        mood = self._current_mood(now)
        # blinking
        if now > self._blink_at:
            self._blink_at = now + random.uniform(2.4, 6.0)
            self._blink_t = now
        bt = now - getattr(self, "_blink_t", 0)
        blink = max(0.0, 1 - abs(bt - 0.07) / 0.07) if bt < 0.14 else 0.0
        # gaze: the mouse when it is near, else wander
        cx, cy = cursor()
        size = self._canvas()
        ex, ey = self._fx, self._fy - size * 0.4
        d = math.hypot(cx - ex, cy - ey)
        if d < 900:
            k = min(1.0, d / 260) / (d or 1)
            target = [(cx - ex) * k, (cy - ey) * k]
        else:
            if now > self._wander_at:
                self._wander_at = now + random.uniform(1.6, 4.2)
                self._wander = [random.uniform(-0.7, 0.7), random.uniform(-0.45, 0.5)]
            target = getattr(self, "_wander", [0.0, 0.0])
        self._gaze = [g + (t - g) * 0.3 for g, t in zip(self._gaze, target)]
        gaze = (-self._gaze[0], self._gaze[1]) if self._facing < 0 else tuple(self._gaze)
        t = now - self._t0
        pose = paint.pose_for(mood, t, gaze=gaze, blink=blink, talk=self._talk)
        pose["flip"] = self._facing < 0
        pose["cloud"] = True
        drifting = self._target is not None and math.hypot(self._vx, self._vy) > 10 * self._scale()
        pose["lean"] = pose.get("lean", 0.0) + (max(-6.0, min(6.0, -self._vx / (12 * self._scale()))) if drifting else 1.5 * math.sin(t * 0.8))
        bump = now - getattr(self, "_land_bump", 0)
        if bump < 0.35:
            pose["squash"] = math.sin(bump / 0.35 * math.pi) * 0.9
        self._bmp = self._painter.render(size, pose, self._bmp)
        # the whole cloud bobs; asleep it barely moves
        bob = math.sin(t * (0.9 if mood == "sleeping" else 1.6)) * (2.0 if mood == "sleeping" else 4.5) * self._scale()
        if self._state == "drag":
            bob = -6 * self._scale()
        self._blit(int(self._fx - size / 2), int(self._fy - self._feet_offset() + bob), size)

    def _blit(self, x, y, size):
        from System.Drawing import Color
        hwnd = self._form_hwnd()
        hbm = self._bmp.GetHbitmap(Color.FromArgb(0))
        hbm = ctypes.c_void_p(hbm.ToInt64())
        screen = user32.GetDC(None)
        mem = gdi32.CreateCompatibleDC(screen)
        old = gdi32.SelectObject(mem, hbm)
        try:
            blend = BLENDFUNCTION(0, 0, 255, 1)
            user32.UpdateLayeredWindow(hwnd, screen, ctypes.byref(POINT(x, y)), ctypes.byref(SIZE(size, size)), mem,
                                       ctypes.byref(POINT(0, 0)), 0, ctypes.byref(blend), ULW_ALPHA)
        finally:
            gdi32.SelectObject(mem, old)
            gdi32.DeleteObject(hbm)
            gdi32.DeleteDC(mem)
            user32.ReleaseDC(None, screen)
        if self._card_shown:
            self._place_card()

    # ---------- the mouse ----------

    def _on_down(self, _s, e):
        import System.Windows.Forms as WF
        if e.Button != WF.MouseButtons.Left:
            return
        x, y = cursor()
        self._press = (x, y, time.time())
        self._drag = {"ox": self._fx - x, "oy": self._fy - y}

    def _on_move(self, _s, e):
        x, _y = cursor()
        if getattr(self, "_press", None) and self._state != "drag":
            px, py, _t = self._press
            if abs(x - px) + abs(_y - py) > 6 * self._scale():
                self._state = "drag"
                self._target = None
        # petting: stroke back and forth over Scorpi
        lx, ldir, turns = self._pet
        d = (x > lx) - (x < lx)
        if d and d != ldir:
            turns += 1
            if turns >= 5:
                turns = 0
                self._flash = ("shy", time.time() + 1.8)
        self._pet = (x, d or ldir, turns)

    def _on_up(self, _s, e):
        import System.Windows.Forms as WF
        press = getattr(self, "_press", None)
        self._press = None
        if e.Button == WF.MouseButtons.Right:
            return self._menu()
        if e.Button != WF.MouseButtons.Left or not press:
            return
        if self._state == "drag":
            return self._drop()
        self._event({"type": "tap"})
        self._glance_now()

    def _on_enter(self, _s, _e):
        self._hover = True
        if self._current_mood(time.time()) == "idle" and random.random() < 0.6:
            self._flash = ("happy", time.time() + 1.0)

    def _drop(self):
        """Let go: the cloud stays right there (inside the screen)."""
        self._state = "float"
        self._drag = None
        self._vx = self._vy = 0.0
        self._clamp()
        self._land_bump = time.time()
        self._flash = ("happy", time.time() + 0.9)
        self._save(pos=[self._fx, self._fy])

    def _menu(self):
        self._event({"type": "menu", "prefs": self.public_prefs(), "summoned": self._summoned})
        self.card(True, mode="menu")

    # ---------- push-to-talk (a background thread polls the keys) ----------

    def _keys(self):
        phase = "idle"
        started = 0.0
        while not self._quitting:
            time.sleep(0.015)
            if not self._prefs.get("ptt", True):
                phase = "idle"
                continue
            groups = parse_hotkey(self._prefs.get("hotkey", "ctrl+win"))
            if not groups:
                continue
            combo = {vk for g in groups for vk in g}
            down = all(any(key_down(vk) for vk in g) for g in groups)
            if phase == "idle":
                if down:
                    phase, started = "arming", time.time()
                    if any(vk in combo for vk in (0x5B, 0x5C)):
                        user32.keybd_event(0xE8, 0, 0, None)  # a key nobody uses: Windows won't open Start when Win goes up
                        user32.keybd_event(0xE8, 0, 2, None)
            elif phase == "arming":
                if not down:
                    phase = "idle"
                    if time.time() - started < HOLD_MS / 1000.0:
                        self._on_ui(lambda: (self._glance_now(), self._ensure_out(), self._event({"type": "tap"})))
                    continue
                if any(key_down(vk) for vk in OTHER_KEYS if vk not in combo):
                    phase = "blocked"  # Ctrl+Win+Left and friends: a shortcut, not push-to-talk
                    continue
                if time.time() - started >= HOLD_MS / 1000.0:
                    phase = "talking"
                    self._on_ui(lambda: (self._glance_now(), self._ensure_out(), self._event({"type": "ptt-down"})))
            elif phase == "talking":
                if not down:
                    phase = "idle"
                    self._on_ui(lambda: self._event({"type": "ptt-up"}))
            elif phase == "blocked" and not down:
                phase = "idle"

    def _on_ui(self, fn):
        from System import Action
        form = self._form
        if form is not None and not form.IsDisposed:
            form.BeginInvoke(Action(self._safe(fn)))

    def _ensure_out(self):
        if self._state == "hidden":
            self._summon()
        self._busy_until = time.time() + 20

    def _glance_now(self):
        """What the user is looking at right now: the foreground window, where the mouse is on it, and (in a thread)
        a picture of it and a browser's address."""
        hwnd = user32.GetForegroundWindow()
        if not hwnd or int(hwnd) in self._own or not self._real(hwnd):
            self._glance = {"info": None, "ready": threading.Event()}
            self._glance["ready"].set()
            return
        l, t, r, b = window_rect(hwnd)
        info = {"hwnd": int(hwnd), "title": _text(user32.GetWindowTextW, hwnd)[:200], "process": process_name(hwnd),
                "rect": {"x": l, "y": t, "w": r - l, "h": b - t}}
        mx, my = cursor()
        if l <= mx < r and t <= my < b:
            info["pointer"] = {"x": mx - l, "y": my - t}
        glance = {"info": info, "shot": "", "url": "", "ready": threading.Event()}
        self._glance = glance

        def work():
            try:
                if info["process"].lower() in BROWSERS:
                    glance["url"] = browser_url(hwnd)
                if self._prefs.get("peek", True):
                    glance["shot"] = snapshot(hwnd)
            finally:
                glance["ready"].set()

        threading.Thread(target=self._safe(work), daemon=True, name="scorpi-glance").start()

    # ---------- the talk card ----------

    def _event(self, ev):
        if self._card is None:
            return
        code = "window.companionEvent && companionEvent(%s)" % json.dumps(ev)
        threading.Thread(target=self._safe(lambda: self._card.evaluate_js(code)), daemon=True).start()

    def _place_card(self):
        if self._card is None:
            return
        s = self._scale()
        w = int(360 * s)
        h = int(max(48, min(self._card_h, 520)) * s)
        size = self._canvas()
        l, t, r, b = work_area(self._fx, self._fy - 4)
        top_of_scorpi = self._fy - size * 0.72
        x = int(min(max(self._fx - w / 2, l + 8), r - w - 8))
        y = int(top_of_scorpi - h - 10 * s)
        if y < t + 8:  # no room above: beside Scorpi
            y = int(min(max(self._fy - h, t + 8), b - h - 8))
            x = int(self._fx + size * 0.5) if self._fx + size * 0.5 + w < r - 8 else int(self._fx - size * 0.5 - w)
        key = (x, y, w, h)
        if key != getattr(self, "_card_at", None):
            self._card_at = key
            user32.SetWindowPos(self._card_hwnd(), ctypes.c_void_p(HWND_TOPMOST), x, y, w, h, SWP_NOACTIVATE | SWP_ASYNC)

    # ---------- the card page's API ----------

    def state(self):
        return {"prefs": self.public_prefs(), "out": self._state != "hidden", "summoned": self._summoned}

    def card(self, show=True, height=None, mode="talk"):
        """Show the card next to Scorpi (mode talk: without taking the focus; menu: focused, closes when it loses it)."""
        if self._card is None:
            return False
        if height:
            self._card_h = int(height)
        hwnd = self._card_hwnd()
        if show:
            if self._state == "hidden":
                self._summon()
            self._card_mode = mode
            self._card_shown = True
            self._card_at = None
            self._place_card()
            user32.ShowWindowAsync(hwnd, SW_SHOWNOACTIVATE)
            if mode == "menu":
                ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex & ~WS_EX_NOACTIVATE)
                from System import Action
                self._card.native.BeginInvoke(Action(self._safe(lambda: (self._card.native.Activate(), user32.SetForegroundWindow(hwnd)))))
        else:
            self._card_shown = False
            user32.ShowWindowAsync(hwnd, SW_HIDE)
            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_NOACTIVATE)
        return True

    def mood(self, name="idle", level=0.0, flash=0):
        """The card says what Scorpi is doing: listening, thinking, working, talking (level = mouth), idle."""
        name = name if name in paint.MOODS else "idle"
        if flash:
            self._flash = (name, time.time() + float(flash) / 1000.0)
        else:
            self._mood = name
        self._talk = max(0.0, min(1.0, float(level or 0)))
        self._busy_until = time.time() + (6 if name == "idle" else 30)
        return True

    def glance(self, wait=True):
        """What the user was looking at when they pressed the key (title, program, address, picture)."""
        g = self._glance
        if not g:
            return None
        if wait:
            g["ready"].wait(2.5)
        if not g.get("info"):
            return None
        return {**g["info"], "url": g.get("url") or "", "shot": g.get("shot") or ""}

    def set(self, patch):
        allowed = {k: v for k, v in (patch or {}).items() if k in DEFAULTS}
        if "detached" in allowed:
            self._set_detached(bool(allowed.pop("detached")))
        if allowed:
            self._save(**allowed)
            if "size" in allowed:
                self._bmp = None
        self._event({"type": "prefs", "prefs": self.public_prefs()})
        return self.public_prefs()

    def say(self, text):
        self._event({"type": "say", "text": str(text)[:400]})
        return True

    def open_main(self, path="/"):
        self._show_main(path)

    def dock(self):
        """Back into the app (onto the prompt box)."""
        self._set_detached(False)
        self._show_main(None)
        return True

    def detach(self):
        self._set_detached(True)
        return True

    def stay(self):
        """A summoned Scorpi stays on the desktop."""
        self._set_detached(True)
        return True
