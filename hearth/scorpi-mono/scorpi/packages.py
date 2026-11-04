"""Checks a package before an agent installs it. Models invent package names, and attackers register those names
("slopsquatting") or names one typo away from popular ones ("typosquatting"). Before an install command runs, each
package is looked up in its registry (PyPI, npm, crates.io, RubyGems): one that does not exist is refused, one that is
brand new, barely used or a near-copy of a popular name needs the user's OK.
"""

import datetime as dt
import json
import re
import shlex
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import VERSION
from .config import SETTINGS

NEW_DAYS = 30  # younger than this: ask first
FEW = {"pypi": 300, "npm": 100, "crates": 300, "rubygems": 300}  # downloads in the last month (npm: week) below this: ask first
_cache = {}  # (ecosystem, name) -> (time, info)
_lock = threading.Lock()
CACHE_SECONDS = 6 * 3600

POPULAR = {
    "pypi": """requests numpy pandas urllib3 boto3 botocore setuptools six python-dateutil certifi idna charset-normalizer
typing-extensions packaging pyyaml s3transfer cryptography cffi pycparser attrs jinja2 markupsafe click pip wheel pydantic
pytz protobuf rsa pyasn1 jmespath google-api-core grpcio aiohttp multidict yarl frozenlist platformdirs filelock virtualenv
pyjwt oauthlib requests-oauthlib sqlalchemy psycopg2 psycopg2-binary pymysql redis celery flask django fastapi uvicorn
starlette httpx httpcore anyio sniffio scipy scikit-learn matplotlib seaborn pillow opencv-python torch torchvision
tensorflow keras transformers tokenizers huggingface-hub openai anthropic langchain tqdm colorama rich pytest pytest-cov
coverage tox black flake8 pylint mypy isort ruff beautifulsoup4 lxml selenium playwright scrapy pyarrow polars sympy
networkx pygments docutils sphinx werkzeug itsdangerous gunicorn websockets paramiko pexpect psutil docker kubernetes
pyopenssl toml tomli python-dotenv regex simplejson ujson orjson msgpack marshmallow alembic jsonschema tenacity loguru
structlog arrow pendulum babel nltk spacy gensim xgboost lightgbm statsmodels plotly dash streamlit gradio pydub moviepy
pyinstaller pywin32 pyautogui keyboard pynput tweepy stripe twilio sendgrid google-cloud-storage firebase-admin pymongo
motor elasticsearch sentry-sdk pyperclip openpyxl xlrd python-docx reportlab pypdf pdfplumber fpdf2 qrcode faker
markdown mistune pytesseract easyocr ollama tiktoken""".split(),
    "npm": """react react-dom next vue svelte express lodash axios moment dayjs date-fns typescript webpack vite rollup esbuild
eslint prettier jest mocha chai vitest chalk commander yargs inquirer debug dotenv uuid nanoid classnames clsx tailwindcss
postcss autoprefixer sass styled-components redux react-redux zustand mobx rxjs jquery bootstrap underscore ramda immer
zod yup joi ajv mongoose sequelize prisma mysql2 sqlite3 redis ioredis socket.io cors body-parser cookie-parser helmet
morgan jsonwebtoken bcrypt bcryptjs passport nodemon ts-node concurrently cross-env rimraf glob fs-extra mkdirp semver
minimist node-fetch cheerio puppeteer playwright three chart.js leaflet marked highlight.js katex mathjs firebase
aws-sdk stripe openai electron react-router-dom react-router framer-motion gsap lucide-react react-icons antd
express-session multer sharp jimp pdfkit nodemailer winston pino graphql apollo-server vue-router pinia vuex nuxt astro
solid-js preact htmx.org alpinejs husky lint-staged nodemailer dompurify validator qs formidable""".split(),
    "crates": """serde serde_json tokio rand clap anyhow thiserror regex reqwest log env_logger chrono itertools futures
lazy_static once_cell syn quote proc-macro2 hyper axum actix-web tracing tracing-subscriber uuid base64 bytes sqlx diesel
rayon crossbeam parking_lot hashbrown indexmap tempfile walkdir""".split(),
    "rubygems": """rails rake bundler rspec nokogiri puma sinatra rack devise pg sidekiq redis json activesupport
activerecord thor faraday httparty rubocop pry minitest capybara""".split(),
}
REGISTRY_NAMES = {"pypi": "PyPI", "npm": "npm", "crates": "crates.io", "rubygems": "RubyGems"}


