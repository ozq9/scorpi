"""Scorpi, the pixel scorpion: the one place its pixels are drawn.

    python scripts/make_scorpi.py            writes scorpi/sprites.py and public/js/lib/scorpi-sprites.js
    python scripts/make_scorpi.py --preview  also writes a contact sheet of every mood to the path given after it

Line art in white on black, seen from the front and a little from the side: a round outlined head with happy eyes, a big
tail of crescent segments curling over its back to the stinger, two C-shaped pincers in front and three legs. The web
mascot (public/js/lib/mascot.js), the desktop one (scorpi_paint.py) and the icons (scripts/make_icons.py) all read what
this writes, so they can't drift apart. Edit the art here and run the script again; never edit the output.

Scorpi lives on a 32 x 24 grid. Layers, back to front: legs, body (tail, pincers, the head's outline; inside the head
stays empty), face (eyes and mouth, moved by the gaze), front (unused for now). Effects (zzz, !, ...) are drawn in the
empty corner above its head. Palette: 1 body, 2 eyes and mouth, 3 shadow (the stinger's tip, sweat).
"""

import json
import os
import sys

W, H = 32, 24
FX_H = 0  # effects share the grid (top left is empty)
PALETTE = {".": 0, "#": 1, "o": 2, "+": 3}
COLORS = {1: "#EDEDED", 2: "#EDEDED", 3: "#8F8F8F"}  # body, eyes and mouth, shadow (the --sc-* tokens default to these)

# ---------- the head: an outline; "-" is its inside, kept empty ----------

HEAD_AT = (4, 9)
HEAD = [
    "....######....",
    "..##------##..",
    ".#----------#.",
    "#------------#",
    "#------------#",
    "#------------#",
    "#------------#",
    ".#----------#.",
    "..##------##..",
    "....######....",
]

# eyes and mouth sit at these anchors (top-left of the glyph) plus the glyph's own nudge
EYE_AT = ((6, 13), (12, 13))
MOUTH_AT = (10, 15)

EYES = {
    "open": ((1, 0), ["oo", "oo"]),
    "blink": ((1, 1), ["oo"]),
    "wide": ((0, 0), ["ooo", "ooo"]),
    "happy": ((0, 0), [".oo.", "o..o"]),
    "shut": ((0, 1), ["oooo"]),
}
MOUTHS = {
    "none": ((0, 0), [""]),
    "smile": ((-1, 0), ["o..o", ".oo."]),
    "flat": ((0, 1), ["oo"]),
    "sad": ((-1, 1), [".oo.", "o..o"]),
    "o": ((0, 0), ["oo", "oo"]),
    "o1": ((0, 1), ["oo"]),
    "o2": ((0, 0), ["oo", "oo"]),
    "o3": ((0, 0), ["oo", "oo", "oo"]),
    "grin": ((-1, 0), ["oooo", ".oo."]),
}

# ---------- the tail: crescent segments from its back, up and over, to the stinger ----------
# each piece: (x, y, art); a pose moves pieces by (dx, dy)

TAIL = {
    "s1": (19, 9, ["..###", "#####", "####."]),
    "s2": (25, 7, ["..##", "####", "###."]),
    "s3": (27, 2, ["##..", "###.", "####", ".###"]),
    "s4": (22, 0, ["#####", "####."]),
    "s5": (17, 0, [".###", "####", "###."]),
    "sting": (15, 4, [".###", "##..", "+..."]),
}
TAILS = {
    "up": {},
    # the top of the tail and the stinger a pixel further back
    "sway": {"s3": (1, 0), "s4": (1, 0), "s5": (1, 0), "sting": (1, 0)},
    # curled in (thinking, shy)
    "curl": {"s5": (1, 1), "sting": (2, 1)},
    # the stinger raised, ready (alert)
    "high": {"s5": (1, 0), "sting": (2, -1)},
    # lowered (sad, asleep)
    "droop": {"s3": (0, 1), "s4": (0, 2), "s5": (1, 2), "sting": (1, 3)},
}

