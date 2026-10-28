"""Fill in what one source leaves out by following the job to other public sources.

LinkedIn's logged-out pages give the title, company and often a link to the employer's own
application page, but descriptions can be cut short and Easy Apply jobs need a LinkedIn login.
For those jobs this module:
  1. reads the employer's application page (Greenhouse and Lever via their public job APIs,
     any other careers site via its schema.org JobPosting data, which most ATSs publish), and
  2. looks the same job up on JobsDB (title + company), which often has the full description
     and a way to apply without LinkedIn.
No logins, no evasion: if a site refuses, the job just keeps what it has.
"""

import html as htmllib
import json
import re
import sys

from bs4 import BeautifulSoup

from . import storage
from .http import get
from .sources import jobsdb, linkedin

_GREENHOUSE = re.compile(r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)/jobs/(\d+)")
_LEVER = re.compile(r"jobs\.lever\.co/([\w.-]+)/([0-9a-f-]{36})")
MIN_GOOD_DESCRIPTION = 400   # shorter than this counts as "cut short"


def _text(html: str) -> str:
    return BeautifulSoup(htmllib.unescape(html or ""), "html.parser").get_text("\n", strip=True)


def parse_jsonld(page: str) -> dict:
    """Pull a schema.org JobPosting out of a careers page, if it has one."""
    soup = BeautifulSoup(page, "html.parser")
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for item in items:
            kind = item.get("@type") if isinstance(item, dict) else None
            if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
                return {"description": _text(item.get("description", "")),
                        "posted": (item.get("datePosted") or "")[:10]}
    return {}


def from_employer_page(url: str) -> dict:
    """Description from the employer's application page, via the cleanest route available."""
    if m := _GREENHOUSE.search(url):
        data = get(f"https://boards-api.greenhouse.io/v1/boards/{m[1]}/jobs/{m[2]}").json()
        return {"description": _text(data.get("content", ""))}
    if m := _LEVER.search(url):
        data = get(f"https://api.lever.co/v0/postings/{m[1]}/{m[2]}").json()
        parts = [data.get("descriptionPlain", "")]
        for section in data.get("lists", []):
            parts += [section.get("text", ""), _text(section.get("content", ""))]
        parts.append(data.get("additionalPlain", ""))
        return {"description": "\n".join(p for p in parts if p)}
    return parse_jsonld(get(url).text)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower())) - {"the", "and", "of", "hk", "limited", "ltd", "co"}


def same_job(title: str, company: str, other_title: str, other_company: str) -> bool:
    """Loose match: same company (by name tokens) and mostly the same title words."""
    c1, c2 = _tokens(company), _tokens(other_company)
    if not c1 or not c2 or len(c1 & c2) / min(len(c1), len(c2)) < 0.6:
        return False
    t1, t2 = _tokens(title), _tokens(other_title)
    return bool(t1 and t2) and len(t1 & t2) / len(t1 | t2) >= 0.6


def find_on_jobsdb(title: str, company: str) -> tuple[str, dict] | None:
    for job in jobsdb.search(f"{title} {company}", "Hong Kong", days=31, max_pages=1):
        if same_job(title, company, job.title, job.company):
            return job.url, jobsdb.fetch_details(job.job_id, job.snippet)
    return None


def find_on_linkedin(title: str, company: str) -> tuple[str, dict] | None:
    for job in linkedin.search(f"{title} {company}", "Hong Kong", days=31, max_pages=1):
        if same_job(title, company, job.title, job.company):
            return job.url, linkedin.fetch_details(job.job_id)
    return None


def _employer_url(r) -> str:
    url = r["apply_url"] or ""
    return url if r["apply_method"] == "external" and "linkedin.com" not in url else ""


def needs_enrichment(r) -> bool:
    short = len(r["description"] or "") < MIN_GOOD_DESCRIPTION
    easy_apply = r["apply_method"] == "linkedin_easy_apply"
    return short or easy_apply or bool(_employer_url(r))


def enrich(conn, limit: int = 40) -> int:
    rows = [r for r in conn.execute(
        "SELECT * FROM jobs WHERE details_fetched_at IS NOT NULL AND (alt_urls IS NULL) "
        f"AND status NOT IN ({','.join('?' * len(storage.DONE))}) ORDER BY score DESC",
        storage.DONE).fetchall() if needs_enrichment(r)][:limit]
    improved = 0
    for r in rows:
        best = r["description"] or ""
        alt_urls, updates = [], {}
        if employer := _employer_url(r):
            try:
                found = from_employer_page(employer)
                if len(found.get("description", "")) > len(best):
                    best = found["description"]
                    alt_urls.append(employer)
            except Exception as exc:
                print(f"[enrich] employer page for {r['title']!r}: {exc}", file=sys.stderr)
        if r["source"] == "jobsdb" and len(best) < MIN_GOOD_DESCRIPTION:
            # JobsDB gives only a summary; the same job on LinkedIn usually has the full text.
            try:
                hit = find_on_linkedin(r["title"], r["company"])
                if hit:
                    url, details = hit
                    alt_urls.append(url)
                    if len(details.get("description", "")) > len(best):
                        best = details["description"]
                    if details.get("applicants") is not None:
                        updates["applicants"] = details["applicants"]
                    if details.get("seniority"):
                        updates["seniority"] = details["seniority"]
            except Exception as exc:
                print(f"[enrich] LinkedIn lookup for {r['title']!r}: {exc}", file=sys.stderr)
        if len(best) < MIN_GOOD_DESCRIPTION or r["apply_method"] == "linkedin_easy_apply":
            if r["source"] != "jobsdb":
                try:
                    hit = find_on_jobsdb(r["title"], r["company"])
                    if hit:
                        url, details = hit
                        alt_urls.append(url)
                        if len(details.get("description", "")) > len(best):
                            best = details["description"]
                        if r["apply_method"] == "linkedin_easy_apply":
                            # Same job, applicable without a LinkedIn login.
                            updates.update(apply_url=details["apply_url"], apply_method="jobsdb")
                except Exception as exc:
                    print(f"[enrich] JobsDB lookup for {r['title']!r}: {exc}", file=sys.stderr)
        if best != (r["description"] or ""):
            updates["description"] = best
            improved += 1
        updates["alt_urls"] = json.dumps(alt_urls)
        cols = ", ".join(f"{k} = :{k}" for k in updates)
        with conn:
            conn.execute(f"UPDATE jobs SET {cols} WHERE id = :id", {**updates, "id": r["id"]})
    print(f"[enrich] checked {len(rows)} jobs elsewhere, improved {improved}", file=sys.stderr)
    return improved

