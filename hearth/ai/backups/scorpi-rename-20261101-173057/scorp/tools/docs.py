"""Office documents: PDF, Word, Excel, PowerPoint, CSV."""

import csv
import difflib
import io
import os
import re

from . import Result, ToolError, module_available, resolve_path, tool
from .files import file_card

DOC_LIMIT = 80_000


def extract_text(path, max_chars=DOC_LIMIT):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        if not module_available("pypdf"):
            raise ToolError("Reading PDFs needs: pip install pypdf")
        from pypdf import PdfReader
        reader = PdfReader(path)
        parts = []
        for i, page in enumerate(reader.pages):
            parts.append("--- page %d ---\n%s" % (i + 1, (page.extract_text() or "").strip()))
            if sum(len(p) for p in parts) > max_chars:
                parts.append("... [%d pages total, truncated]" % len(reader.pages))
                break
        text = "\n".join(parts)
        if len(text.strip()) < 40 * len(reader.pages[:3]):
            text += "\n(note: little text found; this PDF may be scanned images)"
        return text[:max_chars]
    if ext == ".docx":
        if not module_available("docx"):
            raise ToolError("Reading Word files needs: pip install python-docx")
        import docx
        d = docx.Document(path)
        out = []
        for para in d.paragraphs:
            if not para.text.strip():
                continue
            style = (para.style.name or "").lower() if para.style is not None else ""
            if style.startswith("heading"):
                level = re.sub(r"\D", "", style) or "1"
                out.append("#" * min(int(level), 4) + " " + para.text)
            elif "list" in style:
                out.append("- " + para.text)
            else:
                out.append(para.text)
        for ti, table in enumerate(d.tables):
            out.append("\n[table %d]" % (ti + 1))
            for row in table.rows:
                out.append(" | ".join(cell.text.strip() for cell in row.cells))
        return "\n".join(out)[:max_chars]
    if ext in (".xlsx", ".xlsm"):
        if not module_available("openpyxl"):
            raise ToolError("Reading Excel needs: pip install openpyxl")
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        out = []
        for ws in wb.worksheets:
            out.append("--- sheet: %s (%s rows x %s cols) ---" % (ws.title, ws.max_row, ws.max_column))
            for r, row in enumerate(ws.iter_rows(values_only=True)):
                if r >= 300:
                    out.append("... more rows")
                    break
                if any(v is not None for v in row):
                    out.append(" | ".join("" if v is None else str(v) for v in row))
        return "\n".join(out)[:max_chars]
    if ext == ".pptx":
        if not module_available("pptx"):
            raise ToolError("Reading PowerPoint needs: pip install python-pptx")
        from pptx import Presentation
        prs = Presentation(path)
        out = []
        for i, slide in enumerate(prs.slides):
            out.append("--- slide %d ---" % (i + 1))
            for shape in slide.shapes:
                if shape.has_text_frame and shape.text_frame.text.strip():
                    out.append(shape.text_frame.text)
        return "\n".join(out)[:max_chars]
    with open(path, "rb") as fh:
        raw = fh.read(max_chars * 2)
    if b"\x00" in raw[:2048]:
        raise ToolError("Unsupported binary format: %s" % ext)
    return raw.decode("utf-8", "replace")[:max_chars]


@tool(
    "read_document",
    "Extract the text of a PDF, Word (.docx), Excel (.xlsx), PowerPoint (.pptx), CSV or text file. "
    "Use for summarizing, extracting key information or answering questions about a document.",
    {"path": "string: Document path"},
    group="docs",
)
def read_document(ctx, path):
    path = resolve_path(path, must_exist=True)
    text = extract_text(path)
    return Result("Document: %s (%d characters)\n\n%s" % (path, len(text), text))


# ---------- creation ----------

