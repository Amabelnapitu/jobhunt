"""SQLite storage. One file (data/jobs.db), no server; open it with any SQLite viewer.

The dashboard keeps its own copy in the artifact database; `import_state` and `export_sync`
move jobs between the two (see .claude/skills/morning-run/SKILL.md).
"""

import csv
import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from .models import Job

STATUSES = ("new", "interested", "ready", "applied", "interviewing", "offer", "rejected", "ignored")
# Statuses that mean "don't put this in today's picks again".
DONE = ("applied", "interviewing", "offer", "rejected", "ignored")

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    sources     TEXT NOT NULL,
    found       INTEGER,
    new         INTEGER
);

CREATE TABLE IF NOT EXISTS jobs (
    id            INTEGER PRIMARY KEY,
    dedupe_key    TEXT NOT NULL UNIQUE,   -- normalized "title|company"
    source        TEXT NOT NULL,
    source_job_id TEXT NOT NULL,
    title         TEXT NOT NULL,
    company       TEXT NOT NULL,
    location      TEXT,
    url           TEXT,
    posted        TEXT,                   -- YYYY-MM-DD when the board gives it
    salary        TEXT,
    snippet       TEXT,
    score         INTEGER NOT NULL DEFAULT 0,
    matched       TEXT,
    first_seen    TEXT NOT NULL,
    first_run_id  INTEGER NOT NULL REFERENCES runs (id),
    last_seen     TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'new'
                  CHECK (status IN ({", ".join(f"'{s}'" for s in STATUSES)})),
    notes         TEXT
);
CREATE INDEX IF NOT EXISTS jobs_score  ON jobs (score DESC);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs (status);

CREATE TABLE IF NOT EXISTS job_queries (
    job_id INTEGER NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    query  TEXT NOT NULL,
    PRIMARY KEY (job_id, query)
);
"""

# Columns added after the first release; connect() adds any the database lacks.
LATER_COLUMNS = {
    "category": "TEXT",
    "description": "TEXT",              # full text from the job's own page
    "apply_url": "TEXT",
    "apply_method": "TEXT",             # jobsdb | linkedin_easy_apply | external
    "applicants": "INTEGER",            # LinkedIn's applicant count, when shown
    "seniority": "TEXT",
    "documents": "TEXT",                # JSON list: documents the posting asks for
    "info_needed": "TEXT",              # JSON list: details the posting asks for (salary, notice...)
    "languages": "TEXT",                # JSON {language: required|preferred}
    "years_required": "INTEGER",
    "eligibility": "TEXT",              # eligible | check | ineligible
    "reasons": "TEXT",                  # JSON list explaining check/ineligible
    "interview": "INTEGER",             # 0-100 interview likelihood
    "freshness": "INTEGER",             # 0-100, newer is higher
    "rank_score": "REAL",               # NULL when ineligible
    "pick_date": "TEXT",                # last day this job was in the daily top picks
    "details_fetched_at": "TEXT",
    "alt_urls": "TEXT",                 # JSON list: the same job found elsewhere (employer site, JobsDB)
    "remote_exists": "INTEGER",         # 1 when the dashboard already has this job's document
}

# Scraped fields refreshed on every sighting. status/notes/first_seen are never overwritten.
_UPSERT = """
INSERT INTO jobs (dedupe_key, source, source_job_id, title, company, location, url, posted,
                  salary, snippet, score, matched, category, first_seen, first_run_id, last_seen)
VALUES (:dedupe_key, :source, :job_id, :title, :company, :location, :url, :posted,
        :salary, :snippet, :score, :matched, :category, :now, :run_id, :now)
ON CONFLICT (dedupe_key) DO UPDATE SET
    location = excluded.location, url = excluded.url, salary = excluded.salary,
    snippet = excluded.snippet, score = excluded.score, matched = excluded.matched,
    category = excluded.category,
    posted = COALESCE(NULLIF(excluded.posted, ''), jobs.posted),
    last_seen = excluded.last_seen