# ---------- reading install commands ----------

INSTALLERS = [
    # (regex on the words, ecosystem, words after which package names follow)
    (re.compile(r"^(?:\S*py(?:thon)?[\d.]*(?:\.exe)?(?:\s+-[\d.]+)?\s+-m\s+)?pip[\d.]*(?:\.exe)?\s+install\b"), "pypi"),
    (re.compile(r"^uv\s+(?:pip\s+install|add)\b"), "pypi"),
    (re.compile(r"^(?:poetry|pdm|rye|hatch)\s+add\b"), "pypi"),
    (re.compile(r"^pipenv\s+install\b"), "pypi"),
    (re.compile(r"^pipx\s+install\b"), "pypi"),
    (re.compile(r"^npm\s+(?:install|i|add|isntall)\b"), "npm"),
    (re.compile(r"^(?:yarn|pnpm|bun)\s+(?:add|install|i)\b"), "npm"),
    (re.compile(r"^(?:npx|pnpx|bunx)\s+"), "npm-run"),
    (re.compile(r"^cargo\s+(?:add|install)\b"), "crates"),
    (re.compile(r"^gem\s+install\b"), "rubygems"),
]
VALUE_FLAGS = {"-r", "--requirement", "-c", "--constraint", "-i", "--index-url", "--extra-index-url", "-f", "--find-links",
               "--target", "-t", "--prefix", "--python", "-p", "--registry", "--prefix", "--cwd", "--version", "--features",
               "--git", "--path", "--branch", "--tag", "--rev", "-e", "--editable", "--source", "--group", "-g"}


def _segments(command):
    return [s.strip() for s in re.split(r"&&|\|\||;|\n|\|", str(command or "")) if s.strip()]


def _name(spec, eco):
    """The package name in a spec ("requests==2.31", "lodash@4", "@scope/pkg@1.0", "serde@1"), or "" for paths and URLs."""
    spec = spec.strip().strip("'\"")
    if not spec or re.match(r"^(\.|/|~|[A-Za-z]:\\|git\+|https?:|file:|link:|workspace:)", spec) or spec.endswith((".whl", ".tar.gz", ".zip", ".tgz")):
        return ""
    if eco == "npm":
        if spec.startswith("@"):
            scope, _, rest = spec[1:].partition("/")
            return "@%s/%s" % (scope, rest.split("@")[0]) if rest else ""
        return spec.split("@")[0]
    if eco == "pypi":
        return re.split(r"[\[<>=!~;@ ]", spec)[0]
    return spec.split("@")[0].split(":")[0]


def installs(command):
    """[(ecosystem, package)] that a shell command would install."""
    found = []
    for seg in _segments(command):
        seg = re.sub(r"^(sudo|env\s+\S+=\S+)\s+", "", seg)
        for rx, eco in INSTALLERS:
            m = rx.match(seg)
            if not m:
                continue
            try:
                words = shlex.split(seg[m.end():], posix=True)
            except ValueError:
                words = seg[m.end():].split()
            if eco == "npm-run":
                # npx runs a package, downloading it if needed: only the first word is a package
                first = next((w for w in words if not w.startswith("-")), "")
                name = _name(first, "npm")
                if name:
                    found.append(("npm", name))
                break
            skip = False
            for w in words:
                if skip:
                    skip = False
                    continue
                if w in VALUE_FLAGS:
                    skip = True
                    continue
                if w.startswith("-"):
                    continue
                name = _name(w, eco)
                if name and re.match(r"^[@A-Za-z0-9][\w.@/-]*$", name):
                    found.append((eco, name))
            break
    return list(dict.fromkeys(found))


# ---------- registries ----------

