"""Draws Scorpi's icons: the pixel scorpion on a black tile, scaled up by whole numbers with nearest neighbour, so
every pixel stays a sharp square from the 16 px tray icon to Discord's 1024 px art. Scorpi comes from the same sprites
as the app and the desktop companion (scripts/make_scorpi.py); below 30 px, where the whole scorpion can't fit, its face
(the outlined head with happy eyes) stands in.

    python scripts/make_icons.py [--site <site folder>]

Writes public/icons/ (app, manifest, the Windows .ico and a crisp SVG), public/icons/discord/ (app icon and Rich
Presence art) and server-control/scorpi*.png (Scorpi.exe's splash and the installer's pictures). With --site it also
writes the website's img/icon-192.png."""

import os
import sys

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from make_scorpi import COLORS, HEAD, HEAD_AT, build, compose  # noqa: E402

ROOT = os.path.dirname(ROOT)
OUT = os.path.join(ROOT, "public", "icons")
DISCORD = os.path.join(OUT, "discord")
BG = (0, 0, 0, 255)
RULE = (35, 35, 35, 255)  # #232323, the 1px rule of the app and the site
RGB = {int(k): tuple(int(v[i:i + 2], 16) for i in (1, 3, 5)) + (255,) for k, v in COLORS.items()}

DATA = build()


def scorpi_grid(mood="idle", fx=False):
    """Scorpi as rows of palette numbers, cropped to what is drawn (the frame where it stands on the ground)."""
    m = DATA["moods"][mood]
    frame = next((i for i, b in enumerate(m["bob"]) if not b), 0)
    g = compose(DATA, mood, frame, fx_frame=frame) if fx or not m["fx"] else compose(dict(DATA, moods={**DATA["moods"], mood: dict(m, fx="")}), mood, frame)
    used = [i for i, r in enumerate(g) if any(r)]
    g = g[used[0]:used[-1] + 1]
    left = min(min(i for i, v in enumerate(r) if v) for r in g if any(r))
    right = max(max(i for i, v in enumerate(r) if v) for r in g if any(r))
    return [r[left:right + 1] for r in g]


def face():
    """Just the head and its happy eyes, for icons too small for the whole scorpion."""
    g = compose(DATA, "idle", 0)
    x, y = HEAD_AT
    return [row[x:x + len(HEAD[0])] for row in g[y:y + len(HEAD)]]


def fitted(size, fill):
    """What fits the icon at a whole-number scale: the whole scorpion from 30 px up, below that its face."""
    if size < 30:
        grid = face()
        return grid, max(1, int(size * 0.9 // len(grid[0])))
    grid = scorpi_grid()
    return grid, max(1, int(size * fill // len(grid[0])))


def paint(img, grid, k, x0, y0):
    d = ImageDraw.Draw(img)
    for y, row in enumerate(grid):
        for x, v in enumerate(row):
            if v:
                d.rectangle([x0 + x * k, y0 + y * k, x0 + x * k + k - 1, y0 + y * k + k - 1], fill=RGB[v])


def icon(size, full=False, fill=0.8):
    """The app icon: a black tile (rounded, ruled) with Scorpi in the middle. full: square, edge to edge."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if full:
        d.rectangle([0, 0, size - 1, size - 1], fill=BG)
    else:
        r = max(2, round(size * 0.16))
        d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=BG, outline=RULE if size >= 32 else None, width=max(1, size // 128))
    grid, k = fitted(size, fill)
    w, h = len(grid[0]) * k, len(grid) * k
    paint(img, grid, k, (size - w) // 2, (size - h) // 2)
    return img


def picture(mood, k=8):
    """Scorpi without a tile, effects included (the splash and the installer draw it on their own black)."""
    grid = scorpi_grid(mood, fx=mood != "idle")
    img = Image.new("RGBA", (len(grid[0]) * k, len(grid) * k), (0, 0, 0, 0))
    paint(img, grid, k, 0, 0)
    return img


def svg(size=32):
    """A crisp vector icon for browser tabs and the manifest: the pixels as one path per colour."""
    grid = scorpi_grid()
    k = 1
    ox, oy = (size - len(grid[0])) // 2, (size - len(grid)) // 2
    paths = {}
    for y, row in enumerate(grid):
        x = 0
        while x < len(row):
            v = row[x]
            if not v:
                x += 1
                continue
            s = x
            while x < len(row) and row[x] == v:
                x += 1
            paths[v] = paths.get(v, "") + "M%d %dh%dv1h-%dz" % (ox + s, oy + y, x - s, x - s)
    body = "".join('<path fill="%s" d="%s"/>' % (COLORS[v], d) for v, d in sorted(paths.items()))
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" shape-rendering="crispEdges">'
            '<rect width="%d" height="%d" rx="5" fill="#000"/>%s</svg>\n' % (size, size, size, size, body))


def badge(size, kind):
    """Discord's small round badge: a white pixel glyph on a black circle (code brackets or a speech bubble)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([0, 0, size - 1, size - 1], fill=BG, outline=RULE, width=max(1, size // 64))
    art = {
        "code": ["..#.......#..", ".#....#....#.", "#....#......#", ".#..#......#.", "..#.#.....#.."],
        "chat": ["#########", "#.......#", "#.#.#.#.#", "#.......#", "#########", ".##......", "#........"],
    }[kind]
    grid = [[1 if c == "#" else 0 for c in row] for row in art]
    k = size * 0.5 // len(grid[0])
    k = int(k)
    paint(img, grid, k, (size - len(grid[0]) * k) // 2, (size - len(grid) * k) // 2)
    return img


def main():
    os.makedirs(DISCORD, exist_ok=True)
    icon(192).save(os.path.join(OUT, "icon-192.png"))
    big = icon(512)
    big.save(os.path.join(OUT, "icon-512.png"))
    icon(512, full=True, fill=0.55).save(os.path.join(OUT, "maskable-512.png"))  # Android crops it to its own shape
    icon(180, full=True, fill=0.7).convert("RGB").save(os.path.join(OUT, "apple-touch-icon.png"))
    with open(os.path.join(OUT, "icon.svg"), "w", encoding="utf-8") as fh:
        fh.write(svg())
    # Windows: taskbar, title bar, tray and shortcuts. Each size drawn on its own grid.
    sizes = [16, 20, 24, 28, 32, 40, 48, 56, 64, 72, 80, 96, 128, 256]
    frames = [icon(n) for n in sizes]
    frames[-1].save(os.path.join(OUT, "scorpi.ico"), format="ICO", sizes=[(n, n) for n in sizes], append_images=frames[:-1])
    # Discord: the application icon and Rich Presence art (upload these in the Developer Portal)
    icon(1024).save(os.path.join(DISCORD, "app-icon.png"))
    big.save(os.path.join(DISCORD, "app-icon-512.png"))
    icon(1024, full=True, fill=0.7).convert("RGB").save(os.path.join(DISCORD, "scorpi.png"))
    badge(512, "code").save(os.path.join(DISCORD, "code.png"))
    badge(512, "chat").save(os.path.join(DISCORD, "chat.png"))
    # Scorpi.exe's splash and the installer's pictures (built into the exes)
    for mood, name in (("idle", "scorpi.png"), ("happy", "scorpi-happy.png"), ("working", "scorpi-working.png")):
        picture(mood).save(os.path.join(ROOT, "server-control", name), optimize=True)
    if "--site" in sys.argv:
        site = sys.argv[sys.argv.index("--site") + 1]
        icon(192).save(os.path.join(site, "img", "icon-192.png"))
    print("Icons written to", OUT)


if __name__ == "__main__":
    sys.exit(main())