# ---------- pincers: C shapes opening up and to the right, a finger inside that snips ----------
# (x, y, art) per pose; "left" sits by its cheek, "mid" under its chin

CLAWS = {
    "left": {
        "open": (0, 17, ["..##..", ".##...", "##..#.", "##.##.", "##....", ".##...", "..####"]),
        "shut": (0, 17, ["..##..", ".##.#.", "##.##.", "##....", "##....", ".##...", "..####"]),
        "up": (0, 16, ["..##..", ".##...", "##..#.", "##.##.", "##....", ".##...", "..####"]),
        "up-shut": (0, 16, ["..##..", ".##.#.", "##.##.", "##....", "##....", ".##...", "..####"]),
        "down": (0, 18, [".##...", "##....", "##....", "##.##.", ".##.#.", "..####"]),
    },
    "mid": {
        "open": (13, 19, ["..###.", ".##...", "##..#.", "##.##.", ".####."]),
        "shut": (13, 19, ["..###.", ".##.#.", "##.##.", "##....", ".####."]),
        "up": (13, 18, ["..###.", ".##...", "##..#.", "##.##.", ".####."]),
        "up-shut": (13, 18, ["..###.", ".##.#.", "##.##.", "##....", ".####."]),
        "down": (13, 20, [".##...", "##..#.", "##.##.", ".####."]),
    },
}

# ---------- legs: three slanted strokes behind its right side ----------

LEGS = {
    "stand": [
        (23, 13, ["##...", ".##..", "..##.", "...##"]),
        (20, 15, ["##....", ".##...", "..##..", "...##.", "....##"]),
        (19, 18, ["##...", ".##..", "..##.", "...##"]),
    ],
    "walk-a": [
        (23, 13, ["##...", ".##..", "..##.", "..##."]),
        (20, 15, ["##....", ".##...", "..##..", "...##.", "....##"]),
        (19, 18, ["##...", ".##..", ".##..", "..##."]),
    ],
    "walk-b": [
        (23, 13, ["##...", ".##..", "..##.", "...##"]),
        (20, 15, ["##....", ".##...", "..##..", "..##..", "..##.."]),
        (19, 18, ["##...", ".##..", "..##.", "...##"]),
    ],
    "tuck": [
        (23, 14, ["##..", ".##.", "..##"]),
        (20, 16, ["##...", ".##..", "..##.", "...##"]),
        (19, 19, ["##..", ".##.", "..##"]),
    ],
    "dangle-a": [
        (23, 13, ["##..", ".##.", ".##.", ".##."]),
        (20, 15, ["##...", ".##..", ".##..", "..##.", "..##."]),
        (19, 18, ["##..", ".##.", ".##.", ".##."]),
    ],
    "dangle-b": [
        (23, 13, ["##...", ".##..", "..##.", "..##."]),
        (20, 15, ["##....", "##....", ".##...", ".##...", "..##.."]),
        (19, 18, ["##...", "##...", ".##..", ".##.."]),
    ],
}

# ---------- effects: frames of pieces in the empty corner above its head ----------

