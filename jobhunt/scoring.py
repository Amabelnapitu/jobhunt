import re

from .models import Job

_NON_WORD = re.compile(r"[^a-z0-9&+]+")


def _prep(text: str) -> str:
    """Lowercase, turn punctuation into spaces and pad, so phrases match on word boundaries."""
    return f" {_NON_WORD.sub(' ', text.lower()).strip()} "


def _has(haystack: str, phrase: str) -> bool:
    return f" {_prep(phrase).strip()} " in haystack


def categorize(title: str, categories: dict[str, list[str]]) -> str:
    """First category (in profile order) with a keyword in the title, else "other"."""
    prepped = _prep(title)
    for name, keywords in categories.items():
        if any(_has(prepped, kw) for kw in keywords):
            return name
    return "other"


def score_job(job: Job, scoring: dict, categories: dict[str, list[str]] | None = None) -> Job:
    title = _prep(job.title)
    body = _prep(f"{job.company} {job.snippet}")
    total, hits = 0, []

    for kw, weight in scoring.get("keywords", {}).items():
        if _has(title, kw):
            total += 2 * weight
            hits.append(kw)
        elif _has(body, kw):
            total += weight
            hits.append(kw)

    for kw, weight in scoring.get("penalties", {}).items():
        if _has(title, kw):
            total += weight
            hits.append(f"-{kw}")

    for kw, weight in scoring.get("level_bonus", {}).items():
        if _has(title, kw):
            total += weight

    job.category = categorize(job.title, categories or {})
    job.score = total + scoring.get("category_bonus", {}).get(job.category, 0)
    job.matched = ", ".join(hits)
    return job
