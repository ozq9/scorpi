"""Text-to-speech and speech-to-text.

TTS engines: edge (free Microsoft neural voices, online), system (offline Windows SAPI / espeak), fish (Fish Audio, key).
STT on the host: free cloud Whisper (Groq, Mistral Voxtral) when a key is set, or local faster-whisper (offline).
Devices prefer their own recognizer (Android, Edge/Chrome, Safari) and only send audio here as a fallback.
"""

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid

from . import providers as P
from .config import IS_WINDOWS, SETTINGS
from .tools import module_available

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_whisper = {"name": None, "model": None, "device": None}
_whisper_lock = threading.Lock()
_whisper_run = threading.Lock()  # CTranslate2 deadlocks when two threads decode with one model at once
_voice_cache = {}


class VoiceError(Exception):
    pass


def clean_for_speech(text):
    text = re.sub(r"```.*?```", " (code omitted) ", text or "", flags=re.S)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"^\s*[-*•]\s+", "", text, flags=re.M)
    text = re.sub(r"\*\*|__|\*|~~|\|", "", text)
    text = re.sub(r"https?://\S+", "link", text)
    text = re.sub(r"\[\d+\]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def engines():
    out = [{"id": "edge", "name": "Neural voices (free, online)", "available": module_available("edge_tts")},
           {"id": "system", "name": "System voice (offline)", "available": IS_WINDOWS or bool(shutil.which("espeak-ng") or shutil.which("espeak"))},
           {"id": "fish", "name": "Fish Audio (API key)", "available": bool(SETTINGS["fish_api_key"])},
           {"id": "browser", "name": "Browser voice (on each device)", "available": True}]
    return out


def synthesize(text, voice=None, engine=None, rate=None):
    """Return (audio bytes, mime). Falls back through engines so speech always works when any engine does."""
    audio, mime, _used, _note = synthesize_ex(text, voice, engine, rate)
    return audio, mime


def synthesize_ex(text, voice=None, engine=None, rate=None):
    """Like synthesize, plus the engine that spoke and why the engines before it failed ("" when the first one worked)."""
    text = clean_for_speech(text)[:5000]
    if not text:
        raise VoiceError("Nothing to say.")
    engine = engine or SETTINGS["tts_engine"]
    rate = SETTINGS["tts_rate"] if rate is None else rate
    order = [engine] + [e for e in ("edge", "system", "fish") if e != engine]
    errors = []
    for name in order:
        try:
            if name == "edge":
                if not module_available("edge_tts"):
                    raise VoiceError("edge-tts is not installed")
                out = _edge(text, voice if voice and "Neural" in voice else SETTINGS["tts_voice"], rate), "audio/mpeg"
            elif name == "system":
                out = _system(text, voice if voice and "Neural" not in voice else "", rate), "audio/wav"
            elif name == "fish":
                if not SETTINGS["fish_api_key"]:
                    raise VoiceError("no API key saved")
                out = _fish(text, voice if voice and len(voice) == 32 else SETTINGS["fish_voice"], rate), "audio/mpeg"
            else:
                continue
            return out[0], out[1], name, "; ".join(errors)
        except Exception as exc:
            errors.append("%s: %s" % (name, exc))
    raise VoiceError("No text-to-speech engine worked (%s)." % "; ".join(errors) if errors else "No TTS engine available. Run: pip install edge-tts")


def _edge(text, voice, rate):
    import edge_tts

    async def run():
        comm = edge_tts.Communicate(text, voice or "en-US-AvaMultilingualNeural", rate="%+d%%" % int(rate or 0))
        audio = bytearray()
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                audio.extend(chunk["data"])
        return bytes(audio)

    data = asyncio.run(run())
    if not data:
        raise VoiceError("edge-tts returned no audio")
    return data


def _system(text, voice, rate):
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        if IS_WINDOWS:
            select = "try { $s.SelectVoice('%s') } catch {};" % voice.replace("'", "''") if voice else ""
            script = (
                "Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; %s"
                "$s.Rate = %d; $s.SetOutputToWaveFile('%s'); $in = [Console]::In.ReadToEnd(); $s.Speak($in); $s.Dispose()"
            ) % (select, max(-10, min(10, int(rate or 0) // 10)), path.replace("'", "''"))
            subprocess.run(["powershell", "-NoProfile", "-Command", script], input=text.encode("utf-8"), capture_output=True,
                           timeout=120, creationflags=NO_WINDOW, check=True)
        else:
            exe = shutil.which("espeak-ng") or shutil.which("espeak")
            if not exe:
                raise VoiceError("no offline voice installed")
            subprocess.run([exe, "-w", path, "-s", str(165 + int(rate or 0))] + (["-v", voice] if voice else []) + [text],
                           capture_output=True, timeout=120, check=True)
        with open(path, "rb") as fh:
            data = fh.read()
        if len(data) < 100:
            raise VoiceError("system voice produced no audio")
        return data
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


FISH_MODELS = ["s2.1-pro", "s2.1-pro-free", "s2-pro", "s1", "drama-3"]


def _fish_request(path, body=None):
    headers = {"Authorization": "Bearer " + SETTINGS["fish_api_key"].strip(), "User-Agent": "Hearth/1.0"}
    if body is not None:
        headers["Content-Type"] = "application/json"
        headers["model"] = SETTINGS["fish_model"] or "s2.1-pro"
    req = urllib.request.Request("https://api.fish.audio" + path, data=None if body is None else json.dumps(body).encode(),
                                 method="GET" if body is None else "POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        if exc.code == 401:
            detail = "the API key was rejected"
        elif exc.code == 402 or exc.headers.get("x-fish-error-code") == "insufficient_balance":
            detail = "the account has no credits left"
        raise VoiceError("Fish Audio %s: %s" % (exc.code, detail))
    except urllib.error.URLError as exc:
        raise VoiceError("Fish Audio is unreachable (%s)" % exc.reason)


def _fish(text, voice, rate=0):
    body = {"text": text, "format": "mp3", "mp3_bitrate": 128, "normalize": True, "latency": "balanced"}
    if voice:
        body["reference_id"] = voice
    if rate:
        body["prosody"] = {"speed": max(0.5, min(2.0, 1 + int(rate) / 100))}
    data = _fish_request("/v1/tts", body)
    if len(data) < 100:
        raise VoiceError("Fish Audio returned no audio")
    return data


def list_voices(engine):
    if engine in _voice_cache and time.time() - _voice_cache[engine][0] < 3600:
        return _voice_cache[engine][1]
    voices = []
    if engine == "edge" and module_available("edge_tts"):
        import edge_tts
        raw = asyncio.run(edge_tts.list_voices())
        for v in raw:
            voices.append({"id": v["ShortName"], "name": "%s · %s" % (v["ShortName"].split("-")[-1].replace("Neural", "").replace("Multilingual", " (multilingual)"), v["Locale"]),
                           "locale": v["Locale"], "gender": v.get("Gender", "")})
        voices.sort(key=lambda v: (v["locale"], v["id"]))
    elif engine == "system" and IS_WINDOWS:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices() | % { $_.VoiceInfo.Name + '|' + $_.VoiceInfo.Culture }"],
                             capture_output=True, timeout=30, creationflags=NO_WINDOW)
        for line in out.stdout.decode("utf-8", "replace").splitlines():
            if "|" in line:
                name, locale = line.strip().split("|", 1)
                voices.append({"id": name, "name": name, "locale": locale})
    elif engine == "fish":
        if not SETTINGS["fish_api_key"]:
            raise VoiceError("Save your Fish Audio API key below to pick a voice.")
        seen = set()
        # your own voices first, then the most used public voices
        for query, suffix in (("self=true&page_size=100", " · yours"), ("page_size=60&sort_by=task_count", "")):
            data = json.loads(_fish_request("/model?type=tts&" + query) or b"{}")
            for i in data.get("items", []):
                if i.get("_id") and i["_id"] not in seen and i.get("state", "trained") == "trained":
                    seen.add(i["_id"])
                    voices.append({"id": i["_id"], "name": (i.get("title") or "Untitled").strip() + suffix,
                                   "locale": ",".join(i.get("languages") or [])})
    _voice_cache[engine] = (time.time(), voices)
    return voices


# ---------- speech to text ----------
# Order: each cloud Whisper with a key (free tiers first), then local faster-whisper. A provider that just failed
# (rate limit, outage, no network) rests for a while so the next utterance does not wait on it again.

STT_COOLDOWN = 90
_stt_resting = {}  # provider id -> time it may be tried again first
# what Whisper writes for silence or noise: subtitle credits it learned from videos
_CREDITS = re.compile(r"amara\.org|untertitel|subtitles by|captions by|sous-titr|sottotitoli|subtítulos|zdf|"
                      r"thanks? (you )?for watching|danke fürs zuschauen|bis zum nächsten mal", re.I)
_FILLERS = {"you", "thank you", "thanks", "thank you very much", "bye", "okay", "ok", "danke", "vielen dank", "tschüss",
            "merci", "gracias", "ご視聴ありがとうございました", "谢谢观看", "продолжение следует"}


def local_stt_available():
    return module_available("faster_whisper")


def cloud_stt_providers():
    """Ids of every provider with a key that offers speech recognition (free tiers before paid keys)."""
    return [prov["id"] for prov in P.FREE + P.CODING if prov.get("stt") and SETTINGS[P.key_setting(prov["id"])]]


def cloud_stt_provider():
    """Id of the first provider with a key that offers speech recognition, or ''."""
    ids = cloud_stt_providers()
    return ids[0] if ids else ""


def stt_available():
    host = SETTINGS["stt_host"]
    if host == "local":
        return local_stt_available()
    if host == "cloud":
        return bool(cloud_stt_provider())
    return local_stt_available() or bool(cloud_stt_provider())


def _stt_order():
    host = SETTINGS["stt_host"]
    clouds = cloud_stt_providers() if host != "local" else []
    now = time.time()
    order = [p for p in clouds if _stt_resting.get(p, 0) <= now]
    resting = [p for p in clouds if p not in order]
    if local_stt_available() and (host != "cloud" or not clouds):
        order.append("local")
    return order + resting  # a resting provider is still better than nothing


def _whisper_lang(language):
    """Whisper wants ISO 639-1 ('de'), devices use BCP 47 ('de-DE')."""
    if not language or language == "auto":
        return None
    return language.split("-")[0].split("_")[0].lower()


def _is_filler(text, no_speech):
    words = re.sub(r"[^\w\s']", " ", text.lower()).split()
    if not words:
        return True
    if _CREDITS.search(text):
        return True
    return no_speech > 0.25 and " ".join(words) in _FILLERS


def _confidence(segments):
    """Whisper's own certainty about a transcript: the mean log-probability of its segments (higher is surer)."""
    probs = [float(seg.get("avg_logprob") or 0) for seg in segments or []]
    return sum(probs) / len(probs) if probs else -9.0


def _clean_segments(segments, timestamps):
    """Drop segments Whisper itself marks as non-speech, then join them."""
    kept = []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        no_speech = float(seg.get("no_speech_prob") or 0)
        if not text or (no_speech > 0.6 and float(seg.get("avg_logprob") or 0) < -0.8) or _is_filler(text, no_speech):
            continue
        kept.append((float(seg.get("start") or 0), text))
    if timestamps:
        return "\n".join("[%02d:%02d] %s" % (int(start // 60), int(start % 60), text) for start, text in kept)
    return " ".join(text for _start, text in kept).strip()


def _multipart(fields, file_field, filename, data, mime):
    boundary = uuid.uuid4().hex
    out = bytearray()
    for name, value in fields.items():
        out += ("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (boundary, name, value)).encode()
    out += ("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\nContent-Type: %s\r\n\r\n"
            % (boundary, file_field, filename, mime)).encode()
    out += data + ("\r\n--%s--\r\n" % boundary).encode()
    return bytes(out), "multipart/form-data; boundary=" + boundary


AUDIO_MIME = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".wav": "audio/wav", ".ogg": "audio/ogg",
              ".flac": "audio/flac", ".webm": "audio/webm", ".aac": "audio/aac", ".opus": "audio/ogg"}


def _cloud_transcribe(path, language, timestamps, pid=None, prompt=None, scored=False):
    pid = pid or cloud_stt_provider()
    prov = P.BY_ID[pid]
    with open(path, "rb") as fh:
        audio = fh.read()
    if len(audio) > 24 * 1024 * 1024:
        raise VoiceError("Audio is larger than the 25 MB cloud limit.")
    ext = os.path.splitext(path)[1].lower() or ".webm"
    mime = AUDIO_MIME.get(ext, "application/octet-stream")
    # verbose_json carries per-segment no-speech scores (Groq/OpenAI), used to drop what Whisper invents for silence
    verbose = timestamps or pid != "mistral"
    fields = {"model": prov["stt"], "response_format": "verbose_json" if verbose else "json", "temperature": "0"}
    if prompt:
        fields["prompt"] = prompt
    if timestamps and pid == "mistral":
        fields["timestamp_granularities"] = "segment"
    lang = _whisper_lang(language)
    if lang:
        fields["language"] = lang
    body, ctype = _multipart(fields, "file", "audio" + ext, audio, mime)
    base = (SETTINGS["cloud_urls"] or {}).get(pid) or prov["url"]
    req = urllib.request.Request(base.rstrip("/") + "/audio/transcriptions", data=body, method="POST", headers={
        "Content-Type": ctype, "Authorization": "Bearer " + SETTINGS[P.key_setting(pid)], "User-Agent": "Hearth/1.0"})
    # a spoken sentence comes back in well under a second; only long recordings deserve a long wait
    timeout = min(300, 8 + len(audio) / (1024 * 1024) * 25)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", "replace")
        if exc.code == 401:
            detail = "the API key was rejected"
        elif exc.code == 429:
            detail = "rate limited, try again in a minute"
        raise VoiceError("%s speech recognition failed: HTTP %s %s" % (prov["name"], exc.code, detail))
    except (urllib.error.URLError, OSError) as exc:
        raise VoiceError("%s speech recognition is unreachable (%s)" % (prov["name"], getattr(exc, "reason", exc)))
    segments = data.get("segments") or []
    if segments:
        text = _clean_segments(segments, timestamps)
    else:
        text = (data.get("text") or "").strip()
        if _is_filler(text, 0):
            text = ""
    if scored:
        return text, data.get("language") or language, _confidence(segments)
    return text, data.get("language") or language


def _model():
    name = SETTINGS["stt_model"] or "base"
    with _whisper_lock:
        if _whisper["model"] is None or _whisper["name"] != name:
            from faster_whisper import WhisperModel
            try:
                _whisper["model"] = WhisperModel(name, device="auto", compute_type="default")
                _whisper["device"] = _whisper["model"].model.device
            except Exception:
                _whisper["model"] = WhisperModel(name, device="cpu", compute_type="int8")
                _whisper["device"] = "cpu"
            _whisper["name"] = name
        return _whisper["model"]


def _cpu_model():
    """Swap to the CPU model after a GPU failure (missing CUDA/cuDNN libraries are only noticed while decoding)."""
    from faster_whisper import WhisperModel
    with _whisper_lock:
        _whisper["model"] = WhisperModel(_whisper["name"] or SETTINGS["stt_model"] or "base", device="cpu", compute_type="int8")
        _whisper["device"] = "cpu"
        return _whisper["model"]


def warm_up(background=True):
    """Load (and on first use download) the local model ahead of time, so the first words are not slow."""
    if not local_stt_available() or _whisper["model"] is not None:
        return

    def run():
        try:
            import numpy as np
            _transcribe_array(np.zeros(16000, dtype="float32"), None, False)
        except Exception as exc:
            print("Local speech recognition is not ready: %s" % exc)

    if background:
        threading.Thread(target=run, daemon=True, name="whisper-warmup").start()
    else:
        run()


def _decode(path):
    """Audio file -> 16 kHz mono float32. Decoded here rather than by faster-whisper, whose decoder breaks with
    newer PyAV releases."""
    import av
    import numpy as np
    try:
        container = av.open(path, mode="r", metadata_errors="ignore")
    except TypeError:  # PyAV 15+ dropped the argument
        container = av.open(path, mode="r")
    resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=16000)
    parts = []
    with container:
        if not container.streams.audio:
            raise VoiceError("The recording contains no audio.")
        try:
            for frame in container.decode(audio=0):
                for out in resampler.resample(frame):
                    parts.append(out.to_ndarray().reshape(-1))
        except av.error.InvalidDataError:
            pass  # a recording cut off mid-frame (tab closed, network drop): keep what decoded
        for out in resampler.resample(None):
            parts.append(out.to_ndarray().reshape(-1))
    if not parts:
        return np.zeros(0, dtype="float32")
    return np.concatenate(parts).astype("float32") / 32768.0


def _transcribe_array(audio, language, timestamps, prompt=None, scored=False):
    with _whisper_run:
        return _transcribe_locked(audio, language, timestamps, prompt, scored)


def _transcribe_locked(audio, language, timestamps, prompt=None, scored=False):
    model = _model()
    # live speech: greedy decoding on the CPU is several times faster and just as good for a sentence or two
    short = len(audio) < 16000 * 45
    opts = dict(language=language, vad_filter=True, vad_parameters={"min_silence_duration_ms": 500},
                condition_on_previous_text=False, temperature=0.0, initial_prompt=prompt,
                beam_size=1 if short and _whisper.get("device") == "cpu" else 5)
    try:
        segments, info = model.transcribe(audio, **opts)
        segments = list(segments)
    except RuntimeError as exc:
        if not any(k in str(exc).lower() for k in ("cuda", "cudnn", "cublas")):
            raise
        model = _cpu_model()
        opts["beam_size"] = 1 if short else 5
        segments, info = model.transcribe(audio, **opts)
        segments = list(segments)
    rows = [{"start": s.start, "text": s.text, "no_speech_prob": s.no_speech_prob, "avg_logprob": s.avg_logprob} for s in segments]
    if scored:
        return _clean_segments(rows, timestamps), getattr(info, "language", language), _confidence(rows)
    return _clean_segments(rows, timestamps), getattr(info, "language", language)


def _local_transcribe(path, language="auto", timestamps=False, prompt=None, scored=False):
    audio = _decode(path)
    if len(audio) < 1600:  # under 0.1 s
        return ("", language, -9.0) if scored else ("", language)
    return _transcribe_array(audio, _whisper_lang(language), timestamps, prompt, scored)


def transcribe_file(path, language="auto", timestamps=False):
    """Every cloud Whisper with a key, then local faster-whisper (or local only / cloud only, Settings → Voice)."""
    order = _stt_order()
    if not order:
        raise VoiceError("No speech recognition on the host. Add a free Groq key (Connections → Model) or run: pip install faster-whisper")
    errors = []
    for pid in order:
        try:
            if pid == "local":
                return _local_transcribe(path, language, timestamps)
            result = _cloud_transcribe(path, language, timestamps, pid)
            _stt_resting.pop(pid, None)
            return result
        except Exception as exc:
            errors.append(str(exc))
            if pid != "local":
                _stt_resting[pid] = time.time() + STT_COOLDOWN
                warm_up()  # get the local model ready in case the cloud stays down
    raise VoiceError(errors[0] if len(errors) == 1 else "Speech recognition failed: " + "; ".join(errors))


def transcribe_bytes(audio, language="auto", suffix=".webm"):
    if not audio:
        raise VoiceError("No audio received.")
    if len(audio) < 400:  # a container header without any sound
        return "", language
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as fh:
        fh.write(audio)
    try:
        return transcribe_file(path, language)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


# ---------- wake words ----------
# A listening device sends short bursts of speech here. Local Whisper goes first (nothing leaves the PC and there is no
# rate limit), then the cloud. "Hey Hearth" starts a voice conversation, "Hey Ember" calls Ember; what follows in the
# same breath is the first thing said to it.

# Whisper's usual spellings of the two names; the ones that are everyday words only count after a greeting
_WAKE = re.compile(r"^\W*(?P<greet>(?:hey|hi|hello|hallo|hej|okay|ok|yo|na)\W+)?"
                   r"(?P<name>scorpi|scorpy|scorpie|scorpee|skorpi|scorpio|scorpia|scorp|scorpe|skorp|scorb|ember|embers|embar|emba|amber|hearth|hearths|harth|hearst|heart|hoth|huff|hath)\b\W*(?P<rest>.*)$", re.I | re.S)
_WAKE_WORDS = {"scorpi": "ember", "scorpy": "ember", "scorpie": "ember", "scorpee": "ember", "skorpi": "ember",
               "scorpio": "ember", "scorpia": "ember", "ember": "ember",
               "scorp": "hearth", "scorpe": "hearth", "skorp": "hearth", "scorb": "hearth", "embers": "ember", "embar": "ember", "emba": "ember", "amber": "ember",
               "hearth": "hearth", "hearths": "hearth", "harth": "hearth", "hearst": "hearth", "heart": "hearth",
               "hoth": "hearth", "huff": "hearth", "hath": "hearth"}
_EVERYDAY = {"scorpio", "amber", "heart", "embers", "hearths", "hoth", "huff", "hath"}
# names Whisper does not know: the prompt teaches it their spelling (and keeps a one-second clip from being taken
# for another language)
WAKE_PROMPT = "Scorpi, Scorp."  # "Hey Ember. Hey Hearth." made Whisper drop the greeting it had "already heard"


def detect_wake(text):
    """('ember' | 'hearth' | '', what was said after the wake word)."""
    m = _WAKE.match(text or "")
    if not m:
        return "", ""
    name = m.group("name").lower()
    if name in _EVERYDAY and not m.group("greet"):
        return "", ""
    rest = m.group("rest").strip()
    if _is_filler(rest, 1.0) or not re.search(r"\w", rest):
        rest = ""
    return _WAKE_WORDS[name], rest


def wake_check(audio, language="auto", suffix=".wav"):
    """Transcribe a short burst of speech and look for a wake word: {"wake", "rest", "text"}."""
    empty = {"wake": "", "rest": "", "text": ""}
    if not audio or len(audio) < 400:
        return empty
    host = SETTINGS["stt_host"]
    now = time.time()
    order = ["local"] if local_stt_available() and host != "cloud" else []
    if host != "local":
        order += [p for p in cloud_stt_providers() if _stt_resting.get(p, 0) <= now]
    if not order:
        raise VoiceError("No speech recognition on the host for the wake word. Install faster-whisper or add a free Groq key.")
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as fh:
        fh.write(audio)

    def run(lang):
        """(text, Whisper's confidence in it)"""
        for pid in order:
            try:
                if pid == "local":
                    out = _local_transcribe(path, lang, prompt=WAKE_PROMPT, scored=True)
                else:
                    out = _cloud_transcribe(path, lang, False, pid, prompt=WAKE_PROMPT, scored=True)
                return out[0], out[2]
            except Exception:
                if pid != "local":
                    _stt_resting[pid] = time.time() + STT_COOLDOWN
        return "", -9.0

    try:
        # Whisper guesses the language of a one-second clip badly ("Hey Ember" came back as Arabic), so the wake
        # word is listened for in a fixed language: the spoken-language setting, else English
        fixed = language if language and language != "auto" else "en"
        text, sure = run(fixed)
        if text.strip(" .,!").lower() == WAKE_PROMPT.strip(" .").lower():
            text = ""  # Whisper repeating its prompt over noise
        wake, rest = detect_wake(text)
        if wake and rest and fixed != language:
            # something followed the wake word: hear it again in whatever language it is in, and keep the version
            # Whisper is surer of (a German question stays German, an English one is not re-heard as German)
            again, sure2 = run("auto")
            wake2, rest2 = detect_wake(again)
            if wake2 and rest2 and sure2 > sure:
                text, rest = again, rest2
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return {"wake": wake, "rest": rest, "text": text}
