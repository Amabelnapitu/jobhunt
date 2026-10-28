"""Scrape Hong Kong job boards, rank results against profile.toml, and store them in SQLite.

    python -m jobhunt                          # scrape, open job pages, rank, pick today's top 20
    python -m jobhunt scrape --sources jobsdb  # one source
    python -m jobhunt list --status new        # show stored jobs
    python -m jobhunt list --category product  # one role family
    python -m jobhunt mark 42 applied --notes "via referral"
    python -m jobhunt export                   # write output/jobs.csv for Excel
    python -m jobhunt picks                    # today's top picks
    python -m jobhunt import-state DIR         # load the dashboard's saved jobs/tracking docs
    python -m jobhunt export-sync DIR          # write documents + batches for the dashboard
"""

import argparse
import sys
import tomllib
from pathlib import Path

from . import pipeline, storage
from .models import Job
from .scoring import score_job
from .sources import SOURCES


def load_profile(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def scrape(args, conn) -> None:
    profile = load_profile(args.profile)
    search, scoring, categories = profile["search"], profile.get("scoring", {}), profile.get("categories", {})
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

    jobs = [score_job(j, scoring, categories) for j in found.values()]
    new = storage.save_jobs(conn, run_id, jobs, queries)
    # Re-score older jobs too, so edits to profile.toml apply to them.
    storage.rescore(conn, lambda j: score_job(j, scoring, categories))
    if not args.no_details:
        pipeline.fetch_details(conn, profile)
    pipeline.rank_all(conn, profile)
    picks = storage.pick_today(conn, profile.get("ranking", {}).get("daily_picks", 20))
    exported = storage.export_csv(conn, args.out, args.min_score)

    print(f"\n{len(jobs)} jobs this run, {len(new)} new. {pipeline.summary(conn)}")
    print(f"Database: {args.db}, CSV: {args.out} ({exported} rows)\n\nToday's top {len(picks)}:")
    for r in picks:
        print(pipeline.describe(r))


def list_jobs(args, conn) -> None:
    statuses = tuple(args.status) if args.status else storage.STATUSES
    for r in storage.top_jobs(conn, args.min_score, args.top, statuses, tuple(args.category or ())):
        print(f"{r['id']:>5}  {r['score']:>4}  {r['status']:<12}  {r['category'] or '':<17}  {r['title'][:50]:<50}  "
              f"{r['company'][:28]:<28}  {r['url']}")


def mark(args, conn) -> None:
    if not storage.set_status(conn, args.id, args.status, args.notes):
        sys.exit(f"No job with id {args.id}")
    print(f"Job {args.id} -> {args.status}")


def picks(args, conn) -> None:
    rows = conn.execute("SELECT * FROM jobs WHERE pick_date = (SELECT MAX(pick_date) FROM jobs) "
                        "ORDER BY rank_score DESC").fetchall()
    for r in rows:
        print(f"{r['id']:>5}  {pipeline.describe(r)}  {r['apply_url'] or r['url']}")


def import_state(args, conn) -> None:
    n_jobs, n_tracking = storage.import_state(conn, args.dir)
    print(f"Imported {n_jobs} jobs and {n_tracking} tracking records from {args.dir}")


def export_sync(args, conn) -> None:
    profile = load_profile(args.profile)
    pipeline.rank_all(conn, profile)  # freshness changes daily
    print(storage.export_sync(conn, args.dir))


def export(args, conn) -> None:
    n = storage.export_csv(conn, args.out, args.min_score, tuple(args.category or ()))
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
    p.add_argument("--no-details", action="store_true", help="skip opening job pages")
    p.set_defaults(func=scrape)

    p = sub.add_parser("list", parents=[common], help="show stored jobs, best first")
    p.add_argument("--status", nargs="+", choices=storage.STATUSES)
    p.add_argument("--category", nargs="+", help="e.g. product business_analysis (see profile.toml)")
    p.add_argument("--min-score", type=int, default=0)
    p.add_argument("--top", type=int, default=50)
    p.set_defaults(func=list_jobs)

    p = sub.add_parser("mark", parents=[common], help="set a job's status (and optional notes)")
    p.add_argument("id", type=int)
    p.add_argument("status", choices=storage.STATUSES)
    p.add_argument("--notes")
    p.set_defaults(func=mark)

    p = sub.add_parser("picks", parents=[common], help="show the latest daily top picks")
    p.set_defaults(func=picks)

    p = sub.add_parser("import-state", parents=[common], help="load dashboard docs saved by ArtifactData")
    p.add_argument("dir", type=Path, help="folder containing jobs/ and tracking/ JSON files")
    p.set_defaults(func=import_state)

    p = sub.add_parser("export-sync", parents=[common], help="write dashboard documents and batches")
    p.add_argument("dir", type=Path)
    p.add_argument("--profile", type=Path, default=Path("profile.toml"))
    p.set_defaults(func=export_sync)

    p = sub.add_parser("export", parents=[common], help="write the database to CSV")
    p.add_argument("--out", type=Path, default=Path("output/jobs.csv"))
    p.add_argument("--min-score", type=int, default=0)
    p.add_argument("--category", nargs="+")
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
