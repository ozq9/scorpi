"""Scorpi drawn natively (GDI+ through pythonnet) for the desktop companion's see-through window.

The same white pixel scorpion as public/js/lib/mascot.js, from the same sprites (scorpi/sprites.py, which
scripts/make_scorpi.py writes together with the web copy). Every sprite pixel is a square of whole screen pixels, filled
without smoothing; a pose (mood, frame, gaze, blink, walk, hop, …) is turned into a premultiplied-alpha bitmap that
UpdateLayeredWindow puts on screen with per-pixel transparency. Scorpi stands on a short ledge instead of a cloud.
"""

import os
import re
import sys

import clr  # noqa: F401  (pythonnet, loaded by pywebview)

clr.AddReference("System.Drawing")
from System.Drawing import Bitmap, Color, Graphics, SolidBrush  # noqa: E402
from System.Drawing.Drawing2D import CompositingMode, InterpolationMode, PixelOffsetMode, SmoothingMode  # noqa: E402
from System.Drawing.Imaging import PixelFormat  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scorpi.sprites import SPRITES  # noqa: E402

W, H, FX_H = SPRITES["w"], SPRITES["h"], SPRITES["fxH"]
CANVAS = 40  # sprite pixels across the window: Scorpi is 32, the rest is room for hops
FEET = 68 / 80.0  # where the feet stand, as a share of the window's height (scorpi_desktop keeps the same)
PALETTE = {"body": "#EDEDED", "eye": "#EDEDED", "shadow": "#8F8F8F", "ledge": "#3A3A3A"}
SLOT = {1: "body", 2: "eye", 3: "shadow", 4: "ledge"}
# style tokens and the names the vector Scorpi used
ALIASES = {"--sc-body": "body", "--sc-eye": "eye", "--sc-shadow": "shadow", "mid": "body", "hi": "body", "lo": "shadow", "limb": "shadow"}

MOODS = {name: (m["eyes"], m["mouth"]) for name, m in SPRITES["moods"].items()}
# where a mood looks, overriding the pointer (same as mascot.js)
GAZE = {"thinking": (0.8, -0.9), "writing": (0.6, 0.8), "working": (0.0, 0.6), "sad": (0.0, 0.9), "sleeping": (0.0, 0.0), "shy": (0.0, 0.0)}


