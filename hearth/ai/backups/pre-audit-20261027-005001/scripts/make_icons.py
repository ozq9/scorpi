"""Draws Scorp's icon: Scorpi, the little porcelain scorpion, on a black liquid-glass tile. Scorpi comes from the same
vector drawing as the desktop companion (scorpi_paint, GDI+), rendered 4x larger and reduced (supersampling), so every
size from the 16 px tray icon to Discord's 1024 px art has clean, smooth edges.

    python scripts/make_icons.py

Writes public/icons/ (app, manifest and the Windows .ico) and public/icons/discord/ (app icon and Rich Presence art)."""

import os
import sys

from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "public", "icons")
DISCORD = os.path.join(OUT, "discord")

sys.path.insert(0, ROOT)


def render_scorpi(px, mood="idle", cloud=True):
    """Scorpi (on its cloud) as a PIL image of px x px."""
    import scorpi_paint
    from System.Drawing.Imaging import ImageFormat
    from System.IO import MemoryStream
    import io
    painter = scorpi_paint.Painter()
    pose = scorpi_paint.pose_for(mood, 0.0, gaze=(0.0, 0.15))
    pose["fx"] = set()
    pose["cloud"] = cloud
    bmp = painter.render(px, pose)
    stream = MemoryStream()
    bmp.Save(stream, ImageFormat.Png)
    return Image.open(io.BytesIO(bytes(stream.ToArray()))).convert("RGBA")


