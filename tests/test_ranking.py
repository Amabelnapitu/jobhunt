import json
import tomllib
from datetime import date
from pathlib import Path

from jobhunt import pipeline, storage
from jobhunt.analysis import analyze
from jobhunt.models import Job
from jobhunt.ranking import CHECK, ELIGIBLE, INELIGIBLE, eligibility, freshness, rank
from jobhunt.scoring import score_job
from jobhunt.sources import linkedin

ROOT = Path(__file__).parent.parent
PROFILE = tomllib.loads((ROOT / "profile.toml").read_text())
CANDIDATE = PROFILE["candidate"]

HK_AD = """Requirements:
• Degree holder in Business, Analytics or related disciplines
• 1 year of experience in product management or business analysis
• Good command of English; Cantonese is an advantage
• Knowledge of Generative AI / LLM tools
Interested parties please send your full resume and cover letter with current and expected salary
and availability to us."""


def test_extracts_documents_info_years_languages():
    a = analyze(HK_AD)
    assert a.documents == ["CV / resume", "Cover letter"]
    assert a.info_needed == ["Current salary", "Expected salary", "Availability / notice period"]
    assert a.years_required == 1
    assert a.languages == {"cantonese": "preferred"}


def test_language_rules_match_candidate():
    def status(text):
        return eligibility(analyze(text), "Business Analyst", CANDIDATE, True)[0]
    assert status("Fluent in English and Cantonese.") == INELIGIBLE
    assert status("Trilingual is a must.") == INELIGIBLE
    assert status("Good written Chinese required.") == INELIGIBLE
    assert status("Fluent Mandarin is required.") == INELIGIBLE
    assert status("Conversational Mandarin is required.") == ELIGIBLE      # candidate has this
    assert status("Mandarin is a plus.") == ELIGIBLE
    assert status("Korean speaking is required.") == ELIGIBLE
    assert status("Bilingual candidates preferred.") == CHECK


def test_experience_thresholds():
    def status(text):
        return eligibility(analyze(text), "Analyst", CANDIDATE, True)
    assert status("At least 1 year of relevant experience.")[0] == ELIGIBLE
    assert status("Minimum 2 years' working experience.")[0] == CHECK
    assert status("3-5 years of experience in banking.") == (INELIGIBLE, ["Requires 3+ years' experience"])
    assert status("No experience needed; fresh graduates welcome.")[0] == ELIGIBLE


def test_pr_only_is_ineligible_for_iang():
    a = analyze("Applicants must be Hong Kong permanent residents.")
    assert eligibility(a, "Analyst", CANDIDATE, True)[0] == INELIGIBLE


def test_freshness_newest_first():
    today = date(2026, 10, 28)
    assert freshness("2026-10-28", today) == 100
    assert freshness("2026-10-21", today) > freshness("2026-10-01", today)
    assert freshness("", today) == freshness("2026-10-14", today)


def test_rare_language_and_low_applicants_raise_interview_odds():
    base = dict(fit_score=30, title="Business Analyst", matched=["business analyst"], posted="2026-10-27",
                candidate=CANDIDATE, weights=PROFILE["ranking"], has_description=True, domain_hit=False,
                today=date(2026, 10, 28))
    plain = rank(a=analyze("Good English."), applicants=150, **base)
    edge = rank(a=analyze("Korean speaking is required."), applicants=10, **base)
    assert edge.interview > plain.interview
    assert edge.rank_score > plain.rank_score


def test_ineligible_has_no_rank():
    r = rank(fit_score=40, a=analyze("Fluent Cantonese required."), title="Product Manager", matched=[],
             posted="2026-10-27", applicants=None, candidate=CANDIDATE, weights=PROFILE["ranking"],
             has_description=True, domain_hit=False)
    assert r.rank_score is None


def test_parse_linkedin_detail_external_apply():
    html = """
    <div class="show-more-less-html__markup">Build GenAI tools.<br>2 years experience.</div>
    <figcaption class="num-applicants__caption">Over 200 applicants</figcaption>
    <code id="applyUrl" style="display: none"><!--"https://www.linkedin.com/jobs/view/externalApply/1?url=https%3A%2F%2Fcareers%2Eexample%2Ecom%2Fjob%2F9&urlHash=x"--></code>
    <ul><li class="description__job-criteria-item">
      <h3 class="description__job-criteria-subheader">Seniority level</h3>
      <span class="description__job-criteria-text">Entry level</span></li></ul>"""
    d = linkedin.parse_detail(html)
    assert d["description"].startswith("Build GenAI tools.")
    assert d["applicants"] == 200
    assert d["apply_url"] == "https://careers.example.com/job/9"
    assert d["apply_method"] == "external"
    assert d["seniority"] == "Entry level"


def make_db(tmp_path, jobs):
    conn = storage.connect(tmp_path / "jobs.db")
    scored = [score_job(j, PROFILE["scoring"], PROFILE["categories"]) for j in jobs]
    storage.save_jobs(conn, storage.start_run(conn, ["t"]), scored, {})
    return conn


def job(title, jid, desc=""):
    return Job("linkedin", jid, title, "Example Insurance", "HK", f"https://x/{jid}",
               posted=date.today().isoformat(), snippet=desc)


