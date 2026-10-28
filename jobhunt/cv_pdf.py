"""Render a structured CV to an A4 PDF and verify it is exactly one page.

The layout copies the original Google Doc: Times New Roman (Tinos, its metric twin, when
Times isn't installed) 10pt, a 13pt bold name, bold section titles. Chromium (via the
pre-installed Playwright) prints the PDF; pypdf counts the pages.
"""

import html
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from pypdf import PdfReader

CSS = """
@page { size: A4; margin: 0 }
body { margin: 0 }
.paper { width: 210mm; box-sizing: border-box; padding: 11mm 12.7mm; color: #111;
  font: 10pt/1.16 "Times New Roman", Tinos, Times, serif }
.nm { text-align: center; font-weight: 700; font-size: 13pt; margin: 0 }
.hl { text-align: center; margin: 0 }
.st { font-weight: 700; margin: 9pt 0 2pt; text-transform: uppercase }
.er { display: flex; justify-content: space-between; gap: 8pt }
.er b, .er i { white-space: nowrap }
.bl { font-style: italic; margin: 0 }
ul { margin: 1pt 0 3pt; padding-left: 16pt }
li { margin: 0 }
p { margin: 0 }
"""
FONTS = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Tinos:ital,wght@0,400;0,700;1,400'
         '&display=swap">')

_JS = r"""
const { chromium } = require('playwright');
(async () => {
  const [src, out] = process.argv.slice(2);
  const b = await chromium.launch();
  const p = await b.newPage();
  await p.goto('file://' + src, { waitUntil: 'networkidle' });
  await p.evaluate(() => document.fonts.ready);
  await p.pdf({ path: out, format: 'A4', printBackground: true, margin: { top: 0, right: 0, bottom: 0, left: 0 } });
  await b.close();
})().catch(e => { console.error(e); process.exit(1); });
"""


def _t(x) -> str:
    return html.escape(x["text"] if isinstance(x, dict) else (x or ""))


def to_html(cv: dict) -> str:
    h = cv.get("header", {})
    out = [f'<p class="nm">{_t(h.get("name"))}</p>'] + [f'<p class="hl">{_t(l)}</p>' for l in h.get("lines", [])]
    for s in cv.get("sections", []):
        out.append(f'<p class="st">{_t(s["title"])}</p>')
        if "text" in s:
            out.append(f"<p>{_t(s['text'])}</p>")
        for e in s.get("entries", []):
            out.append(f'<div class="er"><b>{_t(e.get("org"))}</b><b>{_t(e.get("location"))}</b></div>')
            if e.get("role") or e.get("dates"):
                out.append(f'<div class="er"><b>{_t(e.get("role"))}</b><i>{_t(e.get("dates"))}</i></div>')
            if _t(e.get("blurb")):
                out.append(f'<p class="bl">{_t(e.get("blurb"))}</p>')
            if e.get("bullets"):
                out.append("<ul>" + "".join(f"<li>{_t(b)}</li>" for b in e["bullets"]) + "</ul>")
        for line in s.get("lines", []):
            out.append(f"<p><b>{_t(line.get('label'))}:</b> {_t(line.get('text'))}</p>")
    return (f"<!doctype html><html><head><meta charset='utf-8'>{FONTS}<style>{CSS}</style></head>"
            f"<body><div class='paper'>{''.join(out)}</div></body></html>")


def filename(cv: dict, company: str, title: str = "") -> str:
    """e.g. "Jane_Doe_CV_Acme_Product_Owner.pdf", from the CV's own header and the job."""
    name = cv.get("header", {}).get("name", "CV")
    if "," in name:  # "SURNAME, Given Names" -> "Given Names Surname"
        last, first = [p.strip() for p in name.split(",", 1)]
        name = f"{first} {last.title()}"
    slug = lambda s: re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")
    job = "_".join(filter(None, [slug(company)[:30], slug(title)[:30]]))
    return f"{slug(name)}_CV_{job}.pdf"


def render(cv: dict, out: Path) -> int:
    """Write the PDF and return its page count."""
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node is needed to print PDFs (Playwright's Chromium)")
    with tempfile.TemporaryDirectory() as tmp:
        src, script = Path(tmp) / "cv.html", Path(tmp) / "print.js"
        src.write_text(to_html(cv), encoding="utf-8")
        script.write_text(_JS, encoding="utf-8")
        subprocess.run([node, str(script), str(src), str(out)], check=True, capture_output=True, text=True)
    return len(PdfReader(str(out)).pages)


def render_tailored(path: Path, out_dir: Path) -> dict:
    """Render a tailored-CV document; record the result under "pdf" in the document."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / filename(doc["cv"], doc.get("company", "job"), doc.get("job_title", ""))
    pages = render(doc["cv"], out)
    doc["pdf"] = {**doc.get("pdf", {}), "filename": out.name, "pages": pages, "path": str(out.resolve())}
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return doc["pdf"]
