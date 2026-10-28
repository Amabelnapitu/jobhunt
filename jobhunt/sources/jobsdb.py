"""JobsDB Hong Kong (SEEK platform). Uses the JSON search endpoint the website itself calls."""

from collections.abc import Iterator

from bs4 import BeautifulSoup

from ..http import get
from ..models import Job

SEARCH_URL = "https://hk.jobsdb.com/api/jobsearch/v5/search"
JOB_URL = "https://hk.jobsdb.com/job/{id}"
PAGE_SIZE = 100
# SEEK only accepts these date ranges (days).
_DATE_RANGES = (1, 3, 7, 14, 31)


def search(query: str, location: str, days: int, max_pages: int) -> Iterator[Job]:
    daterange = next((d for d in _DATE_RANGES if d >= days), 31)
    for page in range(1, max_pages + 1):
        params = {
            "siteKey": "HK-Main",
            "sourcesystem": "houston",
            "locale": "en-HK",
            "keywords": query,
            "where": "All Hong Kong SAR" if location.lower() == "hong kong" else location,
            "daterange": daterange,
            "page": page,
            "pageSize": PAGE_SIZE,
        }
        data = get(SEARCH_URL, params=params).json()
        items = data.get("data") or []
        for item in items:
            yield parse_item(item, query)
        if len(items) < PAGE_SIZE:
            break


def parse_item(item: dict, query: str = "") -> Job:
    job_id = str(item.get("id", ""))
    company = item.get("companyName") or (item.get("advertiser") or {}).get("description", "")
    locations = item.get("locations") or []
    if locations:
        location = ", ".join(loc.get("label", "") for loc in locations if loc.get("label"))
    else:
        location = item.get("location", "")
    snippet = " ".join(filter(None, [item.get("teaser", ""), *(item.get("bulletPoints") or [])]))
    return Job(
        source="jobsdb",
        job_id=job_id,
        title=(item.get("title") or "").strip(),
        company=company.strip(),
        location=location,
        url=JOB_URL.format(id=job_id),
        posted=(item.get("listingDate") or "")[:10],
        salary=item.get("salaryLabel") or item.get("salary") or "",
        snippet=snippet,
        query=query,
    )


APPLY_URL = "https://hk.jobsdb.com/job/{id}/apply"


def fetch_details(job_id: str) -> dict:
    return parse_detail(get(JOB_URL.format(id=job_id)).text, job_id)


def parse_detail(html: str, job_id: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    desc = soup.select_one('[data-automation="jobAdDetails"]')
    return {
        "description": desc.get_text("\n", strip=True) if desc else "",
        # JobsDB routes every application (Quick Apply or the employer's own form) through this page.
        "apply_url": APPLY_URL.format(id=job_id),
        "apply_method": "jobsdb",
        "applicants": None,
        "seniority": "",
    }
