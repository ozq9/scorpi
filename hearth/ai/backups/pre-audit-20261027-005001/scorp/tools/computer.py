"""Computer use: screen, mouse, keyboard, windows, and a real browser via Playwright.

Every observation of the screen comes two ways at once: a screenshot with numbered boxes on the clickable
controls (for vision models) and the same controls as a text list read from Windows UI Automation (enough for
text-only models). Actions can target an element number instead of pixels, which is far more reliable than
guessing coordinates, and several actions can run in one call with a single screenshot at the end.
"""

import base64
import ctypes
import io
import os
import re
import threading
import time

from ..config import DATA_DIR
from . import Result, ToolError, module_available, tool

_screen = {"scale": 1.0, "elements": {}, "size": (0, 0)}
MAX_SHOT_WIDTH = 1280
MAX_ELEMENTS = 140
_MARK_COLORS = [(230, 25, 75), (0, 130, 200), (245, 130, 48), (60, 160, 60), (145, 30, 180), (0, 150, 150), (200, 0, 120), (128, 96, 0)]
_dpi_done = []


def _dpi_aware():
    """Screenshots, UI Automation rectangles and mouse coordinates must all use physical pixels."""
    if _dpi_done or os.name != "nt":
        return
    _dpi_done.append(True)
    for attempt in (lambda: ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)),
                    lambda: ctypes.windll.shcore.SetProcessDpiAwareness(2),
                    lambda: ctypes.windll.user32.SetProcessDPIAware()):
        try:
            if attempt():
                return
        except Exception:
            continue


def _gui():
    if not module_available("pyautogui"):
        raise ToolError("Computer control needs: pip install pyautogui")
    _dpi_aware()
    import pyautogui
    pyautogui.FAILSAFE = True  # slam the mouse into a corner to abort
    pyautogui.PAUSE = 0.02
    return pyautogui


def _encode(image, quality=72):
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode()


def _grab():
    """Full-resolution screenshot of the primary screen (mss is several times faster than pyautogui)."""
    _dpi_aware()
    if module_available("mss"):
        try:
            import mss
            from PIL import Image
            with mss.mss() as sct:
                mon = next((m for m in sct.monitors[1:] if m["left"] == 0 and m["top"] == 0), sct.monitors[1])
                raw = sct.grab(mon)
                return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        except Exception:
            pass
    return _gui().screenshot()


def _settle(max_wait=1.6):
    """Wait until the screen stops changing (page loads, animations) instead of sleeping a fixed time."""
    from PIL import ImageChops, ImageStat
    time.sleep(0.08)
    deadline = time.time() + max_wait
    prev = _grab().convert("L").resize((96, 54))
    while time.time() < deadline:
        time.sleep(0.09)
        cur = _grab().convert("L").resize((96, 54))
        if ImageStat.Stat(ImageChops.difference(prev, cur)).mean[0] < 0.4:
            return
        prev = cur


# ---------- UI Automation: the clickable controls on screen ----------

_INTERACTIVE = ("Button", "Edit", "ComboBox", "CheckBox", "RadioButton", "MenuItem", "ListItem", "TabItem", "TreeItem",
                "Hyperlink", "DataItem", "SplitButton", "Slider", "Spinner")


def _clean(text, limit=60):
    text = re.sub(r"\s+", " ", (text or "").replace("\xad", "")).strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _collect_elements():
    """Visible interactive controls of the foreground window and the taskbar, in reading order (screen pixels)."""
    if os.name != "nt" or not module_available("uiautomation"):
        return "", []
    _dpi_aware()
    try:
        import uiautomation as auto
        from uiautomation import uiautomation as U
        with auto.UIAutomationInitializerInThread():
            return _collect(auto, U)
    except Exception:
        return "", []