def _blocks(markdown):
    """Very small markdown parser -> [(kind, text)] where kind is h1..h3, bullet, number, table, para."""
    blocks = []
    table = []
    for raw in (markdown or "").splitlines():
        line = raw.rstrip()
        if line.strip().startswith("|") and line.strip().endswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not all(re.fullmatch(r":?-+:?", c) for c in cells if c):
                table.append(cells)
            continue
        if table:
            blocks.append(("table", table))
            table = []
        if not line.strip():
            blocks.append(("blank", ""))
            continue
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            blocks.append(("h%d" % min(len(m.group(1)), 3), m.group(2).strip()))
        elif re.match(r"^\s*[-*•]\s+", line):
            blocks.append(("bullet", re.sub(r"^\s*[-*•]\s+", "", line)))
        elif re.match(r"^\s*\d+[.)]\s+", line):
            blocks.append(("number", re.sub(r"^\s*\d+[.)]\s+", "", line)))
        else:
            blocks.append(("para", line.strip()))
    if table:
        blocks.append(("table", table))
    return blocks


def _strip_inline(text):
    return re.sub(r"\*\*(.+?)\*\*|\*(.+?)\*|`(.+?)`", lambda m: m.group(1) or m.group(2) or m.group(3), text)


def _add_runs(paragraph, text):
    for part in re.split(r"(\*\*.+?\*\*|\*.+?\*)", text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            paragraph.add_run(part[2:-2]).bold = True
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            paragraph.add_run(part[1:-1]).italic = True
        else:
            paragraph.add_run(part)


def make_docx(path, title, content):
    import docx
    d = docx.Document()
    if title:
        d.add_heading(title, 0)
    for kind, text in _blocks(content):
        if kind == "blank":
            continue
        if kind.startswith("h"):
            d.add_heading(_strip_inline(text), int(kind[1]))
        elif kind == "bullet":
            _add_runs(d.add_paragraph(style="List Bullet"), text)
        elif kind == "number":
            _add_runs(d.add_paragraph(style="List Number"), text)
        elif kind == "table":
            rows = text
            width = max(len(r) for r in rows)
            table = d.add_table(rows=len(rows), cols=width)
            table.style = "Light Grid Accent 1"
            for ri, row in enumerate(rows):
                for ci in range(width):
                    table.cell(ri, ci).text = _strip_inline(row[ci]) if ci < len(row) else ""
        else:
            _add_runs(d.add_paragraph(), text)
    d.save(path)


def _pdf_font(pdf):
    """Use a Unicode TTF when available so umlauts, accents and symbols render."""
    candidates = [
        r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/Library/Fonts/Arial Unicode.ttf",
    ]
    bold = {
        r"C:\Windows\Fonts\segoeui.ttf": r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\arial.ttf": r"C:\Windows\Fonts\arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    }
    for font in candidates:
        if os.path.exists(font):
            pdf.add_font("Body", "", font)
            b = bold.get(font)
            pdf.add_font("Body", "B", b if b and os.path.exists(b) else font)
            return "Body", True
    return "Helvetica", False


def make_pdf(path, title, content):
    from fpdf import FPDF
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(True, margin=18)
    pdf.set_margins(20, 20, 20)
    pdf.add_page()
    family, unicode_ok = _pdf_font(pdf)

    def clean(text):
        text = _strip_inline(text)
        if not unicode_ok:
            text = text.encode("latin-1", "replace").decode("latin-1")
        return text

    width = pdf.w - pdf.l_margin - pdf.r_margin
    if title:
        pdf.set_font(family, "B", 20)
        pdf.multi_cell(width, 10, clean(title))
        pdf.ln(4)
    for kind, text in _blocks(content):
        if kind == "blank":
            pdf.ln(2)
        elif kind.startswith("h"):
            size = {"h1": 16, "h2": 13.5, "h3": 12}[kind]
            pdf.ln(3)
            pdf.set_font(family, "B", size)
            pdf.multi_cell(width, size * 0.55, clean(text))
            pdf.ln(1)
        elif kind in ("bullet", "number"):
            pdf.set_font(family, "", 11)
            pdf.multi_cell(width, 6, ("•  " if unicode_ok else "-  ") + clean(text))
        elif kind == "table":
            pdf.set_font(family, "", 9.5)
            cols = max(len(r) for r in text)
            with pdf.table(col_widths=[width / cols] * cols, text_align="LEFT") as table:
                for row in text:
                    r = table.row()
                    for ci in range(cols):
                        r.cell(clean(row[ci]) if ci < len(row) else "")
            pdf.ln(2)
        else:
            pdf.set_font(family, "", 11)
            pdf.multi_cell(width, 6, clean(text))
    pdf.output(path)


def make_xlsx(path, title, rows, sheets=None):
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    sheets = sheets or [{"name": title or "Sheet1", "rows": rows}]
    for sheet in sheets:
        ws = wb.create_sheet((sheet.get("name") or "Sheet")[:31])
        for row in sheet.get("rows") or []:
            ws.append([_cell(v) for v in (row if isinstance(row, (list, tuple)) else [row])])
        if ws.max_row >= 1:
            for cell in ws[1]:
                cell.font = Font(bold=True)
                cell.fill = PatternFill("solid", fgColor="E7EDE8")
        for column in ws.columns:
            longest = max((len(str(c.value)) for c in column if c.value is not None), default=8)
            ws.column_dimensions[column[0].column_letter].width = min(max(10, longest + 2), 60)
    wb.save(path)


def _cell(value):
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("="):
            return stripped
        try:
            return int(stripped) if re.fullmatch(r"-?\d+", stripped) else float(stripped) if re.fullmatch(r"-?\d*\.\d+", stripped) else value
        except ValueError:
            return value
    return value


def make_pptx(path, title, content):
    from pptx import Presentation
    from pptx.util import Pt
    prs = Presentation()
    slides = re.split(r"^#{1,2}\s+", content or "", flags=re.M)
    first = prs.slides.add_slide(prs.slide_layouts[0])
    first.shapes.title.text = title or "Presentation"
    if slides and slides[0].strip() and len(first.placeholders) > 1:
        first.placeholders[1].text = slides[0].strip()[:200]
    for chunk in slides[1:]:
        lines = chunk.strip().splitlines()
        if not lines:
            continue
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = _strip_inline(lines[0])
        body = slide.placeholders[1].text_frame
        body.clear()
        first_para = True
        for line in lines[1:]:
            text = re.sub(r"^\s*([-*•]|\d+[.)])\s+", "", line).strip()
            if not text:
                continue
            para = body.paragraphs[0] if first_para else body.add_paragraph()
            first_para = False
            para.text = _strip_inline(text)
            para.font.size = Pt(18)
    prs.save(path)


@tool(
    "create_document",
    "Create a polished document file. For pdf/docx/pptx pass Markdown content (headings #, bullets -, tables |a|b|, **bold**). "
    "For pptx each '## Heading' becomes a slide. For xlsx/csv pass rows (first row = header); xlsx also accepts sheets. "
    "Relative paths are saved in the workspace.",
    {
        "path": "string: Output file, e.g. 'report.pdf', 'notes.docx', 'budget.xlsx', 'deck.pptx'",
        "title?": "string: Document title",
        "content?": "string: Markdown body for pdf/docx/pptx/md",
        "rows?": {"type": "array", "description": "Table rows for xlsx/csv, e.g. [[\"Name\",\"Qty\"],[\"Apples\",3]]", "items": {"type": "array", "items": {}}},
        "sheets?": {"type": "array", "description": "xlsx only: [{name, rows}]", "items": {"type": "object"}},
    },
    group="docs",
    risk="write",
)
def create_document(ctx, path, title="", content="", rows=None, sheets=None):
    path = resolve_path(path)
    ext = os.path.splitext(path)[1].lower()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if ext == ".docx":
        if not module_available("docx"):
            raise ToolError("Needs: pip install python-docx")
        make_docx(path, title, content)
    elif ext == ".pdf":
        if not module_available("fpdf"):
            raise ToolError("Needs: pip install fpdf2")
        make_pdf(path, title, content)
    elif ext in (".xlsx", ".xlsm"):
        if not module_available("openpyxl"):
            raise ToolError("Needs: pip install openpyxl")
        make_xlsx(path, title, rows or [], sheets)
    elif ext == ".pptx":
        if not module_available("pptx"):
            raise ToolError("Needs: pip install python-pptx")
        make_pptx(path, title, content)
    elif ext == ".csv":
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            csv.writer(fh).writerows(rows or [])
    else:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(("# %s\n\n" % title if title else "") + (content or ""))
    return Result("Created %s" % path, display=file_card(path, title), files=[path])


@tool(
    "edit_document",
    "Edit an existing Word or Excel file. Word: replace text (keeps formatting) or append Markdown paragraphs. "
    "Excel: set cells like {'B2': 5, 'C3': '=SUM(A1:A9)'} or append rows; optional sheet name.",
    {
        "path": "string: .docx or .xlsx file",
        "replace?": {"type": "object", "description": "Word: {old text: new text}"},
        "append?": "string: Word: Markdown to append at the end",
        "cells?": {"type": "object", "description": "Excel: {cell: value}"},
        "rows?": {"type": "array", "description": "Excel: rows to append", "items": {"type": "array", "items": {}}},
        "sheet?": "string: Excel sheet name",
        "save_as?": "string: Save to a new path instead of overwriting",
    },
    group="docs",
    risk="write",
)
def edit_document(ctx, path, replace=None, append="", cells=None, rows=None, sheet="", save_as=""):
    src = resolve_path(path, must_exist=True)
    dst = resolve_path(save_as) if save_as else src
    ext = os.path.splitext(src)[1].lower()
    changes = 0
    if ext == ".docx":
        import docx
        d = docx.Document(src)
        paragraphs = list(d.paragraphs)
        for table in d.tables:
            for row in table.rows:
                for cell in row.cells:
                    paragraphs.extend(cell.paragraphs)
        for old, new in (replace or {}).items():
            for para in paragraphs:
                if old in para.text:
                    done = False
                    for run in para.runs:
                        if old in run.text:
                            run.text = run.text.replace(old, str(new))
                            done = True
                    if not done:  # text spans runs: rewrite paragraph in its first run
                        full = para.text.replace(old, str(new))
                        for i, run in enumerate(para.runs):
                            run.text = full if i == 0 else ""
                    changes += 1
        if append:
            for kind, text in _blocks(append):
                if kind == "blank":
                    continue
                if kind.startswith("h"):
                    d.add_heading(_strip_inline(text), int(kind[1]))
                elif kind == "bullet":
                    _add_runs(d.add_paragraph(style="List Bullet"), text)
                else:
                    _add_runs(d.add_paragraph(), text if kind != "table" else " | ".join(" ".join(r) for r in text))
                changes += 1
        d.save(dst)
    elif ext in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(src)
        ws = wb[sheet] if sheet and sheet in wb.sheetnames else (wb.create_sheet(sheet) if sheet else wb.active)
        for ref, value in (cells or {}).items():
            ws[ref] = _cell(value)
            changes += 1
        for row in rows or []:
            ws.append([_cell(v) for v in row])
            changes += 1
        wb.save(dst)
    else:
        raise ToolError("edit_document supports .docx and .xlsx. For text files use edit_file; for PDFs use pdf_tools.")
    return Result("Applied %d change(s); saved %s" % (changes, dst), display=file_card(dst), files=[dst])


@tool(
    "pdf_tools",
    "Work with PDF files: merge several PDFs, extract a page range, rotate pages, add a text watermark, or render a page to an image.",
    {
        "action": "string[merge,extract,rotate,watermark,page_image]: Operation",
        "paths": "array<string>: Input PDF(s); merge uses all, others use the first",
        "output?": "string: Output path (default derived from input)",
        "pages?": "string: Page range like '1-3,5' (1-based)",
        "degrees?": "integer: Rotation (90, 180, 270)",
        "text?": "string: Watermark text",
    },
    group="docs",
    risk="write",
    requires=("pypdf",),
)
def pdf_tools(ctx, action, paths, output="", pages="", degrees=90, text=""):
    from pypdf import PdfReader, PdfWriter
    inputs = [resolve_path(p, must_exist=True) for p in paths or []]
    if not inputs:
        raise ToolError("Give at least one PDF path.")
    stem = os.path.splitext(inputs[0])[0]
    out = resolve_path(output) if output else "%s-%s.pdf" % (stem, action)

    def page_set(count):
        if not pages:
            return list(range(count))
        chosen = []
        for part in str(pages).split(","):
            part = part.strip()
            if "-" in part:
                a, b = part.split("-", 1)
                chosen.extend(range(int(a) - 1, min(int(b), count)))
            elif part:
                chosen.append(int(part) - 1)
        return [i for i in chosen if 0 <= i < count]

    if action == "page_image":
        if not module_available("pypdfium2"):
            raise ToolError("Needs: pip install pypdfium2")
        import base64
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(inputs[0])
        index = page_set(len(doc))[0] if pages else 0
        image = doc[index].render(scale=1.6).to_pil()
        out = out if out.endswith(".png") else "%s-page%d.png" % (stem, index + 1)
        image.save(out)
        buf = io.BytesIO()
        image.convert("RGB").save(buf, format="JPEG", quality=80)
        return Result("Rendered page %d to %s" % (index + 1, out), display={"type": "image", "path": out}, images=[base64.b64encode(buf.getvalue()).decode()], files=[out])

    writer = PdfWriter()
    if action == "merge":
        for p in inputs:
            for page in PdfReader(p).pages:
                writer.add_page(page)
    else:
        reader = PdfReader(inputs[0])
        indices = page_set(len(reader.pages))
        if action == "extract":
            for i in indices:
                writer.add_page(reader.pages[i])
        elif action == "rotate":
            for i, page in enumerate(reader.pages):
                if i in indices:
                    page.rotate(int(degrees or 90))
                writer.add_page(page)
        elif action == "watermark":
            if not module_available("fpdf"):
                raise ToolError("Watermarks need: pip install fpdf2")
            from fpdf import FPDF
            for i, page in enumerate(reader.pages):
                if i in indices:
                    w, h = float(page.mediabox.width), float(page.mediabox.height)
                    stamp = FPDF(unit="pt", format=(w, h))
                    stamp.add_page()
                    stamp.set_font("Helvetica", "B", max(24, int(min(w, h) / 9)))
                    stamp.set_text_color(170, 170, 170)
                    with stamp.rotation(35, x=w / 2, y=h / 2):
                        stamp.text(w * 0.18, h / 2, (text or "DRAFT").encode("latin-1", "replace").decode("latin-1"))
                    page.merge_page(PdfReader(io.BytesIO(bytes(stamp.output()))).pages[0])
                writer.add_page(page)
        else:
            raise ToolError("Unknown action")
    with open(out, "wb") as fh:
        writer.write(fh)
    return Result("%s done: %s" % (action, out), display=file_card(out), files=[out])


@tool(
    "compare_documents",
    "Compare two documents (contracts, versions, drafts) and return the exact differences plus similarity. "
    "Then explain the meaningful changes.",
    {"path_a": "string: Original document", "path_b": "string: Other document"},
    group="docs",
)
def compare_documents(ctx, path_a, path_b):
    a = extract_text(resolve_path(path_a, must_exist=True))
    b = extract_text(resolve_path(path_b, must_exist=True))
    split = lambda t: [s.strip() for s in re.split(r"(?<=[.!?;:])\s+|\n+", t) if s.strip()]
    sa, sb = split(a), split(b)
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, sa, sb).get_opcodes():
        if tag == "equal":
            continue
        if tag in ("replace", "delete"):
            out.extend("- " + s for s in sa[i1:i2])
        if tag in ("replace", "insert"):
            out.extend("+ " + s for s in sb[j1:j2])
        out.append("")
    diff = "\n".join(out)
    if len(diff) > 40000:
        diff = diff[:40000] + "\n... [truncated]"
    return Result(
        "Similarity: %.1f%%\nA: %s (%d chars)\nB: %s (%d chars)\n\nChanges (- only in A, + only in B):\n%s"
        % (ratio * 100, path_a, len(a), path_b, len(b), diff or "(no differences)")
    )