RETURNING id, first_run_id = :run_id AS is_new
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def doc_id(dedupe_key: str) -> str:
    """Stable id for a job's documents in the dashboard database."""
    return hashlib.sha1(dedupe_key.encode()).hexdigest()[:16]


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
    for name, kind in LATER_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {kind}")
    conn.commit()
    return conn


def start_run(conn: sqlite3.Connection, sources: list[str]) -> int:
    with conn:
        cur = conn.execute("INSERT INTO runs (started_at, sources) VALUES (?, ?)",
                           (_now(), ",".join(sources)))
    return cur.lastrowid


def save_jobs(conn: sqlite3.Connection, run_id: int, jobs: list[Job],
              queries: dict[tuple[str, str], set[str]]) -> set[tuple[str, str]]:
    """Upsert this run's jobs. Returns the dedupe keys of jobs never seen before."""
    now, new = _now(), set()
    with conn:
        for job in jobs:
            params = {**vars(job), "dedupe_key": "|".join(job.key), "now": now, "run_id": run_id}
            row = conn.execute(_UPSERT, params).fetchone()
            if row["is_new"]:
                new.add(job.key)
            conn.executemany("INSERT OR IGNORE INTO job_queries (job_id, query) VALUES (?, ?)",
                             [(row["id"], q) for q in queries.get(job.key, ())])
        conn.execute("UPDATE runs SET finished_at = ?, found = ?, new = ? WHERE id = ?",
                     (_now(), len(jobs), len(new), run_id))
    return new


def job_from_row(r: sqlite3.Row) -> Job:
    """Rebuild a Job for scoring; the full description, once read, stands in for the snippet."""
    return Job(source=r["source"], job_id=r["source_job_id"], title=r["title"], company=r["company"],
               location=r["location"] or "", url=r["url"] or "", posted=r["posted"] or "",
               salary=r["salary"] or "", snippet=r["description"] or r["snippet"] or "")


def rescore(conn: sqlite3.Connection, score) -> None:
    """Re-apply the scoring function to every stored job (after editing profile.toml)."""
    rows = conn.execute("SELECT * FROM jobs").fetchall()
    with conn:
        for r in rows:
            job = score(job_from_row(r))
            conn.execute("UPDATE jobs SET score = ?, matched = ?, category = ? WHERE id = ?",
                         (job.score, job.matched, job.category, r["id"]))


def needing_details(conn: sqlite3.Connection, min_score: int, limit: int,
                    free_sources: tuple[str, ...] = ("jobsdb",)) -> list[sqlite3.Row]:
    """Jobs whose details we haven't got yet, best first. `limit` caps only the sources that
    cost a page request; free_sources (details from data we already have) are all included."""
    rows = conn.execute(
        "SELECT * FROM jobs WHERE details_fetched_at IS NULL AND score >= ? AND status NOT IN "
        f"({','.join('?' * len(DONE))}) ORDER BY score DESC, posted DESC",
        (min_score, *DONE)).fetchall()
    free = [r for r in rows if r["source"] in free_sources]
    paid = [r for r in rows if r["source"] not in free_sources][:limit]
    return free + paid


def save_details(conn: sqlite3.Connection, job_id: int, details: dict) -> None:
    with conn:
        conn.execute(
            "UPDATE jobs SET description = ?, apply_url = ?, apply_method = ?, applicants = ?, "
            "seniority = ?, details_fetched_at = ? WHERE id = ?",
            (details.get("description", ""), details.get("apply_url", ""), details.get("apply_method", ""),
             details.get("applicants"), details.get("seniority", ""), _now(), job_id))


def save_ranking(conn: sqlite3.Connection, job_id: int, fields: dict) -> None:
    fields = {k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in fields.items()}
    cols = ", ".join(f"{k} = :{k}" for k in fields)
    with conn:
        conn.execute(f"UPDATE jobs SET {cols} WHERE id = :id", {**fields, "id": job_id})