def _collect(auto, U):
    ia = U._AutomationClient.instance().IUIAutomation
    prop = U.PropertyId
    request = ia.CreateCacheRequest()
    for pid in (prop.NameProperty, prop.ControlTypeProperty, prop.BoundingRectangleProperty, prop.IsEnabledProperty,
                prop.ValueValueProperty):
        request.AddProperty(pid)
    kinds = ia.CreateOrConditionFromArray([ia.CreatePropertyCondition(prop.ControlTypeProperty, getattr(U.ControlType, k + "Control"))
                                           for k in _INTERACTIVE])
    condition = ia.CreateAndConditionFromArray([kinds, ia.CreatePropertyCondition(prop.IsOffscreenProperty, False)])
    names = {getattr(U.ControlType, k + "Control"): k for k in _INTERACTIVE}

    fg = auto.GetForegroundControl()
    title = _clean(fg.Name, 90) if fg else ""
    roots = [fg] if fg else []
    tray = ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None)
    if tray and (not fg or fg.NativeWindowHandle != tray):
        roots.append(auto.ControlFromHandle(tray))
    sw, sh = ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)

    found, seen = [], set()
    for root in roots:
        try:
            r = root.BoundingRectangle
            bounds = (max(r.left, 0), max(r.top, 0), min(r.right, sw), min(r.bottom, sh))
            array = root.Element.FindAllBuildCache(4, condition, request)  # 4 = TreeScope_Descendants
            if not array.Length and root is fg:
                # Chromium/Electron apps build their accessibility tree only once someone asks: ask again shortly
                time.sleep(0.3)
                array = root.Element.FindAllBuildCache(4, condition, request)
        except Exception:
            continue
        batch = []
        for i in range(array.Length):
            el = array.GetElement(i)
            rect = el.CachedBoundingRectangle
            left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
            if right - left < 3 or bottom - top < 3:
                continue
            cx, cy = (left + right) // 2, (top + bottom) // 2
            if not (bounds[0] <= cx < bounds[2] and bounds[1] <= cy < bounds[3]):
                continue  # scrolled out of the window or off screen
            kind = names.get(el.CachedControlType, "Control")
            name = _clean(el.CachedName)
            key = (left // 4, top // 4, right // 4, bottom // 4)
            if key in seen:
                continue
            seen.add(key)
            value = ""
            if kind in ("Edit", "ComboBox"):
                try:
                    value = _clean(str(el.GetCachedPropertyValue(prop.ValueValueProperty) or ""), 50)
                except Exception:
                    value = ""
            batch.append({"kind": kind, "name": name, "value": value, "enabled": bool(el.CachedIsEnabled),
                          "rect": (max(left, bounds[0]), max(top, bounds[1]), min(right, bounds[2]), min(bottom, bounds[3]))})
        batch.sort(key=lambda e: (e["rect"][1] // 12, e["rect"][0]))
        found.extend(batch)
        if len(found) >= MAX_ELEMENTS:
            break
    return title, found[:MAX_ELEMENTS]


# ---------- observing the screen ----------

def _font(size=12):
    from PIL import ImageFont
    for name in ("segoeuib.ttf", "arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _draw_marks(shot, elements, scale):
    from PIL import ImageDraw
    draw = ImageDraw.Draw(shot)
    font = _font(11)
    for i, el in enumerate(elements, 1):
        color = _MARK_COLORS[i % len(_MARK_COLORS)]
        x1, y1, x2, y2 = [int(v * scale) for v in el["rect"]]
        draw.rectangle([x1, y1, x2, y2], outline=color, width=1)
        label = str(i)
        tw = int(draw.textlength(label, font=font)) + 4
        ty = y1 - 13 if y1 >= 13 else y1
        draw.rectangle([x1, ty, x1 + tw, ty + 13], fill=color)
        draw.text((x1 + 2, ty), label, fill=(255, 255, 255), font=font)


def _draw_grid(shot):
    from PIL import ImageDraw
    draw = ImageDraw.Draw(shot)
    for gx in range(100, shot.size[0], 100):
        draw.line([(gx, 0), (gx, 6)], fill=(255, 0, 80), width=1)
        draw.text((gx + 2, 6), str(gx), fill=(255, 0, 80))
    for gy in range(100, shot.size[1], 100):
        draw.line([(0, gy), (6, gy)], fill=(255, 0, 80), width=1)
        draw.text((8, gy - 5), str(gy), fill=(255, 0, 80))


def capture(grid=True, marks=True):
    """Screenshot (scaled to MAX_SHOT_WIDTH) with numbered element boxes. Returns (image, path, real size, title, elements)."""
    shot = _grab()
    width, height = shot.size
    scale = min(1.0, MAX_SHOT_WIDTH / float(width))
    if scale < 1.0:
        shot = shot.resize((int(width * scale), int(height * scale)))
    title, elements = _collect_elements() if marks else ("", [])
    _screen.update(scale=scale, size=(width, height),
                   elements={i: el for i, el in enumerate(elements, 1)})
    if grid:
        _draw_grid(shot)
    if elements:
        _draw_marks(shot, elements, scale)
    path = os.path.join(DATA_DIR, "workspace", "screens", "screen-%s.jpg" % time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    shot.convert("RGB").save(path, quality=80)
    return shot, path, (width, height), title, elements


def _describe_elements(elements, scale):
    lines = []
    for i, el in enumerate(elements, 1):
        x1, y1, x2, y2 = el["rect"]
        cx, cy = int((x1 + x2) / 2 * scale), int((y1 + y2) / 2 * scale)
        line = "[%d] %s %s" % (i, el["kind"], '"%s"' % el["name"] if el["name"] else "(unnamed)")
        if el["value"]:
            line += ' value="%s"' % el["value"]
        if not el["enabled"]:
            line += " (disabled)"
        lines.append(line + " @%d,%d" % (cx, cy))
    return "\n".join(lines)


def observe(note=""):
    shot, path, (w, h), title, elements = capture()
    head = "%sScreenshot %dx%d (real screen %dx%d). Active window: %s." % (
        (note + "\n") if note else "", shot.size[0], shot.size[1], w, h, '"%s"' % title if title else "unknown")
    if elements:
        body = ("Clickable elements (numbered boxes on the screenshot; act on them with element=N, "
                "@x,y is the centre in screenshot pixels):\n" + _describe_elements(elements, _screen["scale"]))
    else:
        body = ("No element list for this window (it does not expose accessibility info, e.g. games or custom-drawn apps): "
                "use screenshot pixel coordinates; red ticks every 100px help.")
    return Result(head + "\n" + body, display={"type": "image", "path": path, "caption": note or title or "Screen"},
                  images=[_encode(shot)])


def _to_screen(x, y):
    scale = _screen["scale"] or 1.0
    return int(round(float(x) / scale)), int(round(float(y) / scale))


def _point(element=None, x=None, y=None):
    """Screen pixel for an element number from the latest observation, or for screenshot coordinates."""
    if element not in (None, "", 0):
        try:
            el = _screen["elements"][int(element)]
        except (KeyError, ValueError, TypeError):
            raise ToolError("Element %s is not on the latest screenshot. Take a new screenshot and use its numbers." % element)
        x1, y1, x2, y2 = el["rect"]
        return (x1 + x2) // 2, (y1 + y2) // 2
    if x is None or y is None:
        raise ToolError("Give an element number or x and y.")
    return _to_screen(x, y)


# ---------- actions (shared by the single tools and computer_actions) ----------

_KEY_ALIAS = {"win": "winleft", "windows": "winleft", "cmd": "command", "return": "enter", "esc": "escape", "del": "delete",
              "pgdn": "pagedown", "pgup": "pageup", "control": "ctrl", "super": "winleft"}


def _press(gui, keys):
    for combo in keys.split():  # "ctrl+a delete" presses two combinations in a row
        parts = [_KEY_ALIAS.get(k.strip().lower(), k.strip().lower()) for k in combo.split("+") if k.strip()]
        if len(parts) == 1:
            gui.press(parts[0])
        elif parts:
            gui.hotkey(*parts)


def _type(gui, text):
    if len(text) <= 24 and all(32 <= ord(c) < 127 for c in text):
        gui.write(text, interval=0.008)
        return
    # long or non-ASCII text: paste it in one go, then put the user's clipboard back
    from .system import get_clipboard, set_clipboard
    try:
        previous = get_clipboard()
    except Exception:
        previous = None
    set_clipboard(text)
    gui.hotkey("ctrl", "v")
    time.sleep(0.12)
    if previous is not None:
        try:
            set_clipboard(previous)
        except Exception:
            pass


def _act(step):
    """Run one action. step: {do, element?, x?, y?, to_element?, to_x?, to_y?, text?, keys?, amount?, seconds?, title?, clear?}"""
    gui = _gui()
    do = (step.get("do") or step.get("action") or "").lower()
    if do in ("click", "double_click", "right_click", "move"):
        sx, sy = _point(step.get("element"), step.get("x"), step.get("y"))
        {"click": gui.click, "double_click": gui.doubleClick, "right_click": gui.rightClick, "move": gui.moveTo}[do](sx, sy)
        return "%s %s" % (do, _target_label(step))
    if do == "drag":
        sx, sy = _point(step.get("element"), step.get("x"), step.get("y"))
        tx, ty = _point(step.get("to_element"), step.get("to_x"), step.get("to_y"))
        gui.moveTo(sx, sy)
        gui.dragTo(tx, ty, duration=0.4, button="left")
        return "dragged %s" % _target_label(step)
    if do == "type":
        if step.get("element") not in (None, "", 0) or step.get("x") is not None:
            gui.click(*_point(step.get("element"), step.get("x"), step.get("y")))
            time.sleep(0.08)
        if step.get("clear"):
            gui.hotkey("ctrl", "a")
            gui.press("delete")
        _type(gui, str(step.get("text") or ""))
        if step.get("keys"):
            _press(gui, str(step["keys"]))
        return "typed %r%s" % (str(step.get("text") or "")[:40], " + " + step["keys"] if step.get("keys") else "")
    if do in ("key", "keys", "press", "hotkey"):
        _press(gui, str(step.get("keys") or step.get("text") or ""))
        return "pressed %s" % (step.get("keys") or step.get("text"))
    if do == "scroll":
        if step.get("element") not in (None, "", 0) or step.get("x") is not None:
            gui.moveTo(*_point(step.get("element"), step.get("x"), step.get("y")))
        amount = int(step.get("amount") or -5)
        gui.scroll(amount * 120 if os.name == "nt" else amount)
        return "scrolled %s" % amount
    if do == "wait":
        time.sleep(min(float(step.get("seconds") or 1), 10))
        return "waited"
    if do == "focus":
        return _focus_window(step.get("title") or "")
    raise ToolError("Unknown action %r." % do)


def _target_label(step):
    if step.get("element") not in (None, "", 0):
        el = _screen["elements"].get(int(step["element"]), {})
        return "[%s] %s" % (step["element"], ('"%s"' % el.get("name")) if el.get("name") else el.get("kind", ""))
    return "(%s, %s)" % (step.get("x"), step.get("y"))


def _focus_window(title):
    if not module_available("pygetwindow"):
        raise ToolError("Window control needs: pip install pygetwindow")
    import pygetwindow as gw
    matches = [w for w in gw.getAllWindows() if title and title.lower() in w.title.lower() and w.title.strip()]
    if not matches:
        raise ToolError("No window title contains %r." % title)
    win = matches[0]
    if win.isMinimized:
        win.restore()
    try:
        win.activate()
    except Exception:
        _gui().press("alt")  # Windows only lets the foreground app hand focus over; a key press counts
        win.activate()
    return "focused %s" % win.title


def _after(note, settle=True):
    if settle:
        _settle()
    return observe(note)


@tool(
    "screenshot",
    "See the host PC's screen. Returns the screenshot with numbered boxes on clickable controls plus the same controls "
    "as a text list ([N] kind \"name\" @x,y), so you can act with element=N even without seeing the image. "
    "Use zoom='x,y,w,h' (screenshot pixels) to read small text in a region at full resolution.",
    {"zoom?": "string: Region to magnify as x,y,w,h in screenshot pixels"},
    group="computer",
    requires=("pyautogui",),
)
def screenshot(ctx, zoom=""):
    if not zoom:
        return observe()
    try:
        x, y, w, h = [float(v) for v in re.split(r"[,\s]+", zoom.strip()) if v]
    except ValueError:
        raise ToolError("zoom must be x,y,w,h")
    full = _grab()
    scale = min(1.0, MAX_SHOT_WIDTH / float(full.size[0]))
    box = [int(v / scale) for v in (x, y, x + w, y + h)]
    crop = full.crop(box)
    factor = min(3.0, MAX_SHOT_WIDTH / float(max(crop.size[0], 1)))
    if factor > 1:
        crop = crop.resize((int(crop.size[0] * factor), int(crop.size[1] * factor)))
    path = os.path.join(DATA_DIR, "workspace", "screens", "zoom-%s.jpg" % time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    crop.convert("RGB").save(path, quality=85)
    return Result("Zoomed view of region %s (for reading only; keep using full-screenshot coordinates or element numbers to act)." % zoom,
                  display={"type": "image", "path": path, "caption": "Zoom"}, images=[_encode(crop, 85)])


@tool(
    "computer_actions",
    "FASTEST way to operate the PC: run several GUI actions in one call, then get one screenshot + element list. "
    "Each action: {do: click|double_click|right_click|move|drag|type|key|scroll|wait|focus, element?, x?, y?, "
    "to_element?, to_x?, to_y?, text?, keys?, clear?, amount?, seconds?, title?}. Target controls by element number "
    "from the latest screenshot (preferred) or by screenshot x,y. type clicks the element first when given, clear=true "
    "replaces existing text, keys after text are pressed afterwards (e.g. 'enter'). keys accepts combos like 'ctrl+s' "
    "and sequences like 'ctrl+a delete'. Element numbers stay valid only until the window changes, so batch the steps "
    "you can predict (e.g. focus field, type, enter) and look again before acting on new UI.",
    {
        "actions": {
            "type": "array",
            "description": "Actions to run in order",
            "items": {
                "type": "object",
                "properties": {
                    "do": {"type": "string", "enum": ["click", "double_click", "right_click", "move", "drag", "type", "key", "scroll", "wait", "focus"]},
                    "element": {"type": "integer", "description": "Element number from the latest screenshot"},
                    "x": {"type": "number"}, "y": {"type": "number"},
                    "to_element": {"type": "integer"}, "to_x": {"type": "number"}, "to_y": {"type": "number"},
                    "text": {"type": "string", "description": "Text to type"},
                    "keys": {"type": "string", "description": "Keys, e.g. enter, ctrl+l, alt+f4, 'ctrl+a delete'"},
                    "clear": {"type": "boolean", "description": "Select all and delete before typing"},
                    "amount": {"type": "integer", "description": "Scroll clicks: negative down, positive up"},
                    "seconds": {"type": "number", "description": "For wait"},
                    "title": {"type": "string", "description": "Window title text for focus"},
                },
                "required": ["do"],
            },
        },
    },
    group="computer",
    risk="danger",
    requires=("pyautogui",),
)
def computer_actions(ctx, actions):
    if not isinstance(actions, list) or not actions:
        raise ToolError("actions must be a non-empty list")
    done = []
    for i, step in enumerate(actions[:25], 1):
        if ctx.agent.cancelled():
            break
        try:
            done.append("%d. %s" % (i, _act(step if isinstance(step, dict) else {})))
        except ToolError as exc:
            result = _after("\n".join(done + ["%d. FAILED: %s (stopped here)" % (i, exc)]))
            result.ok = bool(done)
            return result
        last = (step.get("do") or "") if isinstance(step, dict) else ""
        if i < len(actions) and last in ("click", "double_click", "right_click", "key", "focus"):
            _settle(0.8)  # let menus/dialogs open before the next step
    return _after("Done:\n" + "\n".join(done))


@tool(
    "mouse",
    "Click or move the mouse on the host screen, targeting an element number from the latest screenshot (preferred) "
    "or screenshot x,y. A new screenshot is returned. For several steps use computer_actions.",
    {
        "action": "string[click,double_click,right_click,move,drag]: What to do",
        "element?": "integer: Element number from the latest screenshot",
        "x?": "number: X in screenshot pixels",
        "y?": "number: Y in screenshot pixels",
        "to_x?": "number: Drag target X",
        "to_y?": "number: Drag target Y",
        "to_element?": "integer: Drag target element",
    },
    group="computer",
    risk="danger",
    requires=("pyautogui",),
)
def mouse(ctx, action, element=None, x=None, y=None, to_x=None, to_y=None, to_element=None):
    note = _act({"do": action, "element": element, "x": x, "y": y, "to_x": to_x, "to_y": to_y, "to_element": to_element})
    return _after(note)


@tool(
    "keyboard",
    "Type text or press keys/shortcuts on the host. Examples: text='hello', keys='enter', keys='ctrl+l', keys='alt+tab', "
    "keys='win'. Give element=N to click into a field first, clear=true to replace its text.",
    {"text?": "string: Text to type", "keys?": "string: Key or combination separated by +, pressed after the text",
     "element?": "integer: Field to click first", "clear?": "boolean: Replace the field's existing text"},
    group="computer",
    risk="danger",
    requires=("pyautogui",),
)
def keyboard(ctx, text="", keys="", element=None, clear=False):
    if text or element or clear:
        note = _act({"do": "type", "text": text, "keys": keys, "element": element, "clear": clear})
    else:
        note = _act({"do": "key", "keys": keys})
    return _after(note)


@tool(
    "scroll",
    "Scroll the mouse wheel on the host, over an element, a point, or where the mouse is.",
    {"amount": "integer: Positive scrolls up, negative down (e.g. -5)", "element?": "integer: Element to scroll over",
     "x?": "number: X", "y?": "number: Y"},
    group="computer",
    risk="danger",
    requires=("pyautogui",),
)
def scroll(ctx, amount, element=None, x=None, y=None):
    return _after(_act({"do": "scroll", "amount": amount, "element": element, "x": x, "y": y}))


@tool(
    "windows",
    "List open windows, or focus/minimize/maximize/close one by (part of) its title. Focus and maximize return a screenshot.",
    {"action": "string[list,focus,minimize,maximize,close]: Operation", "title?": "string: Window title text"},
    group="computer",
    risk="write",
    requires=("pygetwindow",),
)
def windows(ctx, action="list", title=""):
    import pygetwindow as gw
    if action == "list":
        titles = [w.title for w in gw.getAllWindows() if w.title.strip()]
        return Result("Open windows:\n" + "\n".join("- " + t for t in titles[:80]))
    if action == "focus":
        note = _focus_window(title)
    else:
        matches = [w for w in gw.getAllWindows() if title and title.lower() in w.title.lower() and w.title.strip()]
        if not matches:
            raise ToolError("No window title contains %r." % title)
        win = matches[0]
        getattr(win, action)()
        note = "%s: %s" % (action, win.title)
    if action in ("focus", "maximize") and module_available("pyautogui"):
        return _after(note)
    return Result(note)


# ---------- browser automation ----------

class _Browser:
    """Playwright must stay on one thread, so all calls are funnelled through a worker."""

    def __init__(self):
        self.lock = threading.Lock()
        self.thread = None
        self.jobs = []
        self.cond = threading.Condition()
        self.page = None
        self.failed = ""

    def _worker(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            profile = os.path.join(DATA_DIR, "browser-profile")
            context = None
            for channel in ("msedge", "chrome", None):
                try:
                    kwargs = {"headless": False, "viewport": {"width": 1280, "height": 800}}
                    if channel:
                        kwargs["channel"] = channel
                    context = pw.chromium.launch_persistent_context(profile, **kwargs)
                    break
                except Exception:
                    continue
            if context is None:
                self.failed = "No browser found. Install Microsoft Edge/Chrome or run: playwright install chromium"
                return
            self.page = context.pages[0] if context.pages else context.new_page()
            context.on("page", lambda page: setattr(self, "page", page))  # follow links that open a new tab
            while True:
                with self.cond:
                    while not self.jobs:
                        self.cond.wait()
                    fn, box = self.jobs.pop(0)
                if self.page is None or self.page.is_closed():
                    open_pages = [p for p in context.pages if not p.is_closed()]
                    self.page = open_pages[-1] if open_pages else context.new_page()
                try:
                    box["value"] = fn(self.page, context)
                except Exception as exc:
                    box["error"] = exc
                if box.get("close"):
                    context.close()
                    self.page = None
                    box["done"].set()
                    self.thread = None
                    return
                box["done"].set()

    def call(self, fn, close=False, timeout=90):
        with self.lock:
            if self.thread is None or not self.thread.is_alive():
                self.failed = ""
                self.thread = threading.Thread(target=self._worker, daemon=True)
                self.thread.start()
        box = {"done": threading.Event(), "close": close}
        with self.cond:
            self.jobs.append((fn, box))
            self.cond.notify_all()
        deadline = time.time() + timeout
        while not box["done"].wait(0.5):
            thread = self.thread
            if self.failed or thread is None or not thread.is_alive():
                with self.cond:
                    if (fn, box) in self.jobs:
                        self.jobs.remove((fn, box))
                raise ToolError(self.failed or "The browser stopped unexpectedly.")
            if time.time() > deadline:
                raise ToolError("Browser action timed out.")
        if "error" in box:
            raise ToolError("Browser: %s" % box["error"])
        return box.get("value")


BROWSER = _Browser()

# Numbers every visible interactive element (data-scorp-id) and returns their labels and boxes.
_MARK_JS = """() => {
  document.querySelectorAll('[data-scorp-id]').forEach(e => e.removeAttribute('data-scorp-id'));
  const sel = 'a[href],button,input:not([type=hidden]),textarea,select,summary,[role=button],[role=link],[role=tab],' +
    '[role=menuitem],[role=checkbox],[role=radio],[role=option],[role=switch],[role=combobox],[contenteditable=""],[contenteditable=true],[onclick],[tabindex]:not([tabindex="-1"])';
  const out = []; const W = innerWidth, H = innerHeight;
  for (const e of document.querySelectorAll(sel)) {
    const r = e.getBoundingClientRect();
    if (r.width < 3 || r.height < 3 || r.bottom < 0 || r.right < 0 || r.top > H || r.left > W) continue;
    const s = getComputedStyle(e);
    if (s.visibility === 'hidden' || s.display === 'none' || +s.opacity === 0) continue;
    const cx = Math.min(Math.max(r.left + r.width / 2, 0), W - 1), cy = Math.min(Math.max(r.top + r.height / 2, 0), H - 1);
    const top = document.elementFromPoint(cx, cy);
    if (top && top !== e && !e.contains(top) && !top.contains(e)) continue;  // covered by something else
    const label = (e.getAttribute('aria-label') || e.innerText || e.value || e.placeholder || e.title || e.alt || e.name || '').trim().replace(/\\s+/g, ' ').slice(0, 70);
    const tag = e.tagName.toLowerCase();
    const kind = (e.getAttribute('role') || tag) + (tag === 'input' ? '[' + (e.type || 'text') + ']' : '');
    out.push({kind, label, rect: [r.left, r.top, r.right, r.bottom], el: e});
    if (out.length >= 150) break;
  }
  return out.map((o, i) => { o.el.setAttribute('data-scorp-id', String(i + 1)); delete o.el; return o; });
}"""


def _page_state(page):
    title = page.title()
    text = page.evaluate("() => document.body ? document.body.innerText.slice(0, 6000) : ''")
    try:
        marks = page.evaluate(_MARK_JS)
    except Exception:
        marks = []
    shot = page.screenshot(type="png")
    return title, page.url, text, marks, shot, page.viewport_size or {"width": 1280}


def _settle_page(page):
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        page.wait_for_load_state("networkidle", timeout=1500)
    except Exception:
        pass  # busy pages (ads, live feeds) never go idle; what loaded so far is enough
    page.wait_for_timeout(150)


@tool(
    "browser",
    "Control a real browser window on the host (Edge/Chrome, persistent logins). Every result lists the page's controls "
    "as [N] kind \"label\" (also numbered on the screenshot): target them with element=N. Actions: goto (url), "
    "click/hover (element or visible text/CSS selector), type (element/selector + text, optional submit), select "
    "(element + option text), press (key), scroll (amount, negative = down), read, back, forward, screenshot, close.",
    {
        "action": "string[goto,click,hover,type,select,press,scroll,read,back,forward,screenshot,close]: Action",
        "url?": "string: URL for goto",
        "element?": "integer: Element number from the latest browser result",
        "target?": "string: Visible text or CSS selector when no element number fits",
        "text?": "string: Text to type, or option to select",
        "submit?": "boolean: Press Enter after typing",
        "key?": "string: Key for press, e.g. Enter, PageDown, Control+A",
        "amount?": "integer: Scroll steps for scroll (negative = down)",
    },
    group="computer",
    risk="danger",
    requires=("playwright",),
)
def browser(ctx, action, url="", element=None, target="", text="", submit=False, key="", amount=-5):
    def locate(page):
        if element not in (None, "", 0):
            loc = page.locator('[data-scorp-id="%d"]' % int(element)).first
            if loc.count():
                return loc
            raise ToolError("Element %s is no longer on the page. Use the numbers from the latest result." % element)
        if not target:
            raise ToolError("element or target is required")
        if any(ch in target for ch in "#.[]>=:") and not target.strip().startswith(("text=", "'")):
            try:
                loc = page.locator(target).first
                if loc.count():
                    return loc
            except Exception:
                pass
        for getter in (lambda: page.get_by_role("button", name=target), lambda: page.get_by_role("link", name=target),
                       lambda: page.get_by_label(target), lambda: page.get_by_placeholder(target), lambda: page.get_by_text(target)):
            try:
                loc = getter().first
                if loc.count():
                    return loc
            except Exception:
                continue
        raise ToolError("Could not find %r on the page." % target)

    def job(page, context):
        if action == "close":
            return None
        if action == "goto":
            dest = url if url.startswith(("http://", "https://", "file:")) else "https://" + url
            page.goto(dest, wait_until="domcontentloaded", timeout=45000)
        elif action == "click":
            locate(page).click(timeout=10000)
        elif action == "hover":
            locate(page).hover(timeout=10000)
        elif action == "type":
            loc = locate(page)
            try:
                loc.fill(text, timeout=10000)
            except Exception:  # custom widgets (contenteditable, canvas editors) accept real key presses
                loc.click(timeout=10000)
                page.keyboard.type(text, delay=5)
            if submit:
                loc.press("Enter")
        elif action == "select":
            locate(page).select_option(label=text, timeout=10000)
        elif action == "press":
            page.keyboard.press(key or "Enter")
        elif action == "scroll":
            page.mouse.wheel(0, -int(amount or -5) * 120)
        elif action == "back":
            page.go_back()
        elif action == "forward":
            page.go_forward()
        _settle_page(BROWSER.page if BROWSER.page and not BROWSER.page.is_closed() else page)
        current = BROWSER.page if BROWSER.page and not BROWSER.page.is_closed() else page
        return _page_state(current)

    if action == "close":
        BROWSER.call(job, close=True)
        return Result("Browser closed.")
    title, current, page_text, marks, png, viewport = BROWSER.call(job)
    from PIL import Image
    shot = Image.open(io.BytesIO(png)).convert("RGB")
    scale = shot.size[0] / float(viewport.get("width") or shot.size[0])
    if marks:
        _draw_marks(shot, [{"rect": m["rect"]} for m in marks], scale)
    path = os.path.join(DATA_DIR, "workspace", "screens", "browser-%s.jpg" % time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    shot.save(path, quality=80)
    controls = "\n".join('[%d] %s "%s"' % (i, m["kind"], m["label"]) for i, m in enumerate(marks, 1))
    summary = "Page: %s\nURL: %s\n\nVisible text:\n%s\n\nControls (use element=N):\n%s" % (
        title, current, page_text[:5000], controls or "(none found)")
    return Result(summary, display={"type": "image", "path": path, "caption": "%s — %s" % (title, current)},
                  images=[_encode(shot)])
