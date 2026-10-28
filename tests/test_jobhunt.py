import tomllib
from pathlib import Path

from jobhunt import storage
from jobhunt.models import Job
from jobhunt.scoring import score_job
from jobhunt.sources import jobsdb, linkedin

ROOT = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
SCORING = tomllib.loads((ROOT / "profile.toml").read_text())["scoring"]


def make(title, company="Acme", snippet=""):
    return Job(source="t", job_id="1", title=title, company=company, location="HK",
               url="u", snippet=snippet)


def test_relevant_role_outranks_construction_pm():
    ai_pm = score_job(make("Assistant Project Manager, GenAI", "Some Life Insurance"), SCORING)
    site_pm = score_job(make("Assistant Project Manager (Building Construction)"), SCORING)
    assert ai_pm.score > 0 > site_pm.score
    assert "-construction" in site_pm.matched


def test_keywords_match_whole_words_only():
    # "intern" must not fire inside "International", "ai" not inside "maintain".
    job = score_job(make("Business Analyst - International Markets", snippet="maintain reports"), SCORING)
    assert "-intern" not in job.matched
    assert "ai" not in job.matched.split(", ")


def test_title_hit_counts_double():
    in_title = score_job(make("Actuarial Analyst"), SCORING).score
    in_snippet = score_job(make("Analyst", snippet="actuarial team"), SCORING).score
    assert in_title - in_snippet == SCORING["keywords"]["actuarial"]


def test_parse_jobsdb_item():
    item = {
        "id": 81234567,
        "title": " Assistant Project Manager (AI) ",
        "advertiser": {"description": "Example Insurance Ltd"},
        "locations": [{"label": "Central, Central and Western District"}],
        "listingDate": "2026-10-20T03:00:00Z",
        "salaryLabel": "$30,000 – $40,000 per month",
        "teaser": "Drive GenAI delivery",
        "bulletPoints": ["Agile team", "Hybrid"],
    }
    job = jobsdb.parse_item(item, "project manager AI")
    assert job.title == "Assistant Project Manager (AI)"
    assert job.company == "Example Insurance Ltd"
    assert job.url == "https://hk.jobsdb.com/job/81234567"
    assert job.posted == "2026-10-20"
    assert "Agile team" in job.snippet


def test_parse_linkedin_html():
    jobs = linkedin.parse_html((FIXTURES / "linkedin.html").read_text(), "q")
    assert len(jobs) == 2
    first = jobs[0]
    assert first.job_id == "4012345678"
    assert first.title == "Product Manager, Generative AI"
    assert first.company == "Example Bank"
    assert first.location == "Hong Kong SAR"
    assert first.url == "https://hk.linkedin.com/jobs/view/product-manager-4012345678"
    assert first.posted == "2026-10-25"


def test_db_upsert_tracks_new_and_keeps_status(tmp_path):
    conn = storage.connect(tmp_path / "jobs.db")
    ba = score_job(make("Business Analyst"), SCORING)
    run1 = storage.start_run(conn, ["t"])
    new = storage.save_jobs(conn, run1, [ba], {ba.key: {"business analyst"}})
    assert new == {ba.key}

    job_id = conn.execute("SELECT id FROM jobs").fetchone()["id"]
    assert storage.set_status(conn, job_id, "applied", "sent CV")

    # Same job again (different query) plus a fresh one.
    pm = score_job(make("Project Manager"), SCORING)
    run2 = storage.start_run(conn, ["t"])
    new = storage.save_jobs(conn, run2, [make("Business Analyst"), pm],
                            {ba.key: {"insurance"}, pm.key: {"pm"}})
    assert new == {pm.key}

    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert (row["status"], row["notes"]) == ("applied", "sent CV")
    queries = {r[0] for r in conn.execute("SELECT query FROM job_queries WHERE job_id = ?", (job_id,))}
    assert queries == {"business analyst", "insurance"}
    assert [r["new"] for r in conn.execute("SELECT new FROM runs ORDER BY id")] == [1, 1]


def test_rescore_and_export(tmp_path):
    conn = storage.connect(tmp_path / "jobs.db")
    jobs = [score_job(make("Actuarial Analyst"), SCORING), score_job(make("Site Engineer"), SCORING)]
    storage.save_jobs(conn, storage.start_run(conn, ["t"]), jobs, {})
    conn.execute("UPDATE jobs SET score = 0")
    storage.rescore(conn, lambda j: score_job(j, SCORING))
    assert storage.top_jobs(conn)[0]["title"] == "Actuarial Analyst"
    assert storage.export_csv(conn, tmp_path / "out.csv") == 1  # site role scores < 0


def test_invalid_status_rejected(tmp_path):
    import sqlite3
    import pytest
    conn = storage.connect(tmp_path / "jobs.db")
    storage.save_jobs(conn, storage.start_run(conn, ["t"]), [make("Analyst")], {})
    with pytest.raises(sqlite3.IntegrityError):
        storage.set_status(conn, 1, "bogus")