def pick_today(conn: sqlite3.Connection, n: int, today: str | None = None) -> list[sqlite3.Row]:
    """Mark today's top-n ranked jobs that haven't been applied to (or dismissed).

    Only jobs whose page was read qualify: eligibility and required documents come from it."""
    today = today or date.today().isoformat()
    rows = conn.execute(
        "SELECT * FROM jobs WHERE rank_score IS NOT NULL AND details_fetched_at IS NOT NULL AND status NOT IN "
        f"({','.join('?' * len(DONE))}) ORDER BY rank_score DESC, posted DESC LIMIT ?",
        (*DONE, n)).fetchall()
    with conn:
        # Picking again the same day replaces that day's picks.
        conn.execute("UPDATE jobs SET pick_date = NULL WHERE pick_date = ?", (today,))
        conn.executemany("UPDATE jobs SET pick_date = ? WHERE id = ?", [(today, r["id"]) for r in rows])
    return rows


def set_status(conn: sqlite3.Connection, job_id: int, status: str, notes: str | None = None) -> bool:
    with conn:
        cur = conn.execute("UPDATE jobs SET status = ?, notes = COALESCE(?, notes) WHERE id = ?",
                           (status, notes, job_id))
    return cur.rowcount == 1


def top_jobs(conn: sqlite3.Connection, min_score: int = 0, limit: int | None = None,
             statuses: tuple[str, ...] = STATUSES,
             categories: tuple[str, ...] = ()) -> list[sqlite3.Row]:
    sql = f"SELECT * FROM jobs WHERE score >= ? AND status IN ({','.join('?' * len(statuses))})"
    params: list = [min_score, *statuses]
    if categories:
        sql += f" AND category IN ({','.join('?' * len(categories))})"
        params += categories
    sql += " ORDER BY rank_score IS NULL, rank_score DESC, score DESC, posted DESC"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


_CSV_SKIP = {"description", "snippet", "dedupe_key", "first_run_id", "remote_exists", "details_fetched_at"}


def export_csv(conn: sqlite3.Connection, path: Path, min_score: int = 0,
               categories: tuple[str, ...] = ()) -> int:
    rows = top_jobs(conn, min_score, categories=categories)
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig so Excel shows Chinese company names correctly.
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        cols = [c for c in (rows[0].keys() if rows else ["id"]) if c not in _CSV_SKIP]
        writer.writerow(cols)
        writer.writerows([r[c] for c in cols] for r in rows)
    return len(rows)


# ---- Sync with the dashboard database -------------------------------------------------
#
# Dashboard collections (document ids are doc_id(dedupe_key)):
#   jobs/<id>      written once by the scraper after the job page is read, never edited, so
#                  every job document stays at version 1 (which makes deletes safe without a read)
#   runs/<stamp>   one new document per run: today's picks and every job's current rank,
#                  eligibility and scores (the parts that change daily)
#   tracking/<id>  your answers and status, written only by you and the apply workflow
#   profile/me     your standing answers

_STATIC_FIELDS = ("title", "company", "location", "url", "apply_url", "apply_method", "source",
                  "source_job_id", "posted", "salary", "snippet", "documents", "info_needed", "languages",
                  "years_required", "applicants", "seniority", "description", "alt_urls", "first_seen",
                  "details_fetched_at")
_JSON_FIELDS = {"documents": [], "info_needed": [], "languages": {}, "reasons": []}
_TRACKING_STATUS = {"interested": "interested", "ready": "ready", "queued": "ready",
                    "needs_input": "interested", "applied": "applied",
                    "interviewing": "interviewing", "offer": "offer", "rejected": "rejected",
                    "skipped": "ignored", "ignored": "ignored"}


