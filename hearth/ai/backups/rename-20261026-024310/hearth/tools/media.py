"""Images, logos, diagrams and audio."""

import base64
import json
import os
import re
import time
import urllib.parse
import urllib.request

from ..config import SETTINGS
from . import Result, ToolError, resolve_path, tool

STYLES = {
    "photo": "photorealistic, natural light, high detail",
    "illustration": "editorial illustration, soft texture, calm palette, clean composition",
    "logo": "minimal flat vector logo, simple geometric shapes, centered on plain background, no text artifacts",
    "icon": "simple flat icon, centered, plain background",
    "watercolor": "watercolor painting, paper texture, soft edges",
    "3d": "soft 3d render, clay material, studio lighting",
    "sketch": "pencil sketch, fine lines, white paper",
    "diagram": "clean infographic diagram, flat, labeled, white background",
}


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "image"


def _sd_available():
    url = (SETTINGS["sd_url"] or "").rstrip("/")
    if not url:
        return False
    try:
        urllib.request.urlopen(url + "/sdapi/v1/sd-models", timeout=2).read()
        return True
    except Exception:
        return False


def _sd_generate(prompt, negative, width, height, seed):
    url = SETTINGS["sd_url"].rstrip("/") + "/sdapi/v1/txt2img"
    payload = {"prompt": prompt, "negative_prompt": negative, "width": width, "height": height, "steps": 28, "seed": seed, "cfg_scale": 6}
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        data = json.loads(resp.read())
    return base64.b64decode(data["images"][0])


