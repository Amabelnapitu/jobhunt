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
    "remote_version": "INTEGER",        # version of the dashboard's copy, for safe updates
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
    return Job(source=r["source"], job_id=r["source_job_id"], title=r["title"], company=r["company"],
               location=r["location"] or "", url=r["url"] or "", posted=r["posted"] or "",
               salary=r["salary"] or "", snippet=r["snippet"] or "")


def rescore(conn: sqlite3.Connection, score) -> None:
    """Re-apply the scoring function to every stored job (after editing profile.toml)."""
    rows = conn.execute("SELECT * FROM jobs").fetchall()
    with conn:
        for r in rows:
            job = score(job_from_row(r))
            conn.execute("UPDATE jobs SET score = ?, matched = ?, category = ? WHERE id = ?",
                         (job.score, job.matched, job.category, r["id"]))


def needing_details(conn: sqlite3.Connection, min_score: int, limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM jobs WHERE details_fetched_at IS NULL AND score >= ? AND status NOT IN "
        f"({','.join('?' * len(DONE))}) ORDER BY score DESC, posted DESC LIMIT ?",
        (min_score, *DONE, limit)).fetchall()


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


_CSV_SKIP = {"description", "snippet", "dedupe_key", "first_run_id", "remote_version", "details_fetched_at"}


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
# Dashboard collections: "jobs" is written only by the scraper; "tracking" only by you (on the
# dashboard) and by the apply workflow. The scraper reads tracking but never writes it.
# Both use doc_id(dedupe_key) as the document id.

_REMOTE_FIELDS = ("title", "company", "location", "url", "apply_url", "apply_method", "source",
                  "source_job_id", "posted", "salary", "category", "score", "matched", "eligibility",
                  "reasons", "interview", "freshness", "rank_score", "pick_date", "documents",
                  "info_needed", "languages", "years_required", "applicants", "seniority",
                  "description", "first_seen", "last_seen", "details_fetched_at")
_JSON_FIELDS = {"documents": [], "info_needed": [], "languages": {}, "reasons": []}
_TRACKING_STATUS = {"interested": "interested", "ready": "ready", "applied": "applied",
                    "interviewing": "interviewing", "offer": "offer", "rejected": "rejected",
                    "skipped": "ignored", "ignored": "ignored"}


def _read_doc(path: Path) -> tuple[str, dict, int | None]:
    """Read one document file saved by an ArtifactData read with out_dir.

    Accepts the bare document or an envelope with data/version keys."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw.get("data"), dict):
        return raw.get("id") or path.stem, raw["data"], raw.get("version")
    version = raw.get("version") or raw.get("__version__") or raw.get("_version")
    return path.stem, raw, version


def import_state(conn: sqlite3.Connection, state_dir: Path) -> tuple[int, int]:
    """Load the dashboard's jobs and tracking docs into this database.

    The scheduled scrape starts from an empty database, so this keeps each job's first_seen
    date, fetched details, and your status between runs."""
    n_jobs = n_tracking = 0
    jobs_dir, tracking_dir = state_dir / "jobs", state_dir / "tracking"
    if jobs_dir.is_dir():
        run_id = start_run(conn, ["import"])
        with conn:
            for path in sorted(jobs_dir.glob("*.json")):
                _, d, version = _read_doc(path)
                if not d.get("key"):
                    continue
                values = {f: d.get(f) for f in _REMOTE_FIELDS}
                for f, empty in _JSON_FIELDS.items():
                    values[f] = json.dumps(values[f] if values[f] is not None else empty)
                values.update(
                    dedupe_key=d["key"], first_run_id=run_id, remote_version=version,
                    title=values["title"] or "", company=values["company"] or "",
                    source=values["source"] or "", source_job_id=values["source_job_id"] or "",
                    score=values["score"] or 0,
                    first_seen=values["first_seen"] or _now(), last_seen=values["last_seen"] or _now())
                cols = ", ".join(values)
                conn.execute(f"INSERT OR REPLACE INTO jobs ({cols}) VALUES "
                             f"({', '.join(':' + c for c in values)})", values)
                n_jobs += 1
    if tracking_dir.is_dir():
        ids = {doc_id(r["dedupe_key"]): r["id"] for r in conn.execute("SELECT id, dedupe_key FROM jobs")}
        with conn:
            for path in sorted(tracking_dir.glob("*.json")):
                did, d, _ = _read_doc(path)
                status = _TRACKING_STATUS.get(d.get("status", ""))
                if status and did in ids:
                    conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, ids[did]))
                    n_tracking += 1
    return n_jobs, n_tracking


def export_sync(conn: sqlite3.Connection, out_dir: Path, min_fit_for_excluded: int = 20,
                keep_days: int = 30, today: date | None = None) -> dict:
    """Write one JSON file per dashboard job document, plus writes.json: the ArtifactData
    "batch" calls to make (a list of batches, each a list of `writes` entries, max 50).

    Sends every ranked job, and ineligible jobs that otherwise fit well, so you can see why
    they were excluded. Deletes the dashboard copy of jobs you never acted on that dropped
    out, or weren't seen for keep_days."""
    today = today or date.today()
    cutoff = (today - timedelta(days=keep_days)).isoformat()
    docs_dir = out_dir / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    writes, sent = [], 0
    for r in conn.execute("SELECT * FROM jobs").fetchall():
        did = doc_id(r["dedupe_key"])
        keep = r["rank_score"] is not None or (
            r["eligibility"] == "ineligible" and r["score"] >= min_fit_for_excluded)
        stale = r["last_seen"][:10] < cutoff and r["status"] == "new"
        if stale or not keep:
            if r["remote_version"] and r["status"] == "new":
                writes.append({"op": "delete", "collection": "jobs", "doc_id": did,
                               "if_version": r["remote_version"]})
            continue
        doc = {f: r[f] for f in _REMOTE_FIELDS}
        doc["key"] = r["dedupe_key"]
        for f, empty in _JSON_FIELDS.items():
            doc[f] = json.loads(r[f]) if r[f] else empty
        doc["description"] = (r["description"] or "")[:3000]
        path = docs_dir / f"{did}.json"
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        entry = {"op": "set", "collection": "jobs", "doc_id": did, "file_path": str(path.resolve())}
        if r["remote_version"]:
            entry["if_version"] = r["remote_version"]
        writes.append(entry)
        sent += 1
    batches = [writes[i:i + 50] for i in range(0, len(writes), 50)]
    (out_dir / "writes.json").write_text(json.dumps(batches, indent=1), encoding="utf-8")
    return {"documents": sent, "deletes": sum(w["op"] == "delete" for w in writes),
            "batches": len(batches)}
