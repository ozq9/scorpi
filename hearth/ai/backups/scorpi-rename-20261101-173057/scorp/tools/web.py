"""Web search, page reading and generic HTTP API calls."""

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from . import Result, ToolError, module_available, tool

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36 Scorp/1.0"


def fetch(url, data=None, headers=None, method=None, timeout=25, limit=4 * 1024 * 1024):
    hdrs = {"User-Agent": UA, "Accept-Language": "en,de;q=0.8"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read(limit)
        return resp.status, dict(resp.headers), body


def html_to_text(raw):
    text = re.sub(r"(?is)<(script|style|noscript|svg|header|footer|nav|form|iframe)[^>]*>.*?</\1>", " ", raw)
    title = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>|</tr>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "• ", text)
    text = re.sub(r"(?i)<h([1-6])[^>]*>", lambda m: "\n" + "#" * int(m.group(1)) + " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return (html.unescape(title.group(1)).strip() if title else ""), text.strip()


def _public_host(url):
    """False for this PC and the local network: link previews never make the server knock on those."""
    import ipaddress
    import socket
    host = urllib.parse.urlparse(url).hostname or ""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
    return True


def link_preview(url):
    """{url, title, description, image, icon, site, kind, frameable} for a page, picture or video link."""
    parsed = urllib.parse.urlparse(url)
    out = {"url": url, "site": (parsed.hostname or "").removeprefix("www."), "title": "", "description": "", "image": "",
           "icon": "", "kind": "page", "frameable": False}
    if not _public_host(url):
        return out
    try:
        status, headers, body = fetch(url, timeout=8, limit=600 * 1024)
    except Exception:
        return out
    headers = {k.lower(): v for k, v in headers.items()}
    ctype = headers.get("content-type", "").lower()
    for kind in ("image", "video", "audio"):
        if ctype.startswith(kind + "/"):
            out["kind"] = kind
            return out
    if "pdf" in ctype:
        out["kind"] = "pdf"
        return out
    xfo = headers.get("x-frame-options", "").lower()
    csp = headers.get("content-security-policy", "").lower()
    out["frameable"] = not xfo and "frame-ancestors" not in csp
    charset = re.search(r"charset=([\w-]+)", ctype)
    raw = body.decode(charset.group(1) if charset else "utf-8", "replace")
    head = raw[:300000]

    def meta(*names):
        for name in names:
            for pattern in (r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]*content=["\']([^"\']*)' % re.escape(name),
                            r'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\']%s["\']' % re.escape(name)):
                m = re.search(pattern, head, re.I)
                if m and m.group(1).strip():
                    return html.unescape(m.group(1).strip())
        return ""

    title = re.search(r"(?is)<title[^>]*>(.*?)</title>", head)
    out["title"] = meta("og:title", "twitter:title") or (html.unescape(title.group(1)).strip() if title else "")
    out["description"] = meta("og:description", "twitter:description", "description")[:300]
    out["site"] = meta("og:site_name") or out["site"]
    image = meta("og:image", "og:image:url", "twitter:image", "twitter:image:src")
    out["image"] = urllib.parse.urljoin(url, image) if image else ""
    icon = re.search(r'<link[^>]+rel=["\'][^"\']*icon[^"\']*["\'][^>]*href=["\']([^"\']+)', head, re.I) or \
        re.search(r'<link[^>]+href=["\']([^"\']+)["\'][^>]*rel=["\'][^"\']*icon', head, re.I)
    out["icon"] = urllib.parse.urljoin(url, html.unescape(icon.group(1))) if icon else urllib.parse.urljoin(url, "/favicon.ico")
    return out


def _ddg_html(query, limit):
    data = urllib.parse.urlencode({"q": query}).encode()
    _, _, body = fetch("https://html.duckduckgo.com/html/", data=data, method="POST")
    page = body.decode("utf-8", "replace")
    results = []
    for block in re.findall(r'(?s)<div class="result results_links.*?</div>\s*</div>', page):
        link = re.search(r'class="result__a" href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not link:
            continue
        href = html.unescape(link.group(1))
        if "uddg=" in href:
            href = urllib.parse.unquote(re.search(r"uddg=([^&]+)", href).group(1))
        snippet = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, re.S)
        results.append({
            "title": html.unescape(re.sub("<[^>]+>", "", link.group(2))).strip(),
            "url": href,
            "snippet": html.unescape(re.sub("<[^>]+>", "", snippet.group(1))).strip() if snippet else "",
        })
        if len(results) >= limit:
            break
    return results


def search(query, limit=8, kind="text"):
    if module_available("ddgs"):
        try:
            from ddgs import DDGS
            with DDGS() as ddgs:
                if kind == "news":
                    rows = ddgs.news(query, max_results=limit)
                    return [{"title": r.get("title"), "url": r.get("url"), "snippet": r.get("body"), "date": r.get("date"), "source": r.get("source")} for r in rows]
                rows = ddgs.text(query, max_results=limit)
                return [{"title": r.get("title"), "url": r.get("href"), "snippet": r.get("body")} for r in rows]
        except Exception:
            pass
    return _ddg_html(query, limit)


@tool(
    "web_search",
    "Search the web (DuckDuckGo). Returns titles, URLs and snippets. Search several phrasings for research, "
    "then read the best pages with fetch_page. Use kind='news' for recent events.",
    {"query": "string: Search query", "max_results?": "integer: 1-15 (default 8)", "kind?": "string[text,news]: Result type"},
    group="web",
)
def web_search(ctx, query, max_results=8, kind="text"):
    try:
        rows = search(query, min(int(max_results or 8), 15), kind or "text")
    except Exception as exc:
        raise ToolError("Search failed: %s" % exc)
    if not rows:
        return Result("No results for %r." % query)
    lines = []
    for i, r in enumerate(rows, 1):
        extra = " (%s %s)" % (r.get("source") or "", r.get("date") or "") if r.get("date") else ""
        lines.append("[%d] %s%s\n%s\n%s" % (i, r.get("title"), extra, r.get("url"), r.get("snippet") or ""))
    return Result("Results for %r:\n\n%s" % (query, "\n\n".join(lines)), display={"type": "sources", "query": query, "results": rows})


@tool(
    "fetch_page",
    "Download a web page and return its readable text (for reading articles, docs, or verifying claims).",
    {"url": "string: http(s) URL", "max_chars?": "integer: Limit (default 15000)"},
    group="web",
)
def fetch_page(ctx, url, max_chars=15000):
    if not re.match(r"https?://", url or ""):
        raise ToolError("URL must start with http:// or https://")
    try:
        status, headers, body = fetch(url)
    except urllib.error.HTTPError as exc:
        raise ToolError("HTTP %s for %s" % (exc.code, url))
    except Exception as exc:
        raise ToolError("Could not fetch %s: %s" % (url, exc))
    ctype = headers.get("Content-Type", "")
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        import io
        if not module_available("pypdf"):
            raise ToolError("PDF page: install pypdf to read it.")
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(body))
        text = "\n".join((p.extract_text() or "") for p in reader.pages[:40])
        title = url
    elif "json" in ctype:
        title, text = url, body.decode("utf-8", "replace")
    else:
        charset = re.search(r"charset=([\w-]+)", ctype)
        raw = body.decode(charset.group(1) if charset else "utf-8", "replace")
        title, text = html_to_text(raw)
    limit = int(max_chars or 15000)
    note = "\n... [truncated]" if len(text) > limit else ""
    return Result("Title: %s\nURL: %s\n\n%s%s" % (title, url, text[:limit], note), display={"type": "link", "url": url, "title": title})


