"""Structured CVs and the guard rails for tailoring them.

A CV is JSON:
    {"variant": "PM",
     "header": {"name": "...", "lines": ["phone | email", "linkedin"]},
     "sections": [
        {"title": "SUMMARY", "text": "..."},
        {"title": "WORK EXPERIENCE", "entries": [
            {"org": "...", "location": "...", "role": "...", "dates": "...", "blurb": "...",
             "bullets": ["...", {"text": "...", "change": 0}]}]},
        {"title": "LANGUAGES, SKILLS & INTERESTS", "lines": [{"label": "Languages", "text": "..."}]}]}

A tailored CV uses the same shape plus "changes" (what changed, why, and where the facts come
from). Bullets and the summary may be {"text", "change"} objects pointing at a change, so the
dashboard can highlight them.

check() enforces the rules for a tailored CV against its base:
  - header unchanged; no new section titles
  - no more words and no more bullets than the base (the base fills one page)
  - every number and every capitalised term must appear in the source material (your CVs and
    what you've told Claude). Anything that doesn't is reported as unsourced.
Personal data never lives in this repo; CVs and sources are passed in at run time.
"""

import json
import re
from pathlib import Path

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’.\-/&+]*")
_NUMBER = re.compile(r"~?\d[\d,.]*\+?%?")
# A capitalised word or acronym (IFRS, RAFM, Jira, Power BI's "Power", "SOA").
_TERM = re.compile(r"\b[A-Z][A-Za-z0-9&+\-]*[A-Za-z0-9]\b|\b[A-Z]\b")
# Heading and date words that are capitalised by convention and say nothing factual.
_COMMON = {
    "summary", "education", "experience", "projects", "work", "skills", "languages", "interests",
    "leadership", "technical", "domain", "analytical", "consulting", "relevant", "coursework",
    "personal", "project", "present", "github",
    "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
}
_SENTENCE_START = re.compile(r"(^|[.;:!?]\s+|\s[-–—]\s+)([A-Z][a-z]+)")


def _text_of(item) -> str:
    return item["text"] if isinstance(item, dict) else item


def texts(cv: dict, titles: bool = True) -> list[str]:
    """Every piece of visible text, in page order (header included)."""
    out = [cv.get("header", {}).get("name", ""), *cv.get("header", {}).get("lines", [])]
    for s in cv.get("sections", []):
        if titles:
            out.append(s["title"])
        if "text" in s:
            out.append(_text_of(s["text"]))
        for e in s.get("entries", []):
            out += [_text_of(e.get("org", "")), e.get("location", ""), _text_of(e.get("role", "")),
                    e.get("dates", ""), _text_of(e.get("blurb", ""))]
            out += [_text_of(b) for b in e.get("bullets", [])]
        for line in s.get("lines", []):
            out += [line.get("label", ""), _text_of(line.get("text", ""))]
    return [t for t in out if t]


def word_count(cv: dict) -> int:
    return sum(len(_WORD.findall(t)) for t in texts(cv))


def bullet_count(cv: dict) -> int:
    return sum(len(e.get("bullets", [])) for s in cv.get("sections", []) for e in s.get("entries", []))


def _norm(text: str) -> str:
    text = text.lower().replace("‑", "-").replace("‐", "-").replace(" ", " ")
    text = text.replace("‑", "-").replace("’", "'").replace("\\~", "~").replace("\\", "")
    return re.sub(r"\s+", " ", text)


def unsourced(cv: dict, corpus: str) -> list[str]:
    """Numbers and capitalised terms in the CV that the source material never mentions."""
    src = _norm(corpus)
    src_numbers = {n.strip("~+%,.") for n in _NUMBER.findall(src)}
    missing = []
    for t in texts(cv, titles=False):  # section titles have their own rule in check()
        for n in _NUMBER.findall(t):
            core = n.strip("~+%,.")
            if core and core not in src_numbers:
                missing.append(n)
        # Sentence-initial words ("Streamlined ...") are capitalised by position, not as names.
        t = _SENTENCE_START.sub(lambda m: m.group(1) + m.group(2).lower(), t)
        for term in _TERM.findall(t):
            low = term.lower()
            if low in _COMMON or len(low) < 2:
                continue
            if not re.search(r"(?<![a-z0-9])" + re.escape(_norm(term)) + r"(?![a-z0-9])", src):
                missing.append(term)
    return sorted(set(missing))


def check(tailored: dict, base: dict, corpus: str, droppable: tuple[str, ...] = ()) -> dict:
    base_titles = [s["title"] for s in base["sections"]]
    titles = [s["title"] for s in tailored["sections"]]
    problems = []
    if tailored.get("header") != base.get("header"):
        problems.append("Header differs from the base CV")
    extra = [t for t in titles if t not in base_titles]
    if extra:
        problems.append(f"New sections not in the base CV: {', '.join(extra)}")
    dropped = [t for t in base_titles if t not in titles and t not in droppable]
    if dropped:
        problems.append(f"Sections dropped that aren't allowed to be: {', '.join(dropped)}")
    words, base_words = word_count(tailored), word_count(base)
    bullets, base_bullets = bullet_count(tailored), bullet_count(base)
    if words > base_words:
        problems.append(f"{words} words, more than the base CV's {base_words} (it fills the page)")
    if bullets > base_bullets:
        problems.append(f"{bullets} bullets, more than the base CV's {base_bullets}")
    missing = unsourced(tailored, corpus)
    if missing:
        problems.append("Not found in your CVs or notes: " + ", ".join(missing))
    return {"words": words, "base_words": base_words, "bullets": bullets, "base_bullets": base_bullets,
            "unsourced": missing, "problems": problems, "ok": not problems}


def load_corpus(source_dir: Path) -> str:
    parts = []
    for path in sorted(source_dir.rglob("*")):
        if path.suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            parts += texts(data) if "sections" in data else [json.dumps(data, ensure_ascii=False)]
        elif path.suffix in (".txt", ".md"):
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)
