"""SQLite storage. One file (data/jobs.db), no server; open it with any SQLite viewer."""

import csv
import sqlite3
from datetime import datetime
from pathlib import Path

from .models import Job

STATUSES = ("new", "interested", "applied", "interviewing", "offer", "rejected", "ignored")

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
    category      TEXT,
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


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
    if columns and "category" not in columns:  # database created before categories existed
        conn.execute("ALTER TABLE jobs ADD COLUMN category TEXT")
    conn.executescript(SCHEMA)
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
            row = conn.execute(_UPSERT, {**vars(job), "dedupe_key": "|".join(job.key), "now": now, "run_id": run_id}).fetchone()
            if row["is_new"]:
                new.add(job.key)
            conn.executemany("INSERT OR IGNORE INTO job_queries (job_id, query) VALUES (?, ?)",
                             [(row["id"], q) for q in queries.get(job.key, ())])
        conn.execute("UPDATE runs SET finished_at = ?, found = ?, new = ? WHERE id = ?",
                     (_now(), len(jobs), len(new), run_id))
    return new


def rescore(conn: sqlite3.Connection, score) -> None:
    """Re-apply the scoring function to every stored job (after editing profile.toml)."""
    rows = conn.execute("SELECT id, source, source_job_id, title, company, location, url, snippet FROM jobs").fetchall()
    with conn:
        for r in rows:
            job = score(Job(source=r["source"], job_id=r["source_job_id"], title=r["title"],
                            company=r["company"], location=r["location"] or "", url=r["url"] or "",
                            snippet=r["snippet"] or ""))
            conn.execute("UPDATE jobs SET score = ?, matched = ?, category = ? WHERE id = ?",
                         (job.score, job.matched, job.category, r["id"]))


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
    sql += " ORDER BY score DESC, posted DESC"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def export_csv(conn: sqlite3.Connection, path: Path, min_score: int = 0,
               categories: tuple[str, ...] = ()) -> int:
    rows = top_jobs(conn, min_score, categories=categories)
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig so Excel shows Chinese company names correctly.
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(rows[0].keys() if rows else ["id"])
        writer.writerows(tuple(r) for r in rows)
    return len(rows)
