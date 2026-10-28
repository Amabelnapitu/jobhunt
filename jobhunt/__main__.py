"""Scrape Hong Kong job boards, rank results against profile.toml, and store them in SQLite.

    python -m jobhunt                          # scrape all sources (same as `scrape`)
    python -m jobhunt scrape --sources jobsdb  # one source
    python -m jobhunt list --status new        # show stored jobs
    python -m jobhunt mark 42 applied --notes "via referral"
    python -m jobhunt export                   # write output/jobs.csv for Excel
"""

import argparse
import sys
import tomllib
from pathlib import Path

from . import storage
from .models import Job
from .scoring import score_job
from .sources import SOURCES


def load_profile(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def scrape(args, conn) -> None:
    profile = load_profile(args.profile)
    search, scoring = profile["search"], profile.get("scoring", {})
    days = args.days or search.get("days", 14)
    max_pages = args.max_pages or search.get("max_pages", 3)

    run_id = storage.start_run(conn, args.sources)
    found: dict[tuple[str, str], Job] = {}
    queries: dict[tuple[str, str], set[str]] = {}
    for name in args.sources:
        for query in search["queries"]:
            try:
                count = 0
                for job in SOURCES[name](query, search.get("location", "Hong Kong"), days, max_pages):
                    found.setdefault(job.key, job)
                    queries.setdefault(job.key, set()).add(query)
                    count += 1
                print(f"[{name}] {query!r}: {count} results", file=sys.stderr)
            except Exception as exc:  # one broken source/query shouldn't kill the run
                print(f"[{name}] {query!r}: FAILED ({exc})", file=sys.stderr)

    jobs = [score_job(j, scoring) for j in found.values()]
    new = storage.save_jobs(conn, run_id, jobs, queries)
    # Re-score older jobs too, so edits to profile.toml apply to them.
    storage.rescore(conn, lambda j: score_job(j, scoring))
    exported = storage.export_csv(conn, args.out, args.min_score)

    print(f"\n{len(jobs)} jobs this run, {len(new)} new. Database: {args.db}, CSV: {args.out} ({exported} rows)\n")
    this_run = sorted((j for j in jobs if j.score >= args.min_score), key=lambda j: -j.score)
    for job in this_run[: args.top]:
        flag = "NEW " if job.key in new else "    "
        print(f"{flag}{job.score:>4}  {job.title[:60]:<60}  {job.company[:30]:<30}  {job.url}")


def list_jobs(args, conn) -> None:
    statuses = tuple(args.status) if args.status else storage.STATUSES
    for r in storage.top_jobs(conn, args.min_score, args.top, statuses):
        print(f"{r['id']:>5}  {r['score']:>4}  {r['status']:<12}  {r['title'][:55]:<55}  "
              f"{r['company'][:28]:<28}  {r['url']}")


def mark(args, conn) -> None:
    if not storage.set_status(conn, args.id, args.status, args.notes):
        sys.exit(f"No job with id {args.id}")
    print(f"Job {args.id} -> {args.status}")


def export(args, conn) -> None:
    n = storage.export_csv(conn, args.out, args.min_score)
    print(f"Wrote {n} jobs to {args.out}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobhunt", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", type=Path, default=Path("data/jobs.db"))
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("scrape", parents=[common], help="scrape job boards into the database (default)")
    p.add_argument("--profile", type=Path, default=Path("profile.toml"))
    p.add_argument("--out", type=Path, default=Path("output/jobs.csv"))
    p.add_argument("--sources", nargs="+", choices=sorted(SOURCES), default=sorted(SOURCES))
    p.add_argument("--days", type=int, help="override [search].days")
    p.add_argument("--max-pages", type=int, help="override [search].max_pages")
    p.add_argument("--min-score", type=int, default=0, help="hide jobs scoring below this")
    p.add_argument("--top", type=int, default=25, help="how many to print")
    p.set_defaults(func=scrape)

    p = sub.add_parser("list", parents=[common], help="show stored jobs, best first")
    p.add_argument("--status", nargs="+", choices=storage.STATUSES)
    p.add_argument("--min-score", type=int, default=0)
    p.add_argument("--top", type=int, default=50)
    p.set_defaults(func=list_jobs)

    p = sub.add_parser("mark", parents=[common], help="set a job's status (and optional notes)")
    p.add_argument("id", type=int)
    p.add_argument("status", choices=storage.STATUSES)
    p.add_argument("--notes")
    p.set_defaults(func=mark)

    p = sub.add_parser("export", parents=[common], help="write the database to CSV")
    p.add_argument("--out", type=Path, default=Path("output/jobs.csv"))
    p.add_argument("--min-score", type=int, default=0)
    p.set_defaults(func=export)

    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in sub.choices and argv[0] not in ("-h", "--help"):
        argv = ["scrape", *argv]
    args = parser.parse_args(argv)

    conn = storage.connect(args.db)
    try:
        args.func(args, conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
