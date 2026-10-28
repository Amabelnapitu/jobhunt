"""LinkedIn public (logged-out) job search. No login or cookies are used."""

import re
from collections.abc import Iterator
from urllib.parse import parse_qs, unquote, urlparse

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


DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}"


def fetch_details(job_id: str) -> dict:
    return parse_detail(get(DETAIL_URL.format(id=job_id)).text)


def parse_detail(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    desc = soup.select_one(".show-more-less-html__markup") or soup.select_one(".description__text")
    details = {"description": desc.get_text("\n", strip=True) if desc else "",
               "apply_url": "", "apply_method": "linkedin_easy_apply", "applicants": None, "seniority": ""}

    caption = soup.select_one(".num-applicants__caption")
    if caption:
        text = caption.get_text(" ", strip=True).lower()
        digits = re.search(r"(\d[\d,]*)", text)
        if "first" in text:            # "Be among the first 25 applicants"
            details["applicants"] = 0
        elif digits:                   # "37 applicants", "Over 200 applicants"
            details["applicants"] = int(digits.group(1).replace(",", ""))

    # Off-site applications hide the employer's URL in a commented-out <code id="applyUrl">.
    code = soup.select_one("code#applyUrl")
    if code:
        found = re.search(r'"(https?://[^"]+)"', code.decode_contents())
        if found:
            url = found.group(1)
            target = parse_qs(urlparse(url).query).get("url")
            details["apply_url"] = unquote(target[0]) if target else url
            details["apply_method"] = "external"

    for item in soup.select(".description__job-criteria-item"):
        label = item.select_one(".description__job-criteria-subheader")
        value = item.select_one(".description__job-criteria-text")
        if label and value and "seniority" in label.get_text().lower():
            details["seniority"] = value.get_text(strip=True)
    return details
