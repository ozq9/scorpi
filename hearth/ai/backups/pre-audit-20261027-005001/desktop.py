"""Open Scorp as a desktop app window (no browser chrome).

Starts the Scorp server in the background if it is not running yet, then opens an app window:
  1. a frameless pywebview window, full screen over the Windows taskbar by default; the page's own taskbar holds
     the window buttons (minimize / full screen / close), and in a window it can be dragged and resized
  2. Microsoft Edge / Chrome in app mode (python desktop.py --browser, or when pywebview is missing)
  3. the default browser as a last resort

The pywebview window lives in the tray: closing it only hides it (the page stays loaded, so it reopens instantly),
and starting Scorp again brings the running window back instead of opening a second one.
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

from scorp.config import DATA_DIR, IS_WINDOWS, SETTINGS  # noqa: E402

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0)
INSTANCE_FILE = os.path.join(DATA_DIR, "desktop-instance.json")
WINDOW_FILE = os.path.join(DATA_DIR, "desktop-window.json")  # {"fullscreen": bool}


# ---------- one window: a second start asks the running one to show itself ----------

def signal_running(path):
    """Ask an already running Scorp window to come forward (and open `path`). True when one answered."""
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
            "--no-default-browser-check", "--disable-features=TranslateUI",
            "--autoplay-policy=no-user-gesture-required"]  # "play some jazz" in chat starts the radio without a click
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
        """Scorpi lives on the prompt box or, detached, free on the desktop (scorpi_desktop.Companion).
        action: state | detach (x, y: where on screen it jumps out, CSS pixels) | dock."""
        comp = self._tray.companion if self._tray else None
        if not comp or comp._form is None:
            return {"desktop": False, "detached": False}
        if action == "detach":
            if x is not None and y is not None:
                s = comp._scale()
                comp._fx, comp._fy = float(x) * s, float(y) * s
                comp._state = "hidden"
            comp._on_ui(lambda: (comp._clamp(), comp.detach()))
        elif action == "dock":
            comp._on_ui(comp.dock)
        return {"desktop": True, "detached": bool(comp._prefs.get("detached", False))}

    def companion(self, action="get", data=None):
        """Settings → Scorpi: get | set (data: patch) | say (data: text)."""
        comp = self._tray.companion if self._tray else None
        if not comp:
            raise RuntimeError("Scorpi's desktop companion is not running.")
        if action == "set":
            done = {}
            evt = threading.Event()
            comp._on_ui(lambda: (done.update(comp.set(data or {})), evt.set()))
            evt.wait(3)
            return done or comp.public_prefs()
        if action == "say":
            comp._on_ui(lambda: comp.say(str(data or "")))
            return True
        return comp.public_prefs()

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


def _allow_permissions(window, base):
    """Let Scorp's own pages use the microphone and notifications without a WebView2 prompt every time."""
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


ICON_PATH = os.path.join(HERE, "public", "icons", "scorp.ico")


def _set_window_icon(window):
    """Show Scorp's icon in the taskbar and title instead of the Python one."""
    try:
        from System import Action
        from System.Drawing import Icon

        def apply():
            window.native.Icon = Icon(ICON_PATH)

        window.native.Invoke(Action(apply))
    except Exception:
        pass  # keeps the default icon


STARTUP_LINK = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup\Scorp.lnk")
OLD_STARTUP_LINK = os.path.join(os.path.dirname(STARTUP_LINK), "Hearth.lnk")  # made under the app's old name