FX = {
    # the little excitement marks
    "marks": [
        [(1, 4, ["#..#", ".#..#", "..#..#"]), (9, 6, ["#", "#"]), (13, 5, ["#", "#"])],
        [(1, 3, ["#..#", ".#..#", "..#..#"]), (9, 5, ["#", "#"]), (13, 4, ["#", "#"])],
    ],
    "zzz": [
        [(9, 4, ["###", ".#.", "###"])],
        [(7, 5, ["##", "##"]), (11, 1, ["###", ".#.", "###"])],
    ],
    "think": [
        [(11, 6, ["#"])],
        [(11, 6, ["#"]), (9, 3, ["##", "##"])],
        [(11, 6, ["#"]), (9, 3, ["##", "##"]), (5, 0, ["###", "###"])],
    ],
    "sparks": [
        [(1, 2, [".#.", "###", ".#."]), (12, 4, [".#.", "###", ".#."])],
        [(2, 6, [".#.", "#.#", ".#."]), (10, 1, [".#.", "#.#", ".#."])],
    ],
    "bang": [
        [(10, 1, ["##", "##", "##", "##", "..", "##"])],
        [(10, 0, ["##", "##", "##", "##", "..", "##"])],
    ],
    "huh": [
        [(8, 0, [".###.", "#...#", "...#.", "..#..", ".....", "..#.."])],
    ],
    "drop": [
        [(2, 9, [".+.", "+++", "+++"])],
        [(2, 11, [".+.", "+++", "+++"])],
    ],
    "waves": [
        [(0, 11, [".#", "#.", "#.", ".#"])],
        [(0, 10, ["..#", ".#.", "#..", "#..", ".#.", "..#"])],
    ],
}

# ---------- moods ----------
# claws: one (left, mid) pair of pincer poses per frame; tail: a tail pose, or one per frame; ms: one cycle of the
# frames; bob: the body's offset per frame (pixels up); legs: a set of legs, or a list of frames; fx: an effect.

MOODS = {
    "idle": dict(eyes="happy", mouth="none", claws=[("open", "open"), ("open", "open")], tail=["up", "sway"], ms=2400, bob=[0, 0], legs="stand"),
    "curious": dict(eyes="wide", mouth="o1", claws=[("up", "open"), ("up-shut", "open")], tail="sway", ms=1600, legs="stand", fx="huh"),
    "thinking": dict(eyes="open", mouth="flat", claws=[("open", "up"), ("open", "up-shut")], tail="curl", ms=2000, legs="stand", fx="think"),
    "writing": dict(eyes="open", mouth="none", claws=[("open", "shut"), ("shut", "open")], tail="up", ms=700, legs="stand"),
    "working": dict(eyes="open", mouth="flat", claws=[("open", "shut"), ("shut", "open")], tail=["sway", "up"], ms=360, bob=[0, 1], legs=["walk-a", "walk-b"], fx="sparks"),
    "happy": dict(eyes="happy", mouth="none", claws=[("up", "up"), ("up-shut", "up-shut")], tail=["sway", "up"], ms=560, bob=[1, 0], legs="stand", fx="marks"),
    "sad": dict(eyes="open", mouth="sad", claws=[("down", "down")], tail="droop", ms=2600, bob=[0], legs="tuck", fx="drop"),
    "sleeping": dict(eyes="shut", mouth="none", claws=[("down", "down")], tail="droop", ms=3000, bob=[0, 0], legs="tuck", fx="zzz"),
    "listening": dict(eyes="wide", mouth="o1", claws=[("up", "up")], tail="up", ms=1200, legs="stand", fx="waves"),
    "talking": dict(eyes="happy", mouth="talk", claws=[("open", "open")], tail="up", ms=420, legs="stand"),
    "alert": dict(eyes="wide", mouth="o", claws=[("up", "up"), ("up-shut", "up-shut")], tail="high", ms=500, legs="stand", fx="bang"),
    "shy": dict(eyes="happy", mouth="none", claws=[("shut", "shut"), ("down", "down")], tail="curl", ms=1400, legs="tuck"),
    "lifted": dict(eyes="wide", mouth="o", claws=[("up", "down"), ("down", "up")], tail=["sway", "up"], ms=300, legs=["dangle-a", "dangle-b"]),
}
# the left pincer waves for a moment (createMascot().wave(), the desktop on hover)
WAVE = [("up", "open"), ("open", "open")]
WAVE_TAIL = "up"
WAVE_MS = 360
WALK = ["walk-a", "walk-b"]
WALK_MS = 240
TALK = ["o1", "o2", "o3", "o2"]  # mouth frames while talking (the voice level picks one when it is known)