def _get(url, timeout=6):
    req = urllib.request.Request(url, headers={"User-Agent": "Scorpi/%s (package check)" % VERSION, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace") or "{}")


FETCH = _get  # tests replace it


def _date(text):
    try:
        return dt.datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0


def lookup(eco, name):
    """{"exists": bool|None (None: could not check), "created": epoch, "downloads": n or None, "period": "month"|"week"}."""
    key = (eco, name.lower())
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
    q = urllib.parse.quote(name, safe="@")
    info = {"exists": None, "created": 0, "downloads": None, "period": "month"}
    try:
        if eco == "pypi":
            data = FETCH("https://pypi.org/pypi/%s/json" % q)
            uploads = [f.get("upload_time_iso_8601") or f.get("upload_time") for files in (data.get("releases") or {}).values() for f in files]
            info.update(exists=True, created=min((_date(u) for u in uploads if u), default=0))
            try:
                stats = FETCH("https://pypistats.org/api/packages/%s/recent" % q.lower())
                info["downloads"] = int((stats.get("data") or {}).get("last_month") or 0)
            except (urllib.error.URLError, ValueError, OSError):
                pass
        elif eco == "npm":
            data = FETCH("https://registry.npmjs.org/%s" % q.replace("/", "%2F"))
            info.update(exists=True, created=_date((data.get("time") or {}).get("created")), period="week")
            try:
                stats = FETCH("https://api.npmjs.org/downloads/point/last-week/%s" % q)
                info["downloads"] = int(stats.get("downloads") or 0)
            except (urllib.error.URLError, ValueError, OSError):
                pass
        elif eco == "crates":
            data = FETCH("https://crates.io/api/v1/crates/%s" % q).get("crate") or {}
            info.update(exists=True, created=_date(data.get("created_at")), downloads=int(data.get("recent_downloads") or 0) // 3)
        elif eco == "rubygems":
            data = FETCH("https://rubygems.org/api/v1/gems/%s.json" % q)
            versions = FETCH("https://rubygems.org/api/v1/versions/%s.json" % q)
            info.update(exists=True, created=min((_date(v.get("created_at")) for v in versions or []), default=0),
                        downloads=None if not data.get("downloads") else max(int(data["downloads"]) // 120, 0))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            info["exists"] = False
    except (urllib.error.URLError, ValueError, OSError):
        pass  # offline or blocked: unknown
    with _lock:
        _cache[key] = (time.time(), info)
    return info


# ---------- look-alikes ----------

def _distance(a, b, limit=3):
    """Damerau-Levenshtein distance (with swaps of neighbours), stopping early above limit."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2, prev = None, list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if prev2 is not None and i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        prev2, prev = prev, cur
        if min(cur) > limit:
            return limit + 1
    return prev[-1]


def _flat(name):
    return re.sub(r"[-_.]+", "-", name.lower())


def looks_like(eco, name):
    """The popular package this name imitates, or ""."""
    popular = POPULAR.get(eco, [])
    flat = _flat(name)
    if flat in {_flat(p) for p in popular}:
        return ""
    for p in popular:
        if len(p) < 5:
            continue
        if _distance(flat, _flat(p), 2) <= (1 if len(p) < 8 else 2):
            return p
    return ""


# ---------- the verdict ----------

def review(command):
    """What to do with an install command: {"block": [reasons], "ask": [reasons], "checked": [names]}.
    Nothing to install, or checking off: empty lists."""
    out = {"block": [], "ask": [], "checked": []}
    if not SETTINGS.get("check_packages", True):
        return out
    wanted = installs(command)
    if not wanted:
        return out
    with ThreadPoolExecutor(max_workers=min(6, len(wanted))) as pool:
        infos = list(pool.map(lambda pair: lookup(*pair), wanted))
    now = time.time()
    for (eco, name), info in zip(wanted, infos):
        where = REGISTRY_NAMES[eco]
        out["checked"].append(name)
        twin = looks_like(eco, name)
        if info["exists"] is False:
            out["block"].append("'%s' does not exist on %s%s. Models sometimes invent package names, and attackers register "
                                "them later; use the real package name." % (name, where, (" (did you mean '%s'?)" % twin) if twin else ""))
            continue
        reasons = []
        if twin:
            reasons.append("its name is one typo away from the popular '%s'" % twin)
        if info["created"] and now - info["created"] < NEW_DAYS * 86400:
            reasons.append("it was first published %d day(s) ago" % max(1, int((now - info["created"]) // 86400)))
        if info["downloads"] is not None and info["downloads"] < FEW[eco] and name not in POPULAR.get(eco, []):
            reasons.append("it has only %d downloads in the last %s" % (info["downloads"], info["period"]))
        if reasons:
            out["ask"].append("'%s' on %s: %s" % (name, where, "; ".join(reasons)))
    return out
