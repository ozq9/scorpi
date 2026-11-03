"""Scorpi drawn natively (GDI+ through pythonnet) for the desktop companion's see-through window.

The geometry is the same as public/js/lib/mascot.js (a 64 x 64 design grid), so the desktop Scorpi and the one in the
app look alike. Everything is anti-aliased vector drawing; a pose (mood, gaze, blink, tail and claw angles, …) is
turned into a premultiplied-alpha bitmap that UpdateLayeredWindow puts on screen with per-pixel transparency.
"""

import math
import re

import clr  # noqa: F401  (pythonnet, loaded by pywebview)

clr.AddReference("System.Drawing")
from System.Drawing import Bitmap, Color, Graphics, Pen, PointF, SolidBrush  # noqa: E402
from System.Drawing.Drawing2D import (  # noqa: E402
    ColorBlend, CompositingQuality, FillMode, GraphicsPath, LinearGradientBrush, LineCap, LineJoin, Matrix, MatrixOrder,
    PathGradientBrush, PixelOffsetMode, SmoothingMode,
)
from System.Drawing.Imaging import PixelFormat  # noqa: E402
from System import Array, Single  # noqa: E402

# ---------- an SVG path parser (M L H V C S Q Z, absolute and relative) ----------

_TOK = re.compile(r"[MmLlHhVvCcSsQqZz]|-?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")


def svg_path(d, transform=None):
    """A GraphicsPath from SVG path data. transform(x, y) -> (x, y) maps every point (used to place claws)."""
    tf = transform or (lambda x, y: (x, y))
    toks = _TOK.findall(d)
    path = GraphicsPath()
    i = 0
    cmd = "M"
    x = y = sx = sy = 0.0
    last_c = None  # last cubic control point, for S
    prev = ""

    def num():
        nonlocal i
        v = float(toks[i])
        i += 1
        return v

    def pt(px, py):
        a, b = tf(px, py)
        return PointF(a, b)

    while i < len(toks):
        t = toks[i]
        if t.isalpha():
            cmd = t
            i += 1
            if cmd in "Zz":
                path.CloseFigure()
                x, y = sx, sy
                prev = cmd
                continue
        rel = cmd.islower()
        c = cmd.upper()
        ox, oy = (x, y) if rel else (0.0, 0.0)
        if c == "M":
            x, y = num() + ox, num() + oy
            sx, sy = x, y
            path.StartFigure()
            cmd = "l" if rel else "L"
        elif c == "L":
            nx, ny = num() + ox, num() + oy
            path.AddLine(pt(x, y), pt(nx, ny))
            x, y = nx, ny
        elif c == "H":
            nx = num() + ox
            path.AddLine(pt(x, y), pt(nx, y))
            x = nx
        elif c == "V":
            ny = num() + oy
            path.AddLine(pt(x, y), pt(x, ny))
            y = ny
        elif c == "C":
            x1, y1, x2, y2, nx, ny = num() + ox, num() + oy, num() + ox, num() + oy, num() + ox, num() + oy
            path.AddBezier(pt(x, y), pt(x1, y1), pt(x2, y2), pt(nx, ny))
            last_c = (x2, y2)
            x, y = nx, ny
        elif c == "S":
            x2, y2, nx, ny = num() + ox, num() + oy, num() + ox, num() + oy
            x1, y1 = (2 * x - last_c[0], 2 * y - last_c[1]) if prev.upper() in ("C", "S") and last_c else (x, y)
            path.AddBezier(pt(x, y), pt(x1, y1), pt(x2, y2), pt(nx, ny))
            last_c = (x2, y2)
            x, y = nx, ny
        elif c == "Q":
            qx, qy, nx, ny = num() + ox, num() + oy, num() + ox, num() + oy
            path.AddBezier(pt(x, y), pt(x + 2 / 3 * (qx - x), y + 2 / 3 * (qy - y)), pt(nx + 2 / 3 * (qx - nx), ny + 2 / 3 * (qy - ny)), pt(nx, ny))
            x, y = nx, ny
        else:
            i += 1  # unsupported: skip a number
        prev = c
    return path


def ellipse_path(cx, cy, rx, ry, rot=0.0):
    p = GraphicsPath()
    p.AddEllipse(cx - rx, cy - ry, rx * 2, ry * 2)
    if rot:
        m = Matrix()
        m.RotateAt(rot, PointF(cx, cy))
        p.Transform(m)
    return p