def import_state(conn: sqlite3.Connection, state_dir: Path) -> tuple[int, int]:
    """Load the dashboard's jobs and tracking docs (saved by an ArtifactData read with
    out_dir) into this database.

    The scheduled scrape starts from an empty database, so this keeps each job's first_seen
    date, fetched details, and your status between runs."""
    n_jobs = n_tracking = 0
    jobs_dir, tracking_dir = state_dir / "jobs", state_dir / "tracking"
    if jobs_dir.is_dir():
        run_id = start_run(conn, ["import"])
        with conn:
            for path in sorted(jobs_dir.glob("*.json")):
                d = json.loads(path.read_text(encoding="utf-8"))
                if not d.get("key"):
                    continue
                values = {f: d.get(f) for f in _STATIC_FIELDS}
                for f in ("documents", "info_needed", "languages"):
                    values[f] = json.dumps(values[f] if values[f] is not None else _JSON_FIELDS[f])
                values["alt_urls"] = json.dumps(values["alt_urls"]) if values["alt_urls"] is not None else None
                values.update(
                    dedupe_key=d["key"], first_run_id=run_id, remote_exists=1,
                    title=values["title"] or "", company=values["company"] or "",
                    source=values["source"] or "", source_job_id=values["source_job_id"] or "",
                    first_seen=values["first_seen"] or _now(),
                    # Documents written before full descriptions were kept were cut at exactly
                    # 3000 characters; read those jobs' pages again rather than trust the cut text.
                    details_fetched_at=None if len(values["description"] or "") == 3000 and values["source"] != "jobsdb"
                    else values["details_fetched_at"],
                    last_seen=d.get("last_seen") or values["first_seen"] or _now())
                cols = ", ".join(values)
                conn.execute(f"INSERT OR REPLACE INTO jobs ({cols}) VALUES "
                             f"({', '.join(':' + c for c in values)})", values)
                n_jobs += 1
    runs = sorted((state_dir / "runs").glob("*.json")) if (state_dir / "runs").is_dir() else []
    if runs:
        # The latest run remembers when each job was last picked, so earlier picks stay available.
        ranks = json.loads(runs[-1].read_text(encoding="utf-8")).get("ranks", {})
        ids = {doc_id(r["dedupe_key"]): r["id"] for r in conn.execute("SELECT id, dedupe_key FROM jobs")}
        with conn:
            for did, rank in ranks.items():
                if rank.get("last_picked") and did in ids:
                    conn.execute("UPDATE jobs SET pick_date = ? WHERE id = ?", (rank["last_picked"], ids[did]))
    if tracking_dir.is_dir():
        ids = {doc_id(r["dedupe_key"]): r["id"] for r in conn.execute("SELECT id, dedupe_key FROM jobs")}
        with conn:
            for path in sorted(tracking_dir.glob("*.json")):
                d = json.loads(path.read_text(encoding="utf-8"))
                status = _TRACKING_STATUS.get(d.get("status", ""))
                if status and path.stem in ids:
                    conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, ids[path.stem]))
                    n_tracking += 1
    return n_jobs, n_tracking


