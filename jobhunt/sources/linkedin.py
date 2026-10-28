"""LinkedIn public (logged-out) job search. No login or cookies are used."""

from collections.abc import Iterator

from bs4 import BeautifulSoup

from ..http import get
from ..models import Job

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
PAGE_SIZE = 25


def search(query: str, location: str, days: int, max_pages: int) -> Iterator[Job]:
    for page in range(max_pages):
        params = {
            "keywords": query,
            "location": location,
            "f_TPR": f"r{days * 86400}",
            "start": page * PAGE_SIZE,
        }
        jobs = parse_html(get(SEARCH_URL, params=params).text, query)
        yield from jobs
        if len(jobs) < PAGE_SIZE:
            break


def parse_html(html: str, query: str = "") -> list[Job]:
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    for card in soup.select("div.base-card"):
        title = card.select_one(".base-search-card__title")
        if not title:
            continue
        company = card.select_one(".base-search-card__subtitle")
        location = card.select_one(".job-search-card__location")
        link = card.select_one("a.base-card__full-link")
        posted = card.select_one("time")
        salary = card.select_one(".job-search-card__salary-info")
        urn = card.get("data-entity-urn", "")
        url = link["href"].split("?")[0] if link and link.get("href") else ""
        jobs.append(Job(
            source="linkedin",
            job_id=urn.rsplit(":", 1)[-1] if urn else url,
            title=title.get_text(strip=True),
            company=company.get_text(strip=True) if company else "",
            location=location.get_text(strip=True) if location else "",
            url=url,
            posted=posted.get("datetime", "") if posted else "",
            salary=salary.get_text(" ", strip=True) if salary else "",
            query=query,
        ))
    return jobs
