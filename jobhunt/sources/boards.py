"""Employers' own job boards on Greenhouse, Lever and Ashby, read through their public job-board
APIs (published for exactly this). These jobs land in the Claude-applies lane: plain forms with
a file upload and no account.

BOARDS lists employers found to have Hong Kong openings; add any (ats, board slug) pair. Every
board is read once per run, whatever the query, and only Hong Kong jobs are kept; scoring and
eligibility then decide what is worth applying to.
"""

import html as htmllib
import re
import sys
from collections.abc import Iterator
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from ..http import get
from ..models import Job

BOARDS = [  # (ats, board slug, company name)
    ("greenhouse", "okx", "OKX"), ("greenhouse", "imc", "IMC Trading"), ("greenhouse", "bybit", "Bybit"),
    ("greenhouse", "towerresearchcapital", "Tower Research Capital"), ("greenhouse", "flowtraders", "Flow Traders"),
    ("greenhouse", "xendit", "Xendit"), ("greenhouse", "galaxydigitalservices", "Galaxy"),
    ("greenhouse", "mongodb", "MongoDB"), ("greenhouse", "drweng", "DRW"), ("greenhouse", "agoda", "Agoda"),
    ("greenhouse", "bitgo", "BitGo"),
    ("lever", "lalamove", "Lalamove"), ("lever", "crypto", "Crypto.com"), ("lever", "ambergroup", "Amber Group"),
    ("lever", "binance", "Binance"),
    ("ashby", "bjakcareer", "Bjak (Doit)"), ("ashby", "airwallex", "Airwallex"),
]
_COMPANY = {(a, s): c for a, s, c in BOARDS}
_HK = re.compile(r"\b(hong ?kong|hksar|kowloon|hk)\b", re.I)

# Descriptions come with the board listing; kept here so fetch_details needs no extra request.
_details: dict[str, dict] = {}
_read = False
_listings: dict[tuple[str, str], list[dict]] = {}


def _text(html: str) -> str:
    return BeautifulSoup(htmllib.unescape(html or ""), "html.parser").get_text("\n", strip=True)


def _date(value) -> str:
    if isinstance(value, (int, float)):           # Lever: milliseconds since 1970
        return datetime.fromtimestamp(value / 1000, timezone.utc).date().isoformat()
    return (value or "")[:10]


def _greenhouse(slug: str) -> Iterator[dict]:
    for j in get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", params={"content": "true"}).json().get("jobs", []):
        yield {"id": j["id"], "title": j["title"], "company": j.get("company_name") or slug,
               "location": (j.get("location") or {}).get("name", ""), "url": j.get("absolute_url", ""),
               "posted": _date(j.get("first_published") or j.get("updated_at")), "description": _text(j.get("content", ""))}


def _lever(slug: str) -> Iterator[dict]:
    for j in get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"}).json():
        parts = [j.get("descriptionPlain", "")]
        for section in j.get("lists", []):
            parts += [section.get("text", ""), _text(section.get("content", ""))]
        parts.append(j.get("additionalPlain", ""))
        cats = j.get("categories") or {}
        yield {"id": j["id"], "title": j["text"], "company": slug,
               "location": ", ".join(filter(None, [cats.get("location", ""), *(cats.get("allLocations") or [])])),
               "url": j.get("applyUrl") or j.get("hostedUrl", ""), "posted": _date(j.get("createdAt")),
               "description": "\n".join(p for p in parts if p)}


def _ashby(slug: str) -> Iterator[dict]:
    for j in get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}").json().get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        yield {"id": j["id"], "title": j["title"], "company": slug, "location": ", ".join(filter(None, locs)),
               "url": j.get("applyUrl") or j.get("jobUrl", ""), "posted": _date(j.get("publishedAt")),
               "description": j.get("descriptionPlain") or _text(j.get("descriptionHtml", ""))}


READERS = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby}


def board_jobs(ats: str, slug: str) -> list[dict]:
    """A board's open jobs, read at most once per run."""
    if (ats, slug) not in _listings:
        _listings[ats, slug] = list(READERS[ats](slug))
    return _listings[ats, slug]


def search(query: str, location: str, days: int, max_pages: int) -> Iterator[Job]:
    """All Hong Kong jobs on every board, once per run (later queries return nothing new)."""
    global _read
    if _read:
        return
    _read = True
    for ats, slug, _ in BOARDS:
        try:
            jobs = board_jobs(ats, slug)
        except Exception as exc:  # one board failing shouldn't stop the others
            print(f"[boards] {ats}:{slug}: FAILED ({exc})", file=sys.stderr)
            continue
        for j in jobs:
            if not _HK.search(j["location"] or ""):
                continue
            job_id = f"{ats}:{slug}:{j['id']}"
            _details[job_id] = {"description": j["description"], "apply_url": j["url"],
                                "apply_method": "external", "applicants": None, "seniority": ""}
            yield Job(source="boards", job_id=job_id, title=j["title"].strip(), company=_COMPANY.get((ats, slug), j["company"]),
                      location=j["location"], url=j["url"], posted=j["posted"],
                      snippet=j["description"][:300], query=query)


def fetch_details(job_id: str, snippet: str = "") -> dict:
    """Details from this run's board listing; for a job read on an earlier run, the board is
    read again. A job no longer on its board is marked closed."""
    if job_id in _details:
        return _details[job_id]
    ats, slug, ident = job_id.split(":", 2)
    for j in board_jobs(ats, slug):
        if str(j["id"]) == ident:
            _details[job_id] = {"description": j["description"], "apply_url": j["url"],
                                "apply_method": "external", "applicants": None, "seniority": ""}
            return _details[job_id]
    return {"description": snippet, "apply_url": "", "apply_method": "external", "applicants": None,
            "seniority": "", "closed": True}