def export_sync(conn: sqlite3.Connection, out_dir: Path, min_fit_for_excluded: int = 20,
                keep_days: int = 30, now: datetime | None = None, note: str = "",
                max_ranked: int = 150, max_excluded: int = 50) -> dict:
    """Write the dashboard documents for this run, plus writes.json: the ArtifactData "batch"
    calls to make (a list of batches, each a list of up to 50 `writes` entries).

    Shows the max_ranked best-ranked jobs whose details were read, today's picks, jobs you
    acted on, and the max_excluded best-fitting ineligible jobs (so you can see why they were
    excluded). Creates job documents for the ones the dashboard doesn't have yet, writes one
    runs/ document with every shown job's current rank, and deletes job documents you never
    acted on that dropped out or weren't seen for keep_days."""
    now = now or datetime.now()
    cutoff = (now.date() - timedelta(days=keep_days)).isoformat()
    docs_dir = out_dir / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    writes, ranks, created = [], {}, 0
    pick_date = conn.execute("SELECT MAX(pick_date) FROM jobs").fetchone()[0]
    shown = {r[0] for r in conn.execute(
        "SELECT id FROM jobs WHERE details_fetched_at IS NOT NULL AND rank_score IS NOT NULL "
        "ORDER BY rank_score DESC LIMIT ?", (max_ranked,))}
    shown |= {r[0] for r in conn.execute(
        "SELECT id FROM jobs WHERE details_fetched_at IS NOT NULL AND eligibility = 'ineligible' "
        "AND score >= ? ORDER BY score DESC LIMIT ?", (min_fit_for_excluded, max_excluded))}
    # Picks from earlier days stay available (to apply to later) until keep_days pass.
    shown |= {r[0] for r in conn.execute("SELECT id FROM jobs WHERE pick_date >= ?", (cutoff,))}
    for r in conn.execute("SELECT * FROM jobs").fetchall():
        did = doc_id(r["dedupe_key"])
        keep = r["id"] in shown
        stale = r["last_seen"][:10] < cutoff
        acted = r["status"] != "new"
        if (stale or not keep) and not acted:
            if r["remote_exists"]:
                writes.append({"op": "delete", "collection": "jobs", "doc_id": did, "if_version": 1})
            continue
        if not keep and not r["remote_exists"]:
            continue
        ranks[did] = {
            "score": r["score"], "matched": r["matched"] or "", "category": r["category"] or "other",
            "eligibility": r["eligibility"], "reasons": json.loads(r["reasons"] or "[]"),
            "interview": r["interview"], "freshness": r["freshness"], "rank_score": r["rank_score"],
            "last_seen": r["last_seen"], "last_picked": r["pick_date"],
        }
        if not r["remote_exists"]:
            doc = {f: r[f] for f in _STATIC_FIELDS}
            doc["key"] = r["dedupe_key"]
            for f in ("documents", "info_needed", "languages"):
                doc[f] = json.loads(r[f]) if r[f] else _JSON_FIELDS[f]
            doc["alt_urls"] = json.loads(r["alt_urls"]) if r["alt_urls"] else []
            # Keep the whole description: requirements often come last, and eligibility is
            # re-checked from this copy on later runs. (Documents may be up to 256 KiB.)
            doc["description"] = (r["description"] or "")[:60000]
            path = docs_dir / f"{did}.json"
            path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            writes.append({"op": "set", "collection": "jobs", "doc_id": did, "file_path": str(path.resolve())})
            created += 1

    picks = [doc_id(r["dedupe_key"]) for r in conn.execute(
        "SELECT dedupe_key FROM jobs WHERE pick_date = ? ORDER BY rank_score DESC", (pick_date,))]
    last = conn.execute("SELECT found, new FROM runs WHERE sources != 'import' "
                        "ORDER BY id DESC LIMIT 1").fetchone()
    stamp = now.strftime("%Y-%m-%dT%H%M%S")
    run_doc = {
        "at": now.isoformat(timespec="seconds"), "date": now.date().isoformat(),
        "scraped": last["found"] if last else None, "new": last["new"] if last else None,
        "eligible": sum(1 for v in ranks.values() if v["eligibility"] in ("eligible", "check")),
        "picks": [p for p in picks if p in ranks], "ranks": ranks, "note": note,
    }
    run_path = docs_dir / f"run-{stamp}.json"
    run_path.write_text(json.dumps(run_doc, ensure_ascii=False), encoding="utf-8")
    # The run document goes last so the dashboard never shows ranks for jobs not yet written.
    writes.append({"op": "set", "collection": "runs", "doc_id": stamp, "file_path": str(run_path.resolve())})
    batches = [writes[i:i + 50] for i in range(0, len(writes), 50)]
    (out_dir / "writes.json").write_text(json.dumps(batches, indent=1), encoding="utf-8")
    return {"new_job_documents": created, "deletes": sum(w["op"] == "delete" for w in writes),
            "ranked": len(ranks), "picks": len(run_doc["picks"]), "batches": len(batches)}