# ---------- colours ----------

def rgba(hexstr, a=255):
    h = hexstr.lstrip("#")
    return Color.FromArgb(int(a), int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


PALETTE = {
    "hi": "#FFFFFF", "mid": "#EEF0F5", "lo": "#BFC4D0", "limb": "#C9CDD8", "eye": "#0B0C10", "eye_hi": "#3A3F52",
    "sting": "#FF8FAE", "sting_hi": "#FFD6E1", "cheek": "#FF9DB8", "mouth": "#1A1C24",
}


def radial(path, center, hi, mid, lo, mid_at=0.45):
    """A radial gradient over a shape: hi at `center`, mid, then lo at the edge."""
    b = PathGradientBrush(path)
    b.CenterPoint = PointF(center[0], center[1])
    blend = ColorBlend(3)
    blend.Colors = Array[Color]([lo, mid, hi])
    blend.Positions = Array[Single]([0.0, mid_at, 1.0])
    b.InterpolationColors = blend
    return b


# ---------- the cloud Scorpi floats on (same puffs as mascot.js) ----------

CLOUD_BACK = ((17, 56, 6.2), (29, 52.5, 7.6), (41, 53.5, 7.2), (51, 57, 5.6))
CLOUD_FRONT = ((10.5, 61.5, 5.2), (20, 60.5, 7), (32, 61.5, 7.8), (44, 60.8, 7), (54.5, 61.8, 5))
CLOUD_BASE = (32, 64, 23, 4.6)


def cloud_path(puffs, base=None):
    p = GraphicsPath(FillMode.Winding)
    for cx, cy, r in puffs:
        p.AddEllipse(cx - r, cy - r, r * 2, r * 2)
    if base:
        cx, cy, rx, ry = base
        p.AddEllipse(cx - rx, cy - ry, rx * 2, ry * 2)
    return p


# ---------- claws (same construction as mascot.js) ----------

PALM = "M-5.8 .6C-5.8 -3.4 -2.6 -5.2 .6 -4.4L1.8 -1.5C3.4 -1.7 5.1 -2.6 6.8 -3.7C7.6 -1.2 6.8 2.6 3.8 4.3C.6 6 -5.8 5.1 -5.8 .6Z"
FINGER = "M-.6 -2.7C0 -6 3.6 -8 7 -6.9C6.2 -5.3 4.4 -4 2.1 -3.3Z"
HINGE = (0.2, -3.2)


def claw_map(side):
    k = 1.18
    a = math.radians(-62)
    cx, cy = 10.8, 34.2

    def fn(x, y):
        px = cx + (x * math.cos(a) - y * math.sin(a)) * k
        py = cy + (x * math.sin(a) + y * math.cos(a)) * k
        return (px if side == "l" else 64 - px), py
    return fn


class Painter:
    """Draws Scorpi for a pose into a square bitmap of `size` pixels (the 64-unit design grid scaled to fit)."""

    def __init__(self):
        self.pal = {k: rgba(v) for k, v in PALETTE.items()}
        self.edge = Color.FromArgb(70, 40, 44, 60)
        # paths that never change are parsed once
        self.body = svg_path("M32 31.2c9.4 0 16.4 6.1 16.4 13.6 0 7-7.1 11.6-16.4 11.6S15.6 51.8 15.6 44.8C15.6 37.3 22.6 31.2 32 31.2z")
        self.seg1 = svg_path("M19.6 50.2c3.4 2.4 7.6 3.5 12.4 3.5s9-1.1 12.4-3.5")
        self.seg2 = svg_path("M22.4 53.4c2.8 1.3 6 1.9 9.6 1.9s6.8-.6 9.6-1.9")
        self.tail = [ellipse_path(*e) for e in ((43.8, 37, 5.6, 5.2, -55), (48.6, 29.8, 5, 4.6, -75), (49.6, 22.3, 4.5, 4.2, -100),
                                                (46.6, 15.8, 4.1, 3.8, -135), (41.6, 12.6, 3.7, 3.4, -165))]
        self.barb = svg_path("M34.4 14.4C32.8 15.2 31.7 16.8 31.5 19C33.2 17.8 34.8 16.9 36.2 16Z")
        self.sting = ellipse_path(36.6, 13.4, 3.3, 2.8, -24)
        self.claws = {}
        for side in ("l", "r"):
            fn = claw_map(side)
            m = (lambda v: v) if side == "l" else (lambda v: 64 - v)
            self.claws[side] = {
                "limb": svg_path("M%s 44Q%s 44.6 %s 39.4" % (m(20.8), m(13.6), m(10.4))),
                "palm": svg_path(PALM, fn), "finger": svg_path(FINGER, fn), "hinge": fn(*HINGE),
                "shine": fn(-3.4, -2.6), "shoulder": (m(20.5), 43.0),
            }
        self.legs = [((20.8, 51.2), (17.9, 53.8)), ((24.2, 53.8), (22.3, 56.8)), ((28.2, 55.2), (27.3, 58.0)),
                     ((43.2, 51.2), (46.1, 53.8)), ((39.8, 53.8), (41.7, 56.8)), ((35.8, 55.2), (36.7, 58.0))]
        self.lids_happy = svg_path("M23.4 43c1-2.2 5-2.2 6 0M34.6 43c1-2.2 5-2.2 6 0")
        self.lids_shut = svg_path("M23.6 42.4c1.2 1.6 4.4 1.6 5.6 0M34.8 42.4c1.2 1.6 4.4 1.6 5.6 0")
        self.mouths = {
            "smile": svg_path("M30.2 47.2c.9 1.1 2.7 1.1 3.6 0"), "flat": svg_path("M30.4 47.8h3.2"),
            "sad": svg_path("M30.2 48.6c.9-1.1 2.7-1.1 3.6 0"),
        }
        self.cloud_back = cloud_path(CLOUD_BACK)
        self.cloud_front = cloud_path(CLOUD_FRONT, CLOUD_BASE)
        self.grin = svg_path("M29.4 46.7h5.2c0 1.9-1.2 3-2.6 3s-2.6-1.1-2.6-3z")
        self.tongue = svg_path("M30.6 48.9c.7-.6 2.1-.6 2.8 0-.4.5-.9.8-1.4.8s-1-.3-1.4-.8z")

    def set_palette(self, colors):
        for k, v in (colors or {}).items():
            if k in self.pal and isinstance(v, str) and re.match(r"^#[0-9a-fA-F]{6}$", v):
                self.pal[k] = rgba(v)

    # ---------- drawing ----------

    def render(self, size, pose, bitmap=None):
        """pose: dict with mood, eyes, mouth, gaze (gx, gy), blink 0..1, talk 0..1, tail, arm_l, arm_r, finger_l, finger_r
        (degrees), breathe, hop (units up), squash, flip, legs (offset), cheek 0..1, fx (set of names), t (seconds)."""
        bmp = bitmap if bitmap is not None and bitmap.Width == size else Bitmap(size, size, PixelFormat.Format32bppPArgb)
        g = Graphics.FromImage(bmp)
        try:
            g.Clear(Color.Transparent)
            g.SmoothingMode = SmoothingMode.AntiAlias
            g.PixelOffsetMode = PixelOffsetMode.HighQuality
            g.CompositingQuality = CompositingQuality.HighQuality
            s = size / 80.0  # the 64-unit figure sits in an 80-unit canvas (room for hops and effects)
            cloud = pose.get("cloud")
            base = Matrix()
            if cloud:
                base.Translate(size / 2.0, float(size))
                base.Scale(s, s)
                base.Translate(-32, -72)  # the cloud's underside at the bottom centre
            else:
                base.Translate(size / 2.0, size - 6 * s)
                base.Scale(s, s)
                base.Translate(-32, -60)  # feet at the bottom centre
            g.Transform = base
            if cloud:
                self._cloud(g, pose, front=False)
            else:
                self._shadow(g, pose)
            hop = pose.get("hop", 0.0)
            squash = pose.get("squash", 0.0)
            body = base.Clone()
            body.Translate(0, -hop)
            body.Translate(32, 58)
            body.Scale(1 + squash * 0.08 + pose.get("breathe", 0.0) * 0.4, 1 - squash * 0.1 + pose.get("breathe", 0.0))
            if pose.get("lean"):
                body.Rotate(pose["lean"])
            body.Translate(-32, -58)
            if pose.get("flip"):
                body.Translate(64, 0)
                body.Scale(-1, 1)
            g.Transform = body
            self._tail(g, pose)
            self._legs(g, pose)
            self._claw(g, "l", pose.get("arm_l", 0.0), pose.get("finger_l", 0.0))
            self._claw(g, "r", pose.get("arm_r", 0.0), pose.get("finger_r", 0.0))
            self._body(g)
            self._face(g, pose)
            g.Transform = base
            if cloud:
                self._cloud(g, pose, front=True)
            self._fx(g, pose)
        finally:
            g.Dispose()
        return bmp

    def _cloud(self, g, pose, front):
        """Soft white puffs: the back row behind Scorpi, the front row over its feet, a little wobble of their own."""
        t = pose.get("t", 0.0)
        st = g.Save()
        m = g.Transform
        m.Translate(math.sin(t * 0.9) * 0.4, math.sin(t * 1.3 + (0.6 if front else 0)) * 0.35)
        g.Transform = m
        path = self.cloud_front if front else self.cloud_back
        r = path.GetBounds()
        top, bottom = (Color.FromArgb(250, 255, 255, 255), Color.FromArgb(248, 0xDD, 0xE2, 0xEE)) if front else                       (Color.FromArgb(240, 0xEE, 0xF0, 0xF6), Color.FromArgb(236, 0xBF, 0xC6, 0xD6))
        b = LinearGradientBrush(PointF(0, r.Y - 0.5), PointF(0, r.Y + r.Height + 0.5), top, bottom)
        g.FillPath(b, path)
        b.Dispose()
        if front:
            shine = SolidBrush(Color.FromArgb(190, 255, 255, 255))
            for cx, cy, rr in CLOUD_FRONT[1:4]:
                g.FillPath(shine, ellipse_path(cx - rr * 0.25, cy - rr * 0.55, rr * 0.38, rr * 0.16, -12))
            shine.Dispose()
        g.Restore(st)

    def _shadow(self, g, pose):
        k = 1 - min(0.5, pose.get("hop", 0.0) / 18.0)
        b = SolidBrush(Color.FromArgb(int(26 * k), 0, 0, 0))
        g.FillEllipse(b, 32 - 15 * k, 58.6 - 2.2, 30 * k, 4.4)
        b.Dispose()

    def _rot(self, g, deg, px, py):
        if not deg:
            return None
        state = g.Save()
        m = g.Transform
        m.RotateAt(deg, PointF(px, py), MatrixOrder.Prepend)
        g.Transform = m
        return state

    def _fill(self, g, path, brush, edge=True):
        g.FillPath(brush, path)
        if edge:
            pen = Pen(self.edge, 0.6)
            g.DrawPath(pen, path)
            pen.Dispose()

    def _tail(self, g, pose):
        p = self.pal
        st = self._rot(g, pose.get("tail", 0.0), 43, 39)
        for path in self.tail:
            r = path.GetBounds()
            b = radial(path, (r.X + r.Width * 0.38, r.Y + r.Height * 0.3), p["hi"], p["mid"], p["lo"], 0.5)
            self._fill(g, path, b)
            b.Dispose()
        barb = SolidBrush(p["sting"])
        g.FillPath(barb, self.barb)
        barb.Dispose()
        r = self.sting.GetBounds()
        b = radial(self.sting, (r.X + r.Width * 0.35, r.Y + r.Height * 0.3), p["sting_hi"], p["sting"], p["sting"], 0.5)
        g.FillPath(b, self.sting)
        b.Dispose()
        shine = SolidBrush(Color.FromArgb(215, 255, 255, 255))
        g.FillPath(shine, ellipse_path(35.6, 12.2, 1.1, 0.6, -24))
        shine.Dispose()
        if st is not None:
            g.Restore(st)

    def _legs(self, g, pose):
        off = pose.get("legs", 0.0)
        pen = Pen(self.pal["limb"], 2.6)
        pen.StartCap = pen.EndCap = LineCap.Round
        for i, ((x1, y1), (x2, y2)) in enumerate(self.legs):
            d = off if i % 2 == 0 else -off
            g.DrawLine(pen, x1, y1, x2 + d, y2)
        pen.Dispose()

    def _claw(self, g, side, arm, finger):
        p = self.pal
        c = self.claws[side]
        st = self._rot(g, arm, *c["shoulder"])
        limb = Pen(p["limb"], 3.4)
        limb.StartCap = limb.EndCap = LineCap.Round
        g.DrawPath(limb, c["limb"])
        limb.Dispose()
        for key in ("palm", "finger"):
            path = c[key]
            st2 = self._rot(g, finger if key == "finger" else 0.0, *c["hinge"])
            r = path.GetBounds()
            b = radial(path, (r.X + r.Width * 0.3, r.Y + r.Height * 0.3), p["hi"], p["mid"], p["lo"], 0.4)
            self._fill(g, path, b)
            b.Dispose()
            if st2 is not None:
                g.Restore(st2)
        shine = SolidBrush(Color.FromArgb(215, 255, 255, 255))
        g.FillPath(shine, ellipse_path(c["shine"][0], c["shine"][1], 1.5, 0.9, -40 if side == "l" else 40))
        shine.Dispose()
        if st is not None:
            g.Restore(st)

    def _body(self, g):
        p = self.pal
        r = self.body.GetBounds()
        b = radial(self.body, (r.X + r.Width * 0.38, r.Y + r.Height * 0.28), p["hi"], p["mid"], p["lo"], 0.42)
        self._fill(g, self.body, b)
        b.Dispose()
        pen = Pen(Color.FromArgb(36, 40, 44, 60), 0.8)
        pen.StartCap = pen.EndCap = LineCap.Round
        g.DrawPath(pen, self.seg1)
        g.DrawPath(pen, self.seg2)
        pen.Dispose()
        shine = SolidBrush(Color.FromArgb(215, 255, 255, 255))
        g.FillPath(shine, ellipse_path(25.2, 35.6, 4.2, 1.9, -18))
        shine.Dispose()

    def _face(self, g, pose):
        p = self.pal
        gx, gy = pose.get("gaze", (0.0, 0.0))
        st = g.Save()
        m = g.Transform
        m.Translate(gx * 0.9, gy * 0.7)
        g.Transform = m
        cheek = SolidBrush(Color.FromArgb(int(255 * 0.5 * (1 + pose.get("cheek", 0.0) * 0.8)), p["cheek"].R, p["cheek"].G, p["cheek"].B))
        g.FillEllipse(cheek, 22.6 - 2.7, 47.2 - 1.5, 5.4, 3.0)
        g.FillEllipse(cheek, 41.4 - 2.7, 47.2 - 1.5, 5.4, 3.0)
        cheek.Dispose()
        eyes = pose.get("eyes", "open")
        ink = Pen(p["mouth"], 1.5)
        ink.StartCap = ink.EndCap = LineCap.Round
        ink.LineJoin = LineJoin.Round
        if eyes == "open":
            st2 = g.Save()
            m2 = g.Transform
            m2.Translate(gx * 1.3 - gx * 0.9, gy * 1.1 - gy * 0.7)
            g.Transform = m2
            k = max(0.08, 1 - pose.get("blink", 0.0)) * pose.get("eye_scale", 1.0)
            for cx in (26.4, 37.6):
                rx, ry = 3.05 * pose.get("eye_scale", 1.0), 3.7 * k
                path = ellipse_path(cx, 42.2, rx, max(0.3, ry))
                b = radial(path, (cx - 0.6, 42.2 - ry * 0.4), p["eye_hi"], p["eye"], p["eye"], 0.6)
                g.FillPath(b, path)
                b.Dispose()
                if k > 0.4:
                    w = SolidBrush(Color.White)
                    g.FillEllipse(w, cx + 1.1 - 1.25, 42.2 - 1.6 * k - 1.25, 2.5, 2.5)
                    w.Dispose()
                    w = SolidBrush(Color.FromArgb(205, 255, 255, 255))
                    g.FillEllipse(w, cx - 0.9 - 0.55, 42.2 + 1.7 * k - 0.55, 1.1, 1.1)
                    w.Dispose()
            g.Restore(st2)
        else:
            g.DrawPath(ink, self.lids_happy if eyes == "happy" else self.lids_shut)
        mouth = pose.get("mouth", "smile")
        if mouth in self.mouths:
            pen = Pen(p["mouth"], 1.2)
            pen.StartCap = pen.EndCap = LineCap.Round
            g.DrawPath(pen, self.mouths[mouth])
            pen.Dispose()
        elif mouth == "grin":
            b = SolidBrush(p["mouth"])
            g.FillPath(b, self.grin)
            b.Dispose()
            b = SolidBrush(p["cheek"])
            g.FillPath(b, self.tongue)
            b.Dispose()
        else:  # "o", opened by the voice while talking
            t = pose.get("talk", 0.3)
            rx, ry = 1.25 * (0.9 + t * 0.5), 1.4 * (0.55 + t * 1.3)
            b = SolidBrush(p["mouth"])
            g.FillEllipse(b, 32 - rx, 47.9 - ry, rx * 2, ry * 2)
            b.Dispose()
        ink.Dispose()
        g.Restore(st)

    def _fx(self, g, pose):
        fx = pose.get("fx") or ()
        t = pose.get("t", 0.0)
        white = lambda a: SolidBrush(Color.FromArgb(int(max(0, min(255, a))), 255, 255, 255))  # noqa: E731
        if "think" in fx:
            for i, (x, y, r) in enumerate(((52.5, 17, 1.4), (56.2, 11.6, 2.0), (60, 5, 2.8))):
                k = 0.6 + 0.4 * (0.5 + 0.5 * math.sin(t * 3.4 - i * 0.8))
                b = white(210 * k)
                g.FillEllipse(b, x - r * k, y - r * k, r * 2 * k, r * 2 * k)
                b.Dispose()
        if "zzz" in fx:
            pen = Pen(Color.FromArgb(180, 255, 255, 255), 1.3)
            pen.StartCap = pen.EndCap = LineCap.Round
            for i, (x, y, w) in enumerate(((50, 18, 4), (56, 9.5, 3))):
                ph = (t / 3.2 + i * 0.5) % 1.0
                dy = -6 * ph
                a = 255 * min(1, ph * 3) * (1 - ph)
                pen.Color = Color.FromArgb(int(a), 255, 255, 255)
                path = svg_path("M%.2f %.2fh%.2fl%.2f %.2fh%.2f" % (x + 3 * ph, y + dy, w, -w, w, w))
                g.DrawPath(pen, path)
            pen.Dispose()
        if "sparks" in fx:
            for i, (x, y, r) in enumerate(((8, 18.5, 2.3), (57, 28.8, 2.0), (45, 3.4, 1.8))):
                k = 0.5 + 0.5 * (0.5 + 0.5 * math.sin(t * 5 + i * 2.1))
                rr = r * k
                path = svg_path("M%.2f %.2fl%.2f %.2fl%.2f %.2fl%.2f %.2fl%.2f %.2fl%.2f %.2fl%.2f %.2fl%.2f %.2fz" % (
                    x, y - rr * 2.2, rr * .4, rr * 1.8, rr * 1.8, rr * .4, -rr * 1.8, rr * .4, -rr * .4, rr * 1.8, -rr * .4, -rr * 1.8, -rr * 1.8, -rr * .4, rr * 1.8, -rr * .4))
                b = white(230 * k)
                g.FillPath(b, path)
                b.Dispose()
        if "waves" in fx:
            pen = Pen(Color.FromArgb(190, 255, 255, 255), 1.3)
            pen.StartCap = pen.EndCap = LineCap.Round
            for i, d in enumerate(("M58 37c1.6 2 1.6 5 0 7", "M61 34c2.8 3.6 2.8 9.4 0 13", "M6 37c-1.6 2-1.6 5 0 7", "M3 34c-2.8 3.6-2.8 9.4 0 13")):
                ph = (t / 1.6 + (0.3 if i % 2 else 0)) % 1.0
                pen.Color = Color.FromArgb(int(220 * min(1, ph * 2.5) * (1 - ph)), 255, 255, 255)
                g.DrawPath(pen, svg_path(d))
            pen.Dispose()
        if "bang" in fx:
            b = SolidBrush(rgba("#FFC56E"))
            path = GraphicsPath()
            path.AddEllipse(51.4, 6, 3.2, 8)
            g.FillPath(b, path)
            g.FillEllipse(b, 53 - 1.7, 17.4 - 1.7, 3.4, 3.4)
            b.Dispose()
        if "drop" in fx:
            ph = (t / 2.6) % 1.0
            b = SolidBrush(Color.FromArgb(int(230 * min(1, ph * 4) * (1 - ph)), 0x9C, 0xCB, 0xFF))
            g.FillPath(b, svg_path("M51.6 %.2fc1.2 1.8 2.2 3 2.2 4.2c0 1.3-1 2.2-2.2 2.2s-2.2-.9-2.2-2.2c0-1.2 1-2.4 2.2-4.2z" % (23.4 + ph * 5)))
            b.Dispose()
        if "hearts" in fx:
            for i in range(3):
                ph = (t / 1.2 + i / 3) % 1.0
                x = 32 + (i - 1) * 9 + math.sin(t * 2 + i) * 2
                y = 26 - ph * 22
                b = SolidBrush(Color.FromArgb(int(255 * min(1, ph * 4) * (1 - ph)), self.pal["cheek"].R, self.pal["cheek"].G, self.pal["cheek"].B))
                path = svg_path("M%.2f %.2fc-1.2-1.6-3.6-.8-3.6 1 0 1.6 2 2.8 3.6 4 1.6-1.2 3.6-2.4 3.6-4 0-1.8-2.4-2.6-3.6-1z" % (x, y))
                g.FillPath(b, path)
                b.Dispose()


# ---------- moods -> poses ----------

MOODS = {
    "idle": ("open", "smile"), "curious": ("open", "o"), "thinking": ("open", "flat"), "writing": ("open", "smile"),
    "working": ("open", "flat"), "happy": ("happy", "grin"), "sad": ("open", "sad"), "sleeping": ("shut", "o"),
    "listening": ("open", "o"), "talking": ("open", "o"), "alert": ("open", "o"), "shy": ("happy", "smile"),
    "lifted": ("open", "o"),
}
GAZE = {"thinking": (0.55, -0.85), "writing": (0.5, 0.75), "working": (0.0, 0.4), "sad": (0.0, 0.9), "sleeping": (0.0, 0.2)}


def pose_for(mood, t, gaze=(0.0, 0.0), blink=0.0, talk=0.0, walking=False):
    """The animated pose for a mood at time t (seconds)."""
    eyes, mouth = MOODS.get(mood, MOODS["idle"])
    s = math.sin
    pose = {
        "mood": mood, "eyes": eyes, "mouth": mouth, "t": t, "blink": blink, "talk": talk, "fx": set(),
        "gaze": GAZE.get(mood, gaze), "breathe": 0.009 * s(t * 1.75), "tail": -2.5 + 2.5 * s(t * 1.5),
        "arm_l": 0.0, "arm_r": 0.0, "finger_l": 0.0, "finger_r": 0.0, "legs": 0.0, "cheek": 0.0, "hop": 0.0, "eye_scale": 1.0,
    }
    if mood == "thinking":
        pose.update(tail=-7.5 + 4.5 * s(t * 2.4), arm_r=-26, lean=2.5 * s(t * 1.9))
        pose["fx"].add("think")
    elif mood == "working":
        pose.update(tail=4 * s(t * 11.4), finger_l=-14 + 14 * s(t * 12.5), finger_r=14 - 14 * s(t * 12.5 + 1.6), legs=0.8 * (1 if int(t * 7) % 2 else -1),
                    breathe=0.012 * s(t * 9), blink=max(blink, 0.22))
        pose["fx"].add("sparks")
    elif mood == "writing":
        pose.update(finger_r=14 - 14 * s(t * 10.4), breathe=0.012 * s(t * 5.7))
    elif mood == "happy":
        pose.update(arm_l=-15 - 9 * s(t * 12.5), arm_r=15 + 9 * s(t * 12.5), tail=4 * s(t * 15.7), cheek=1.0, hop=max(0.0, 2.5 * s(t * 7.8)))
        pose["fx"].add("hearts")
    elif mood == "shy":
        pose.update(arm_l=18, arm_r=-18, cheek=1.0)
    elif mood == "sad":
        pose.update(arm_l=-14, arm_r=14, tail=18, breathe=-0.02)
        pose["fx"].add("drop")
    elif mood == "sleeping":
        pose.update(tail=28, breathe=0.016 * s(t * 1.25), talk=0.15 + 0.15 * s(t * 1.25))
        pose["fx"].add("zzz")
    elif mood in ("listening", "curious"):
        pose.update(lean=2.5 * s(t * 2.4), eye_scale=1.08, tail=-2 + 4 * s(t * 3.9))
        if mood == "listening":
            pose["fx"].add("waves")
    elif mood == "talking":
        pose.update(breathe=0.01 * s(t * 4.5))
    elif mood == "alert":
        pose.update(eye_scale=1.14, tail=-10, finger_l=-14 + 14 * s(t * 9), finger_r=14 - 14 * s(t * 9 + 1.5))
        pose["fx"].add("bang")
    elif mood == "lifted":
        pose.update(legs=1.2 * s(t * 30), arm_l=-20, arm_r=20, tail=6 * s(t * 8))
    if walking:
        pose["legs"] = 1.0 if int(t * 12) % 2 else -1.0
        pose["hop"] = abs(s(t * 19)) * 0.9
    return pose
