"""Steps that run after scraping: open job pages, analyze, rank, pick today's top jobs."""

import json
import sys
from datetime import date

from . import lanes, storage
from .analysis import analyze
from .ranking import rank
from .sources import DETAILS

_DOMAIN_WORDS = ("insurance", "insurer", "actuarial", "ifrs 17", "reinsurance", "life assurance")
FULL_DESCRIPTION = 400  # characters; shorter is a summary that may omit requirements


def fetch_details(conn, profile: dict) -> int:
    """Open the job page of promising jobs we haven't read yet. Returns how many succeeded."""
    cfg = profile.get("ranking", {})
    rows = storage.needing_details(conn, cfg.get("detail_min_score", 10), cfg.get("max_details_per_run", 80))
    done = 0
    for r in rows:
        try:
            details = DETAILS[r["source"]](r["source_job_id"], r["snippet"] or "")
        except Exception as exc:  # keep going; it will be retried next run
            print(f"[details] {r['title']!r}: FAILED ({exc})", file=sys.stderr)
            continue
        details["apply_url"] = details.get("apply_url") or r["url"]
        storage.save_details(conn, r["id"], details)
        done += 1
    print(f"[details] got details for {done} of {len(rows)} jobs", file=sys.stderr)
    return done


def rank_all(conn, profile: dict, today: date | None = None) -> None:
    candidate, weights = profile.get("candidate", {}), profile.get("ranking", {})
    for r in conn.execute("SELECT * FROM jobs").fetchall():
        text = r["description"] or r["snippet"] or ""
        a = analyze(text, r["company"])
        haystack = f"{text} {r['company']} {r['title']}".lower()
        ranked = rank(
            fit_score=r["score"], a=a, title=r["title"], matched=(r["matched"] or "").split(", "),
            posted=r["posted"] or "", applicants=r["applicants"], candidate=candidate, weights=weights,
            has_description=len(r["description"] or "") >= FULL_DESCRIPTION, domain_hit=any(w in haystack for w in _DOMAIN_WORDS),
            today=today)
        storage.save_ranking(conn, r["id"], {
            "documents": a.documents, "info_needed": a.info_needed, "languages": a.languages,
            "years_required": a.years_required, "eligibility": ranked.eligibility,
            "reasons": ranked.reasons, "interview": ranked.interview, "freshness": ranked.freshness,
            "rank_score": ranked.rank_score,
            **lanes.lane(r["apply_method"] or "", r["apply_url"] or "", r["location"] or "",
                         ranked.eligibility, bool(r["closed"])),
        })


def check_open(conn, profile: dict) -> int:
    """Re-read the LinkedIn page of the best-ranked jobs to catch postings that have closed
    since their details were read. Returns how many were found closed."""
    limit = profile.get("ranking", {}).get("recheck_open", 30)
    rows = conn.execute(
        "SELECT * FROM jobs WHERE source = 'linkedin' AND rank_score IS NOT NULL AND COALESCE(closed, 0) = 0 "
        f"AND details_fetched_at < ? AND status NOT IN ({','.join('?' * len(storage.DONE))}) "
        "ORDER BY rank_score DESC LIMIT ?", (date.today().isoformat(), *storage.DONE, limit)).fetchall()
    closed = 0
    for r in rows:
        try:
            details = DETAILS["linkedin"](r["source_job_id"])
        except Exception as exc:
            print(f"[open] {r['title']!r}: FAILED ({exc})", file=sys.stderr)
            continue
        if details.get("closed"):
            with conn:
                conn.execute("UPDATE jobs SET closed = 1 WHERE id = ?", (r["id"],))
            closed += 1
    print(f"[open] rechecked {len(rows)} jobs, {closed} closed", file=sys.stderr)
    return closed


def summary(conn) -> dict:
    counts = {row[0] or "unranked": row[1] for row in
              conn.execute("SELECT eligibility, COUNT(*) FROM jobs GROUP BY eligibility")}
    return {"eligibility": counts, "total": sum(counts.values())}


def describe(r) -> str:
    reasons = json.loads(r["reasons"] or "[]")
    note = f"  ({'; '.join(reasons)})" if reasons else ""
    return (f"{r['rank_score'] or 0:>5.1f}  {r['eligibility'] or '':<10}  {r['category'] or '':<17}  "
            f"{r['title'][:50]:<50}  {r['company'][:26]:<26}{note}")