def _pollinations(prompt, width, height, seed):
    query = urllib.parse.urlencode({"width": width, "height": height, "seed": seed, "nologo": "true", "model": "flux"})
    url = "https://image.pollinations.ai/prompt/%s?%s" % (urllib.parse.quote(prompt[:1500]), query)
    req = urllib.request.Request(url, headers={"User-Agent": "Hearth/1.0"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = resp.read()
    if len(data) < 2000:
        raise ToolError("Image service returned no image.")
    return data


@tool(
    "generate_image",
    "Generate an image: illustrations, logos, icons, photos, concept art. Uses a local Stable Diffusion (AUTOMATIC1111/Forge) "
    "if running on the host, otherwise the free Pollinations service. Write a detailed visual prompt in English.",
    {
        "prompt": "string: Detailed description of the image",
        "style?": "string[photo,illustration,logo,icon,watercolor,3d,sketch,diagram,none]: Visual style",
        "width?": "integer: Pixels (default 1024)",
        "height?": "integer: Pixels (default 1024)",
        "negative?": "string: Things to avoid",
        "seed?": "integer: Seed for reproducibility",
        "filename?": "string: Output name (png)",
    },
    group="media",
    risk="write",
)
def generate_image(ctx, prompt, style="illustration", width=1024, height=1024, negative="", seed=None, filename=""):
    full = prompt.strip()
    if style and style != "none" and style in STYLES:
        full = "%s, %s" % (full, STYLES[style])
    width = max(256, min(int(width or 1024), 2048)) // 8 * 8
    height = max(256, min(int(height or 1024), 2048)) // 8 * 8
    seed = int(seed) if seed not in (None, "") else int(time.time()) % 100000
    provider = SETTINGS["image_provider"]
    used = ""
    data = None
    errors = []
    if provider in ("auto", "sd") and _sd_available():
        try:
            data = _sd_generate(full, negative or "blurry, distorted, watermark, text artifacts", width, height, seed)
            used = "Stable Diffusion (local)"
        except Exception as exc:
            errors.append("local SD: %s" % exc)
    if data is None and provider in ("auto", "pollinations"):
        try:
            data = _pollinations(full, width, height, seed)
            used = "Pollinations"
        except Exception as exc:
            errors.append("Pollinations: %s" % exc)
    if data is None:
        raise ToolError("Image generation failed (%s)." % "; ".join(errors or ["no provider available"]))
    ext = ".jpg" if data[:3] == b"\xff\xd8\xff" else ".png"
    name = filename or "%s-%s" % (_slug(prompt), seed)
    out = resolve_path(os.path.join("images", os.path.splitext(name)[0] + ext))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "wb") as fh:
        fh.write(data)
    return Result(
        "Generated image with %s (seed %s): %s" % (used, seed, out),
        display={"type": "image", "path": out, "caption": prompt[:140]},
        files=[out],
    )


@tool(
    "create_diagram",
    "Render a diagram from Mermaid syntax (flowchart, sequenceDiagram, classDiagram, erDiagram, gantt, mindmap, timeline, pie). "
    "The diagram is shown to the user and saved as .mmd.",
    {"mermaid": "string: Mermaid source code (no ``` fences)", "title?": "string: Caption", "filename?": "string: Output name"},
    group="media",
    risk="write",
)
def create_diagram(ctx, mermaid, title="", filename=""):
    source = re.sub(r"^```(?:mermaid)?\s*|\s*```$", "", (mermaid or "").strip())
    if not source:
        raise ToolError("Empty diagram.")
    out = resolve_path(os.path.join("diagrams", (filename or _slug(title or "diagram-%d" % time.time())) + ".mmd"))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(source)
    return Result("Diagram rendered for the user and saved to %s" % out, display={"type": "mermaid", "code": source, "caption": title, "path": out}, files=[out])


@tool(
    "create_svg",
    "Create a vector graphic (logo, icon, badge, simple illustration) by writing SVG markup. Saved as .svg and shown.",
    {"svg": "string: Complete <svg ...>...</svg> markup with viewBox", "filename?": "string: Output name", "title?": "string: Caption"},
    group="media",
    risk="write",
)
def create_svg(ctx, svg, filename="", title=""):
    markup = (svg or "").strip()
    markup = re.sub(r"^```(?:svg|xml)?\s*|\s*```$", "", markup)
    if "<svg" not in markup:
        raise ToolError("Markup must contain an <svg> element.")
    markup = re.sub(r"(?is)<script.*?</script>|\son\w+\s*=\s*\"[^\"]*\"", "", markup)
    out = resolve_path(os.path.join("images", (filename or _slug(title or "graphic")).replace(".svg", "") + ".svg"))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(markup)
    return Result("SVG saved to %s" % out, display={"type": "image", "path": out, "caption": title}, files=[out])


@tool(
    "text_to_speech",
    "Convert text into a spoken audio file (MP3) with a natural voice. The user gets a player and download.",
    {"text": "string: Text to speak", "voice?": "string: Voice id (e.g. en-US-AvaMultilingualNeural, de-DE-KatjaNeural)", "filename?": "string: Output name"},
    group="media",
    risk="write",
)
def text_to_speech(ctx, text, voice="", filename=""):
    from .. import voice as voice_mod
    audio, mime = voice_mod.synthesize(text, voice=voice or None)
    ext = ".mp3" if "mpeg" in mime else ".wav"
    out = resolve_path(os.path.join("audio", (filename or _slug(text[:40])).rsplit(".", 1)[0] + ext))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "wb") as fh:
        fh.write(audio)
    return Result("Audio saved to %s" % out, display={"type": "audio", "path": out, "caption": text[:120]}, files=[out])


@tool(
    "transcribe_audio",
    "Transcribe speech in an audio or video file (mp3, wav, m4a, webm, mp4...) to text with Whisper (free cloud or local).",
    {"path": "string: Audio/video file", "language?": "string: Language code or auto"},
    group="media",
)
def transcribe_audio(ctx, path, language="auto"):
    from .. import voice as voice_mod
    path = resolve_path(path, must_exist=True)
    text, lang = voice_mod.transcribe_file(path, language or "auto", timestamps=True)
    return Result("Transcript (%s) of %s:\n\n%s" % (lang, path, text or "(no speech found)"))