def tile(size, radius=0.225, glow=True, full=False):
    """The black glass tile: a soft top-lit gradient, a light rim and a faint glow where Scorpi sits. Drawn 4x, then reduced."""
    k = 4
    s = size * k
    base = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    grad = Image.new("RGBA", (s, s))
    gd = ImageDraw.Draw(grad)
    for y in range(s):
        t = y / max(1, s - 1)
        v = int(30 * (1 - t) + 4 * t)
        gd.line([(0, y), (s, y)], fill=(v, v, v + 2, 255))
    if glow:
        light = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        ImageDraw.Draw(light).ellipse([s * 0.18, s * 0.22, s * 0.82, s * 0.86], fill=(255, 255, 255, 46))
        light = light.filter(ImageFilter.GaussianBlur(s * 0.12))
        grad = Image.alpha_composite(grad, light)
    mask = Image.new("L", (s, s), 0)
    r = 0 if full else int(s * radius)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, s - 1, s - 1], radius=r, fill=255)
    base.paste(grad, (0, 0), mask)
    if not full and size >= 48:
        # the liquid rim: light on the top edge fading down the sides
        rim = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        w = max(k, int(s * 0.006))
        ImageDraw.Draw(rim).rounded_rectangle([w // 2, w // 2, s - 1 - w // 2, s - 1 - w // 2], radius=r, outline=(255, 255, 255, 90), width=w)
        fade = Image.linear_gradient("L").resize((s, s)).point(lambda v: 255 - int(v * 0.85))
        rim.putalpha(Image.composite(rim.getchannel("A"), Image.new("L", (s, s), 0), fade))
        base = Image.alpha_composite(base, rim)
    return base.resize((size, size), Image.LANCZOS)


def scorpi(img, size, scale=0.66, lift=0.0):
    """Scorpi filling the tile: on its cloud from 96 px up; smaller icons drop the cloud so the face stays big.
    Drawn 6x larger, reduced with Lanczos and lightly sharpened, so edges stay crisp at every size."""
    from PIL import ImageFilter
    cloud = size >= 96
    k = scale / 0.66  # the maskable icon asks for a smaller figure
    if cloud:
        box = int(round(size * 1.1 * k))
        cy = 0.58  # the figure's centre, as a share of the drawing's height
    else:
        box = int(round(size * (1.4 if size <= 24 else 1.26) * k))
        cy = 0.6
    hi = render_scorpi(box * 6, cloud=cloud).resize((box, box), Image.LANCZOS)
    if size <= 64:
        hi = hi.filter(ImageFilter.UnsharpMask(radius=0.6, percent=70, threshold=1))
    x = (size - box) // 2
    y = int(round(size / 2 - box * cy + size * lift))
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    layer.paste(hi, (x, y), hi)
    return Image.alpha_composite(img, layer)


def icon(size, **kw):
    full = kw.pop("full", False)
    scale = kw.pop("scale", 0.66)
    return scorpi(tile(size, full=full), size, scale=scale, **kw)


def badge(size, kind):
    """Discord's small round badge: a white glyph on a dark circle (code brackets or a speech bubble)."""
    k = 4
    s = size * k
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([0, 0, s - 1, s - 1], fill=(24, 24, 27, 255), outline=(255, 255, 255, 70), width=s // 40)
    wht = (245, 245, 247, 255)
    lw = s // 16
    if kind == "code":
        c = s / 2
        d.line([(c - s * 0.13, c - s * 0.17), (c - s * 0.28, c), (c - s * 0.13, c + s * 0.17)], fill=wht, width=lw, joint="curve")
        d.line([(c + s * 0.13, c - s * 0.17), (c + s * 0.28, c), (c + s * 0.13, c + s * 0.17)], fill=wht, width=lw, joint="curve")
        d.line([(c + s * 0.05, c - s * 0.22), (c - s * 0.05, c + s * 0.22)], fill=wht, width=lw)
    else:
        d.rounded_rectangle([s * 0.24, s * 0.27, s * 0.76, s * 0.63], radius=s * 0.1, outline=wht, width=lw)
        d.polygon([(s * 0.36, s * 0.6), (s * 0.33, s * 0.75), (s * 0.5, s * 0.62)], fill=wht)
        for i in range(3):
            cx = s * (0.38 + 0.12 * i)
            d.ellipse([cx - s * 0.03, s * 0.42, cx + s * 0.03, s * 0.48], fill=wht)
    return img.resize((size, size), Image.LANCZOS)


def svg(png):
    """The browser tab and web manifest icon: the 512 px icon inside an SVG (scales cleanly, one file)."""
    import base64
    import io
    buf = io.BytesIO()
    png.save(buf, "PNG", optimize=True)
    data = base64.b64encode(buf.getvalue()).decode()
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">\n'
            '  <image width="512" height="512" href="data:image/png;base64,%s"/>\n</svg>\n' % data)


def main():
    os.makedirs(DISCORD, exist_ok=True)
    icon(192).save(os.path.join(OUT, "icon-192.png"))
    big = icon(512)
    big.save(os.path.join(OUT, "icon-512.png"))
    icon(512, full=True, scale=0.5).save(os.path.join(OUT, "maskable-512.png"))  # Android crops it to its own shape
    icon(180, full=True).convert("RGB").save(os.path.join(OUT, "apple-touch-icon.png"))
    with open(os.path.join(OUT, "icon.svg"), "w", encoding="utf-8") as fh:
        fh.write(svg(big))
    # Windows: taskbar, title bar, tray and shortcuts. Each size drawn on its own grid.
    sizes = [16, 20, 24, 28, 32, 40, 48, 56, 64, 72, 80, 96, 128, 256]
    frames = [icon(n) for n in sizes]
    for name in ("scorp.ico",):
        frames[-1].save(os.path.join(OUT, name), format="ICO", sizes=[(n, n) for n in sizes], append_images=frames[:-1])
    # Discord: the application icon and Rich Presence art (upload these in the Developer Portal)
    icon(1024).save(os.path.join(DISCORD, "app-icon.png"))
    icon(512).save(os.path.join(DISCORD, "app-icon-512.png"))
    icon(1024, full=True).convert("RGB").save(os.path.join(DISCORD, "scorp.png"))
    badge(512, "code").save(os.path.join(DISCORD, "code.png"))
    badge(512, "chat").save(os.path.join(DISCORD, "chat.png"))
    print("Icons written to", OUT)


if __name__ == "__main__":
    sys.exit(main())