# =====================================================================================================================

def grid(w, h):
    return [[0] * w for _ in range(h)]


def stamp(g, art, x, y):
    for r, row in enumerate(art):
        for c, ch in enumerate(row):
            v = PALETTE.get(ch, 0)
            gx, gy = x + c, y + r
            if v and 0 <= gx < len(g[0]) and 0 <= gy < len(g):
                g[gy][gx] = v


def runs(g):
    """Horizontal runs of one colour: [x, y, width, palette]."""
    out = []
    for y, row in enumerate(g):
        x = 0
        while x < len(row):
            v = row[x]
            if not v:
                x += 1
                continue
            s = x
            while x < len(row) and row[x] == v:
                x += 1
            out.append([s, y, x - s, v])
    return out


def layers(left, mid, tail):
    """The body (tail, pincers, the head's outline with its inside cleared) for two pincer poses and a tail pose."""
    body = grid(W, H)
    moves = TAILS[tail]
    for name, (x, y, art) in TAIL.items():
        dx, dy = moves.get(name, (0, 0))
        stamp(body, art, x + dx, y + dy)
    for side, pose in (("left", left), ("mid", mid)):
        x, y, art = CLAWS[side][pose]
        stamp(body, art, x, y)
    hx, hy = HEAD_AT
    for r, row in enumerate(HEAD):
        for c, ch in enumerate(row):
            if ch == "-":
                body[hy + r][hx + c] = 0
            elif ch == "#":
                body[hy + r][hx + c] = 1
    return body, grid(W, H)


def glyph(art):
    return runs([[PALETTE.get(ch, 0) for ch in row] for row in art])


def pieces(parts):
    g = grid(W, H)
    for x, y, art in parts:
        stamp(g, art, x, y)
    return runs(g)


def build():
    moods = {}
    for name, spec in MOODS.items():
        frames = []
        tails = spec["tail"] if isinstance(spec["tail"], list) else [spec["tail"]]
        n = max(len(spec["claws"]), len(tails))
        for i in range(n):
            left, mid = spec["claws"][i % len(spec["claws"])]
            body, front = layers(left, mid, tails[i % len(tails)])
            frames.append({"body": runs(body), "front": runs(front)})
        moods[name] = {
            "eyes": spec["eyes"], "mouth": spec["mouth"], "ms": spec["ms"], "frames": frames,
            "bob": spec.get("bob", [0] * len(frames)), "legs": spec["legs"], "fx": spec.get("fx", ""),
        }
    wave = []
    for left, mid in WAVE:
        body, front = layers(left, mid, WAVE_TAIL)
        wave.append({"body": runs(body), "front": runs(front)})
    return {
        "w": W, "h": H, "fxH": FX_H, "colors": {str(k): v for k, v in COLORS.items()},
        "eyeAt": [list(p) for p in EYE_AT], "mouthAt": list(MOUTH_AT),
        "eyes": {k: {"at": list(v[0]), "w": len(v[1][0]), "px": glyph(v[1])} for k, v in EYES.items()},
        "mouths": {k: {"at": list(v[0]), "px": glyph(v[1])} for k, v in MOUTHS.items()},
        "moods": moods, "wave": wave, "waveMs": WAVE_MS, "legs": {k: pieces(v) for k, v in LEGS.items()},
        "walk": WALK, "walkMs": WALK_MS, "talk": TALK, "fx": {k: [pieces(f) for f in frames] for k, frames in FX.items()},
    }