def rgba(hexstr, a=255):
    h = hexstr.lstrip("#")
    return Color.FromArgb(int(a), int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _frame(t, ms, n):
    """Which of n frames shows at t seconds when the n frames take ms together (CSS steps in the web Scorpi)."""
    return int(t * 1000.0 / (ms / float(n))) % n if n > 1 else 0


def compose(pose):
    """The pose as a grid of palette numbers, W wide and FX_H + H + 1 tall (effects, Scorpi, ledge)."""
    rows = FX_H + H + 1
    g = [[0] * W for _ in range(rows)]
    flip = pose.get("flip", False)

    def put(runs, dx=0, dy=0, mirror=False):
        for x, y, w, v in runs:
            for i in range(w):
                gx, gy = x + i + dx, y + dy
                if mirror:
                    gx = W - 1 - gx
                if 0 <= gx < W and 0 <= gy < rows:
                    g[gy][gx] = v

    m = SPRITES["moods"].get(pose.get("mood"), SPRITES["moods"]["idle"])
    t = pose.get("t", 0.0)
    n = len(m["frames"])
    f = _frame(t, m["ms"], n)
    if m["fx"]:
        fx = SPRITES["fx"][m["fx"]]
        put(fx[_frame(t, max(m["ms"], 900) * (1.5 if m["fx"] == "think" else 1), len(fx))])
    if pose.get("ledge", True):
        for x in range(2, W - 2):
            g[FX_H + H][x] = 4
    up = int(pose.get("hop", 0))
    if pose.get("walking"):
        legs = SPRITES["walk"][_frame(t, SPRITES["walkMs"], len(SPRITES["walk"]))]
    elif isinstance(m["legs"], list):
        legs = m["legs"][_frame(t, m["ms"], len(m["legs"]))]
    else:
        legs = m["legs"]
    put(SPRITES["legs"][legs], 0, FX_H - up, flip)
    bob = (m["bob"][f % len(m["bob"])] if m["bob"] else 0) + up
    if pose.get("walking"):
        bob += _frame(t, 240, 2)
    if pose.get("wave"):
        layers = SPRITES["wave"][_frame(t, SPRITES["waveMs"], len(SPRITES["wave"]))]
    else:
        layers = m["frames"][f]
    put(layers["body"], 0, FX_H - bob, flip)
    # face: eyes and mouth, moved a whole pixel by the gaze
    gx, gy = GAZE.get(pose.get("mood"), pose.get("gaze", (0.0, 0.0)))
    ex = max(-1, min(1, int(round(gx * 1.2))))
    ey = max(-1, min(1, int(round(gy * 1.2))))
    if flip:
        ex = -ex  # the caller mirrors the gaze for a mirrored body; the face here is never mirrored, so undo that
    eyes = pose.get("eyes", m["eyes"])
    if eyes in ("open", "wide") and pose.get("blink", 0.0) > 0.5:
        eyes = "blink"
    e = SPRITES["eyes"][eyes]
    for x, y in SPRITES["eyeAt"]:
        put(e["px"], x + e["at"][0] + ex, FX_H - bob + y + e["at"][1] + ey, False)
    mouth = pose.get("mouth", m["mouth"])
    if mouth == "talk":
        level = pose.get("talk", 0.0)
        mouth = ("o1" if level < 0.08 else "o2" if level < 0.4 else "o3") if level else SPRITES["talk"][_frame(t, 420, len(SPRITES["talk"]))]
    mm = SPRITES["mouths"][mouth]
    put(mm["px"], SPRITES["mouthAt"][0] + mm["at"][0] + ex, FX_H - bob + SPRITES["mouthAt"][1] + mm["at"][1], False)
    put(layers["front"], 0, FX_H - bob, flip)
    return g


class Painter:
    """Draws Scorpi for a pose into a square bitmap of `size` pixels."""

    def __init__(self):
        self.pal = {k: rgba(v) for k, v in PALETTE.items()}

    def set_palette(self, colors):
        for k, v in (colors or {}).items():
            key = ALIASES.get(k, k)
            if key in self.pal and isinstance(v, str) and re.match(r"^#[0-9a-fA-F]{6}$", v):
                self.pal[key] = rgba(v)

    @staticmethod
    def cell(size):
        """Screen pixels per sprite pixel: always a whole number."""
        return max(1, int(round(size / float(CANVAS))))

    def origin(self, size):
        """Top-left of the sprite space (effects + Scorpi) in the window, so the feet stand at FEET."""
        k = self.cell(size)
        return (size - W * k) // 2, int(round(size * FEET)) - (FX_H + H) * k

    def render(self, size, pose, bitmap=None):
        """pose: from pose_for(), plus flip, hop (sprite pixels up), wave, walking, ledge (default on)."""
        bmp = bitmap if bitmap is not None and bitmap.Width == size else Bitmap(size, size, PixelFormat.Format32bppPArgb)
        g = Graphics.FromImage(bmp)
        try:
            g.Clear(Color.Transparent)
            g.SmoothingMode = getattr(SmoothingMode, "None")  # .None is a Python keyword
            g.PixelOffsetMode = getattr(PixelOffsetMode, "None")
            g.InterpolationMode = InterpolationMode.NearestNeighbor
            g.CompositingMode = CompositingMode.SourceCopy
            k = self.cell(size)
            ox, oy = self.origin(size)
            brushes = {v: SolidBrush(self.pal[name]) for v, name in SLOT.items()}
            try:
                for y, row in enumerate(compose(pose)):
                    x = 0
                    while x < W:
                        v = row[x]
                        if not v:
                            x += 1
                            continue
                        s = x
                        while x < W and row[x] == v:
                            x += 1
                        py = oy + y * k
                        if 0 <= py < size:
                            g.FillRectangle(brushes[v], ox + s * k, py, (x - s) * k, k)
            finally:
                for b in brushes.values():
                    b.Dispose()
        finally:
            g.Dispose()
        return bmp


def pose_for(mood, t, gaze=(0.0, 0.0), blink=0.0, talk=0.0, walking=False):
    """The pose for a mood at time t (seconds); frames step like the web Scorpi's."""
    eyes, mouth = MOODS.get(mood, MOODS["idle"])
    return {"mood": mood if mood in MOODS else "idle", "eyes": eyes, "mouth": mouth, "t": t, "gaze": gaze, "blink": blink,
            "talk": talk, "walking": walking, "hop": 0}