def _set_startup(enabled):
    """Start Scorp hidden in the tray at sign-in (a shortcut in the Startup folder), or stop doing so."""
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
              "$s.Description = 'Scorp in the tray'; $s.Save()") % (
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
    """Scorp's tray icon. Closing the window hides it here; the page keeps running (reminders, notifications)."""

    def __init__(self, window, base):
        self.window = window
        self.base = base
        self.icon = None
        self.quitting = False
        self.hinted = False
        self.companion = None  # Scorpi on the desktop (scorpi_desktop.Companion), when it runs

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
        if os.path.exists(OLD_STARTUP_LINK):
            # starting with Windows was set up under the old name: the same, as Scorp
            os.remove(OLD_STARTUP_LINK)
            threading.Thread(target=_safe(_set_startup), args=(True,), daemon=True).start()
        startup = add("Start with Windows", lambda: None)
        startup.CheckOnClick = True
        startup.Checked = os.path.exists(STARTUP_LINK)
        startup.Click += _safe(lambda _s, _e: threading.Thread(target=_safe(_set_startup), args=(startup.Checked,), daemon=True).start())
        menu.Opening += _safe(lambda _s, _e: setattr(startup, "Checked", os.path.exists(STARTUP_LINK)))  # changed elsewhere too
        if self.companion:
            free = add("Scorpi on the desktop", lambda: None)
            free.CheckOnClick = True
            free.Checked = self.companion._prefs.get("detached", False)
            free.Click += _safe(lambda _s, _e: self.companion._set_detached(free.Checked))
            menu.Opening += _safe(lambda _s, _e: setattr(free, "Checked", self.companion._prefs.get("detached", False)))
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
                self.icon.ShowBalloonTip(4000, "Scorp is still running", "Click the tray icon to open it again. Quit from its menu.",
                                       WinForms.ToolTipIcon.Info)  # pythonnet 3 takes no plain ints for enums

        # the page stops listening to the microphone before it disappears
        self._js("dispatchEvent(new Event('scorp:hidden'))")
        self._ui(run)

    def quit(self):
        self.quitting = True
        if self.companion:
            self.companion._quitting = True
        self.window.destroy()


def _style_bg(default):
    """The active style's page colour (Studio), so a window shows it instead of black before its page has loaded."""
    try:
        with open(os.path.join(DATA_DIR, "looks.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        bg = ((data.get("styles") or {}).get(data.get("active") or "") or {}).get("vars", {}).get("--bg", "")
        return bg if len(bg) == 7 and bg.startswith("#") else default
    except (OSError, ValueError, AttributeError):
        return default


def open_companion(webview, base, show_main, on_change=None):
    """Scorpi on the desktop: a native see-through Scorpi (created once the main window exists) and its talk card."""
    import scorpi_desktop
    comp = scorpi_desktop.Companion(os.path.join(DATA_DIR, "ember.json"), show_main, on_change, safe=_safe)
    card = webview.create_window("Scorpi", base + "/companion.html", js_api=comp, width=360, height=140, min_size=(200, 40),
                                 frameless=True, easy_drag=False, on_top=True, resizable=False, hidden=True,
                                 background_color=_style_bg("#141418"))
    comp._attach_card(card)
    ready = []

    def on_loaded():
        if ready:
            return
        ready.append(True)
        _allow_permissions(card, base)  # the card listens through the microphone
        _safe(comp._setup_card)()

    def on_closing():
        if comp._quitting:
            return None
        comp.card(False)  # Alt+F4 only hides the card
        return False

    card.events.loaded += on_loaded
    card.events.closing += on_closing
    return comp


def open_webview(url, base, hidden=False):
    # audio may start without a click first: Ember's window listens for its wake word from the moment it opens
    # (this variable replaces pywebview's own browser arguments, so they are repeated here)
    os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
                          "--disable-features=ElasticOverscroll,CalculateNativeWinOcclusion --autoplay-policy=no-user-gesture-required "
                          "--disable-background-timer-throttling --disable-renderer-backgrounding --disable-backgrounding-occluded-windows "
                          "--enable-gpu-rasterization --gpu-rasterization-msaa-sample-count=8 --enable-zero-copy")
    import webview  # pip install pywebview
    controls = WindowControls()
    window = webview.create_window("Scorp", url, js_api=controls, width=1320, height=880, min_size=(380, 560),
                                   background_color=_style_bg("#000000"), text_select=True, hidden=hidden,
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

        tray.companion = open_companion(webview, base, tray.show, scorpi_moved)

        def start_companion():
            for _ in range(200):
                if window.native is not None:
                    break
                time.sleep(0.05)
            tray.companion._create_form(window.native)

        threading.Thread(target=_safe(start_companion), daemon=True, name="scorpi").start()
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
        return  # Scorp was already open: it is in front now
    if IS_WINDOWS:
        # own taskbar identity, so the window isn't grouped under (and shown as) Python
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Scorp.Desktop")
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
