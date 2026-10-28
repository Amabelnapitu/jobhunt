import csv
from datetime import date
from pathlib import Path

from .models import Job


def load(path: Path) -> dict[tuple[str, str], Job]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = csv.DictReader(f)
        jobs = [Job(**{k: v for k, v in row.items() if k in Job.columns()}) for row in rows]
    for job in jobs:
        job.score = int(job.score or 0)
    return {job.key: job for job in jobs}


def merge(previous: dict[tuple[str, str], Job], found: list[Job]) -> tuple[list[Job], list[Job]]:
    """Combine this run's jobs with earlier ones. Returns (all jobs, jobs new this run)."""
    today = date.today().isoformat()
    merged = dict(previous)
    new = []
    for job in found:
        old = merged.get(job.key)
        if old:
            job.first_seen = old.first_seen
        else:
            job.first_seen = today
            new.append(job)
        merged[job.key] = job
    return list(merged.values()), new


def save(path: Path, jobs: list[Job]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Best match first; newest first among equal scores.
    jobs = sorted(sorted(jobs, key=lambda j: j.posted, reverse=True), key=lambda j: -j.score)
    # utf-8-sig so Excel shows Chinese company names correctly.
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=Job.columns())
        writer.writeheader()
        for job in jobs:
            writer.writerow(vars(job))