def test_daily_picks_skip_applied_and_ineligible(tmp_path):
    conn = make_db(tmp_path, [
        job("Associate Product Manager, GenAI", "1"),
        job("Junior Business Analyst", "2"),
        job("Product Owner", "3", "Fluent Cantonese required."),
        job("Product Analyst", "4"),
    ])
    for r in conn.execute("SELECT id, snippet FROM jobs WHERE source_job_id != '2'"):
        storage.save_details(conn, r["id"], {"description": r["snippet"] or "Good English."})
    pipeline.rank_all(conn, PROFILE)
    storage.set_status(conn, 4, "applied")
    picks = storage.pick_today(conn, 20)
    # 2 has no fetched page yet, 3 needs Cantonese, 4 was applied to.
    assert [r["source_job_id"] for r in picks] == ["1"]


def test_sync_round_trip_keeps_tracking(tmp_path):
    conn = make_db(tmp_path, [job("Associate Product Manager", "1"), job("Junior Business Analyst", "2"),
                              job("Product Owner", "3", "Fluent Cantonese required.")])
    for i in (1, 2, 3):
        storage.save_details(conn, i, {"description": conn.execute(
            "SELECT snippet FROM jobs WHERE id = ?", (i,)).fetchone()[0] or "Good English."})
    pipeline.rank_all(conn, PROFILE)
    storage.pick_today(conn, 20)
    result = storage.export_sync(conn, tmp_path / "out")
    assert result == {"new_job_documents": 3, "deletes": 0, "ranked": 3, "picks": 2, "batches": 1}
    batch = json.loads((tmp_path / "out" / "writes.json").read_text())[0]
    assert [w["collection"] for w in batch] == ["jobs", "jobs", "jobs", "runs"]  # run doc last
    assert all("if_version" not in w for w in batch)
    run = json.loads(Path(batch[-1]["file_path"]).read_text())
    cantonese = storage.doc_id("product owner|example insurance")
    assert run["ranks"][cantonese]["eligibility"] == "ineligible" and cantonese not in run["picks"]

    # Next morning: the dashboard's job docs come back as bare-field files; one job was applied to.
    state = tmp_path / "state"
    (state / "jobs").mkdir(parents=True)
    (state / "tracking").mkdir()
    for w in batch[:-1]:
        (state / "jobs" / f"{w['doc_id']}.json").write_text(Path(w["file_path"]).read_text())
    applied_id = storage.doc_id("associate product manager|example insurance")
    (state / "tracking" / f"{applied_id}.json").write_text(json.dumps({"status": "applied"}))

    fresh = storage.connect(tmp_path / "fresh.db")
    assert storage.import_state(fresh, state) == (3, 1)
    row = fresh.execute("SELECT * FROM jobs WHERE source_job_id = '1'").fetchone()
    assert row["status"] == "applied" and row["remote_exists"] == 1 and row["description"]
    storage.rescore(fresh, lambda j: score_job(j, PROFILE["scoring"], PROFILE["categories"]))
    pipeline.rank_all(fresh, PROFILE)
    assert [r["source_job_id"] for r in storage.pick_today(fresh, 20)] == ["2"]

    # Existing job documents are not rewritten; only the new run document is sent.
    result = storage.export_sync(fresh, tmp_path / "out2")
    assert result["new_job_documents"] == 0 and result["ranked"] == 3
    batch2 = json.loads((tmp_path / "out2" / "writes.json").read_text())[0]
    assert [w["collection"] for w in batch2] == ["runs"]


def test_sync_deletes_stale_untouched_jobs(tmp_path):
    from datetime import datetime, timedelta
    conn = make_db(tmp_path, [job("Junior Business Analyst", "2")])
    conn.execute("UPDATE jobs SET remote_exists = 1, last_seen = '2026-01-01T00:00:00', details_fetched_at = 'x'")
    conn.commit()
    batch = None
    storage.export_sync(conn, tmp_path / "out", now=datetime(2026, 10, 28))
    batch = json.loads((tmp_path / "out" / "writes.json").read_text())[0]
    assert batch[0] == {"op": "delete", "collection": "jobs",
                        "doc_id": storage.doc_id("junior business analyst|example insurance"), "if_version": 1}


def test_linkedin_offsite_apply_without_visible_url():
    html = """<div class="show-more-less-html__markup">Role.</div>
    <button data-modal="job-details-topcard-apply-modal">Apply
      <icon data-svg-class-name="apply-button__offsite-apply-icon-svg"></icon></button>
    <div class="contextual-sign-in-modal" data-impression-id="public_jobs_apply-link-offsite_contextual-sign-in-modal"></div>"""
    d = linkedin.parse_detail(html)
    assert d["apply_method"] == "external" and d["apply_url"] == ""


def test_jobsdb_details_use_search_summary_without_a_request():
    from jobhunt.sources import jobsdb
    d = jobsdb.fetch_details("95539362", "Support GTM strategy. 2-3 years' experience.")
    assert d["description"].startswith("Support GTM")
    assert d["apply_url"] == "https://hk.jobsdb.com/job/95539362/apply"


def test_picking_twice_a_day_replaces_picks(tmp_path):
    conn = make_db(tmp_path, [job(f"Business Analyst {i}", str(i)) for i in range(5)])
    for i in range(1, 6):
        storage.save_details(conn, i, {"description": "Good English. " * 40})
    pipeline.rank_all(conn, PROFILE)
    storage.pick_today(conn, 3)
    storage.pick_today(conn, 2)
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE pick_date IS NOT NULL").fetchone()[0] == 2