@tool(
    "wikipedia",
    "Look up a topic on Wikipedia and return the article summary (or full intro). Good second source for facts.",
    {"topic": "string: Article title or search words", "lang?": "string: Language code (default en)"},
    group="web",
)
def wikipedia(ctx, topic, lang="en"):
    lang = re.sub(r"[^a-z-]", "", (lang or "en").lower()) or "en"
    base = "https://%s.wikipedia.org" % lang
    try:
        _, _, body = fetch(base + "/w/api.php?" + urllib.parse.urlencode(
            {"action": "query", "list": "search", "srsearch": topic, "format": "json", "srlimit": 3}))
        hits = json.loads(body).get("query", {}).get("search", [])
        if not hits:
            return Result("No Wikipedia article found for %r." % topic)
        title = hits[0]["title"]
        _, _, body = fetch(base + "/w/api.php?" + urllib.parse.urlencode(
            {"action": "query", "prop": "extracts", "explaintext": 1, "exintro": 0, "titles": title, "format": "json", "exchars": 6000}))
        pages = json.loads(body).get("query", {}).get("pages", {})
        extract = next(iter(pages.values())).get("extract", "")
    except Exception as exc:
        raise ToolError("Wikipedia lookup failed: %s" % exc)
    url = "%s/wiki/%s" % (base, urllib.parse.quote(title.replace(" ", "_")))
    others = ", ".join(h["title"] for h in hits[1:])
    return Result("%s\n%s\n\n%s%s" % (title, url, extract, "\n\nRelated: " + others if others else ""))


def _http(method, url, headers=None, body=None, json_body=None, timeout=30):
    if not re.match(r"https?://", url or ""):
        raise ToolError("URL must start with http:// or https://")
    data = None
    hdrs = dict(headers or {})
    if json_body is not None:
        data = json.dumps(json_body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    elif body:
        data = body.encode() if isinstance(body, str) else body
    try:
        status, rheaders, raw = fetch(url, data=data, headers=hdrs, method=method.upper(), timeout=timeout)
    except urllib.error.HTTPError as exc:
        status, rheaders, raw = exc.code, dict(exc.headers), exc.read()
    except Exception as exc:
        raise ToolError("Request failed: %s" % exc)
    text = raw.decode("utf-8", "replace")
    ctype = rheaders.get("Content-Type", "")
    if "html" in ctype:
        text = html_to_text(text)[1]
    elif "json" in ctype:
        try:
            text = json.dumps(json.loads(text), indent=1, ensure_ascii=False)
        except ValueError:
            pass
    return Result("HTTP %s %s\nContent-Type: %s\n\n%s" % (status, url, ctype, text[:20000]), ok=status < 400)


@tool(
    "http_get",
    "Call a REST/JSON API or URL with GET and return the response (JSON pretty-printed). Use for public APIs "
    "and local services (e.g. http://127.0.0.1:port/...).",
    {"url": "string: Full URL including query string", "headers?": {"type": "object", "description": "Optional headers"}},
    group="web",
)
def http_get(ctx, url, headers=None):
    return _http("GET", url, headers)


@tool(
    "http_request",
    "Send an HTTP request with any method (POST, PUT, PATCH, DELETE) to an external or local service API.",
    {
        "method": "string[POST,PUT,PATCH,DELETE,GET]: HTTP method",
        "url": "string: Full URL",
        "headers?": {"type": "object", "description": "Request headers"},
        "json?": {"type": "object", "description": "JSON body"},
        "body?": "string: Raw body (if not JSON)",
    },
    group="web",
    risk="danger",
)
def http_request(ctx, method, url, headers=None, json=None, body=""):
    return _http(method, url, headers, body, json)