def compose(data, mood, frame=0, eyes=None, mouth=None, legs=None, fx_frame=0):
    """Every layer of one frame as a W x (fxH + H) grid of palette numbers (previews, icons)."""
    m = data["moods"][mood]
    fh = data["fxH"]
    g = grid(W, fh + H)

    def put(rs, dx=0, dy=0):
        for x, y, w, v in rs:
            for i in range(w):
                gx, gy = x + i + dx, y + dy
                if 0 <= gx < W and 0 <= gy < fh + H:
                    g[gy][gx] = v

    f = m["frames"][frame % len(m["frames"])]
    bob = m["bob"][frame % len(m["bob"])] if m["bob"] else 0
    leg = legs or (m["legs"][frame % len(m["legs"])] if isinstance(m["legs"], list) else m["legs"])
    if m["fx"]:
        fxf = data["fx"][m["fx"]]
        put(fxf[fx_frame % len(fxf)])
    put(data["legs"][leg], 0, fh)
    put(f["body"], 0, fh - bob)
    e = data["eyes"][eyes or m["eyes"]]
    for ex, ey in data["eyeAt"]:
        put(e["px"], ex + e["at"][0], fh - bob + ey + e["at"][1])
    mo = mouth or m["mouth"]
    if mo == "talk":
        mo = data["talk"][frame % len(data["talk"])]
    mm = data["mouths"][mo]
    put(mm["px"], data["mouthAt"][0] + mm["at"][0], fh - bob + data["mouthAt"][1] + mm["at"][1])
    put(f["front"], 0, fh - bob)
    return g


# ---------- writing the two files ----------

HEADER = "Generated by scripts/make_scorpi.py from the art in that file. Do not edit; run the script again."


def write(root):
    data = build()
    js = "// " + HEADER + "\n// Scorpi the pixel scorpion: run-length pixels [x, y, width, palette] per layer (see lib/mascot.js).\n"
    js += "export default " + json.dumps(data, separators=(",", ":")) + ";\n"
    py = '"""' + HEADER + '\nScorpi the pixel scorpion: run-length pixels [x, y, width, palette] per layer (see scorpi_paint.py)."""\n\n'
    py += "import json\n\nSPRITES = json.loads(r'''" + json.dumps(data, separators=(",", ":")) + "''')\n"
    with open(os.path.join(root, "public", "js", "lib", "scorpi-sprites.js"), "w", encoding="utf-8", newline="\n") as f:
        f.write(js)
    with open(os.path.join(root, "scorpi", "sprites.py"), "w", encoding="utf-8", newline="\n") as f:
        f.write(py)
    return data


def preview(data, path, k=8):
    from PIL import Image, ImageDraw
    cells = []
    for mood, m in data["moods"].items():
        n = max(len(m["frames"]), len(m["legs"]) if isinstance(m["legs"], list) else 1, 2)
        cells += [("%s %d" % (mood, i), compose(data, mood, i, fx_frame=i)) for i in range(n)]
    cells += [("blink", compose(data, "idle", 0, eyes="blink")), ("happy eyes", compose(data, "idle", 0, eyes="happy"))]
    per_row = 6
    cw, ch = W * k + 16, (data["fxH"] + H) * k + 30
    rows = [cells[i:i + per_row] for i in range(0, len(cells), per_row)]
    img = Image.new("RGB", (per_row * cw, len(rows) * ch), (0, 0, 0))
    d = ImageDraw.Draw(img)
    rgb = {int(i): tuple(int(c[j:j + 2], 16) for j in (1, 3, 5)) for i, c in data["colors"].items()}
    for r, line in enumerate(rows):
        for c, (name, g) in enumerate(line):
            ox, oy = 8 + c * cw, r * ch + 20
            d.text((ox, oy - 16), name, fill=(140, 140, 140))
            for y, row in enumerate(g):
                for x, v in enumerate(row):
                    if v:
                        d.rectangle([ox + x * k, oy + y * k, ox + x * k + k - 1, oy + y * k + k - 1], fill=rgb[v])
    img.save(path)


if __name__ == "__main__":
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data = write(root)
    if "--preview" in sys.argv:
        preview(data, sys.argv[sys.argv.index("--preview") + 1])
    print("Scorpi sprites written: %d moods" % len(data["moods"]))
