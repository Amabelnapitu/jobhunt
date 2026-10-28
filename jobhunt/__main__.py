"""Scrape Hong Kong job boards, rank results against profile.toml, and save to CSV.

    python -m jobhunt                      # all sources, settings from profile.toml
    python -m jobhunt --sources jobsdb     # one source
    python -m jobhunt --min-score 10       # hide weak matches in the CSV
"""

import argparse
import sys
import tomllib
from pathlib import Path

from . import storage
from .models import Job
from .scoring import score_job
from .sources import SOURCES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobhunt", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", type=Path, default=Path("profile.toml"))
    parser.add_argument("--out", type=Path, default=Path("output/jobs.csv"))
    parser.add_argument("--sources", nargs="+", choices=sorted(SOURCES), default=sorted(SOURCES))
    parser.add_argument("--days", type=int, help="override [search].days")
    parser.add_argument("--max-pages", type=int, help="override [search].max_pages")
    parser.add_argument("--min-score", type=int, default=0, help="drop jobs scoring below this")
    parser.add_argument("--top", type=int, default=25, help="how many to print")
    args = parser.parse_args(argv)

    profile = tomllib.loads(args.profile.read_text(encoding="utf-8"))
    search = profile["search"]
    days = args.days or search.get("days", 14)
    max_pages = args.max_pages or search.get("max_pages", 3)

    found: dict[tuple[str, str], Job] = {}
    for name in args.sources:
        for query in search["queries"]:
            try:
                count = 0
                for job in SOURCES[name](query, search.get("location", "Hong Kong"), days, max_pages):
                    found.setdefault(job.key, job)
                    count += 1
                print(f"[{name}] {query!r}: {count} results", file=sys.stderr)
            except Exception as exc:  # one broken source/query shouldn't kill the run
                print(f"[{name}] {query!r}: FAILED ({exc})", file=sys.stderr)

    all_jobs, new = storage.merge(storage.load(args.out), list(found.values()))
    # Re-score everything so edits to profile.toml apply to earlier results too.
    all_jobs = [score_job(j, profile.get("scoring", {})) for j in all_jobs]
    all_jobs = [j for j in all_jobs if j.score >= args.min_score]
    storage.save(args.out, all_jobs)

    new_keys = {j.key for j in new}
    this_run = [j for j in all_jobs if j.key in found]
    print(f"\n{len(found)} jobs this run, {len(new)} new. Saved {len(all_jobs)} to {args.out}\n")
    for job in sorted(this_run, key=lambda j: -j.score)[: args.top]:
        flag = "NEW " if job.key in new_keys else "    "
        print(f"{flag}{job.score:>4}  {job.title[:60]:<60}  {job.company[:30]:<30}  {job.url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
