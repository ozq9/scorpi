"""Personal knowledge base (RAG): chunk documents, embed with the local model, retrieve with citations."""

import array
import math
import os
import re
import threading
import time
from collections import Counter

from . import db, events, llm
from .tools import Result, ToolError, resolve_path, tool

CHUNK = 1100
OVERLAP = 180
INDEXABLE = (".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".md", ".markdown", ".csv", ".json", ".html", ".htm", ".py", ".js", ".ts", ".rst", ".log")
_index_lock = threading.Lock()


def _chunks(text):
    text = re.sub(r"\n{3,}", "\n\n", text)
    paragraphs = re.split(r"\n\s*\n", text)
    out, current = [], ""
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) + 2 <= CHUNK:
            current = (current + "\n\n" + para).strip()
            continue
        if current:
            out.append(current)
        while len(para) > CHUNK:
            cut = para.rfind(". ", 0, CHUNK)
            cut = cut + 1 if cut > CHUNK // 2 else CHUNK
            out.append(para[:cut].strip())
            para = para[max(0, cut - OVERLAP):]
        current = para
    if current:
        out.append(current)
    return out


def _pack(vec):
    return array.array("f", vec).tobytes()


def _unpack(blob):
    a = array.array("f")
    a.frombytes(blob)
    return a


def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def add_document(path, title=""):
    from .tools.docs import extract_text
    path = os.path.abspath(path)
    text = extract_text(path, max_chars=2_000_000)
    if not text.strip():
        raise ToolError("No text could be extracted from %s" % path)
    chunks = _chunks(text)
    with _index_lock:
        old = db.one("SELECT id FROM kb_docs WHERE path=?", (path,))
        if old:
            db.x("DELETE FROM kb_chunks WHERE doc_id=?", (old["id"],))
            db.x("DELETE FROM kb_docs WHERE id=?", (old["id"],))
        doc_id = db.x("INSERT INTO kb_docs (title, path, added, chunks) VALUES (?,?,?,?)",
                      (title or os.path.basename(path), path, time.time(), len(chunks)))
        embedded = 0
        for start in range(0, len(chunks), 16):
            batch = chunks[start:start + 16]
            vectors = llm.embed(batch)
            for i, chunk in enumerate(batch):
                vec = vectors[i] if vectors and i < len(vectors) else None
                if vec:
                    embedded += 1
                db.x("INSERT INTO kb_chunks (doc_id, ord, text, emb) VALUES (?,?,?,?)",
                     (doc_id, start + i, chunk, _pack(vec) if vec else None))
        db.x("UPDATE kb_docs SET embedded=? WHERE id=?", (1 if embedded == len(chunks) else 0, doc_id))
    events.publish("knowledge", {})
    return {"id": doc_id, "title": title or os.path.basename(path), "chunks": len(chunks), "embedded": embedded}


def add_folder(folder):
    added = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__")]
        for name in files:
            if name.lower().endswith(INDEXABLE):
                try:
                    added.append(add_document(os.path.join(root, name)))
                except Exception:
                    continue
            if len(added) >= 500:
                return added
    return added


_TOKEN = re.compile(r"[\wÀ-ɏ]{2,}")


def _tokens(text):
    return [t.lower() for t in _TOKEN.findall(text)]


def search(query, k=6):
    rows = db.q("SELECT c.id, c.doc_id, c.ord, c.text, c.emb, d.title, d.path FROM kb_chunks c JOIN kb_docs d ON d.id=c.doc_id")
    if not rows:
        return []
    scored = []
    qvec = None
    if any(r["emb"] for r in rows):
        vecs = llm.embed([query])
        qvec = vecs[0] if vecs else None
    if qvec:
        for r in rows:
            if r["emb"]:
                scored.append((_cos(qvec, _unpack(r["emb"])), r))
    # keyword (BM25-style) scoring, blended in or used alone
    q_terms = set(_tokens(query))
    if q_terms:
        docs = [Counter(_tokens(r["text"])) for r in rows]
        avg = sum(sum(d.values()) for d in docs) / max(1, len(docs))
        df = Counter(t for d in docs for t in set(d) if t in q_terms)
        n = len(docs)
        kw = {}
        for r, counts in zip(rows, docs):
            length = sum(counts.values()) or 1
            score = 0.0
            for t in q_terms:
                if counts.get(t):
                    idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                    tf = counts[t]
                    score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / avg))
            kw[r["id"]] = score
        top = max(kw.values()) or 1.0
        if scored:
            scored = [(s * 0.75 + 0.25 * kw.get(r["id"], 0) / top, r) for s, r in scored]
        else:
            scored = [(kw[r["id"]] / top, r) for r in rows if kw[r["id"]] > 0]
    scored.sort(key=lambda sr: -sr[0])
    return [{"score": round(s, 3), "title": r["title"], "path": r["path"], "chunk": r["ord"], "text": r["text"]} for s, r in scored[:k]]


@tool(
    "knowledge_search",
    "Search the user's personal knowledge base (documents they added to Scorp's Library) and return relevant passages with "
    "sources. Cite them as [title §chunk].",
    {"query": "string: What to look for", "k?": "integer: Number of passages (default 6)"},
    group="knowledge",
)
def knowledge_search(ctx, query, k=6):
    hits = search(query, int(k or 6))
    if not hits:
        return Result("The knowledge base has nothing relevant (or is empty). Add documents in Library → Knowledge.")
    return Result("\n\n".join("[%s §%d] (score %.2f)\n%s" % (h["title"], h["chunk"], h["score"], h["text"]) for h in hits),
                  display={"type": "sources", "query": query, "results": [{"title": "%s §%d" % (h["title"], h["chunk"]), "url": "", "snippet": h["text"][:220]} for h in hits]})


@tool(
    "knowledge_add",
    "Add a document or a whole folder to the knowledge base so it can be searched later.",
    {"path": "string: File or folder path"},
    group="knowledge",
    risk="write",
)
def knowledge_add(ctx, path):
    path = resolve_path(path, must_exist=True)
    if os.path.isdir(path):
        added = add_folder(path)
        return Result("Indexed %d documents from %s." % (len(added), path))
    info = add_document(path)
    return Result("Indexed %s (%d chunks, %d embedded)." % (info["title"], info["chunks"], info["embedded"]))


def doc_count():
    row = db.one("SELECT COUNT(*) AS n FROM kb_docs")
    return row["n"] if row else 0
