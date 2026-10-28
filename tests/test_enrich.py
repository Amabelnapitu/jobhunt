import json
from pathlib import Path

from jobhunt import enrich, storage
from jobhunt.models import Job

LONG = "Own the roadmap for our GenAI platform. " * 20


def test_parse_jsonld_job_posting():
    page = """<html><script type="application/ld+json">
      {"@context": "https://schema.org", "@graph": [{"@type": "Organization"},
       {"@type": "JobPosting", "title": "APM", "datePosted": "2026-10-20T00:00:00Z",
        "description": "&lt;p&gt;Build &lt;b&gt;AI&lt;/b&gt; tools.&lt;/p&gt;&lt;ul&gt;&lt;li&gt;1 year experience&lt;/li&gt;&lt;/ul&gt;"}]}
    </script></html>"""
    found = enrich.parse_jsonld(page)
    assert found["posted"] == "2026-10-20"
    assert found["description"].split("\n") == ["Build", "AI", "tools.", "1 year experience"]


def test_same_job_matching():
    assert enrich.same_job("Associate Product Manager (GenAI)", "FWD Insurance",
                           "Associate Product Manager - GenAI", "FWD Life Insurance Company")
    assert not enrich.same_job("Associate Product Manager", "FWD", "Associate Product Manager", "AIA")
    assert not enrich.same_job("Product Manager", "Example Bank", "Data Engineer", "Example Bank")


def test_greenhouse_uses_public_api(monkeypatch):
    calls = []

    class Resp:
        def json(self):
            return {"content": "&lt;p&gt;Full description&lt;/p&gt;"}

    monkeypatch.setattr(enrich, "get", lambda url: calls.append(url) or Resp())
    found = enrich.from_employer_page("https://job-boards.greenhouse.io/examplecorp/jobs/12345?gh_src=li")
    assert calls == ["https://boards-api.greenhouse.io/v1/boards/examplecorp/jobs/12345"]
    assert found["description"] == "Full description"


def test_easy_apply_job_gets_jobsdb_route_and_description(tmp_path, monkeypatch):
    conn = storage.connect(tmp_path / "jobs.db")
    li = Job("linkedin", "99", "Associate Product Manager", "Example Insurance", "HK", "https://li/99")
    storage.save_jobs(conn, storage.start_run(conn, ["t"]), [li], {})
    storage.save_details(conn, 1, {"description": "Short teaser.", "apply_url": "https://li/99",
                                   "apply_method": "linkedin_easy_apply"})
    monkeypatch.setattr(enrich, "find_on_jobsdb", lambda title, company: (
        "https://hk.jobsdb.com/job/555",
        {"description": LONG, "apply_url": "https://hk.jobsdb.com/job/555/apply", "apply_method": "jobsdb"}))
    assert enrich.enrich(conn) == 1
    r = conn.execute("SELECT * FROM jobs").fetchone()
    assert r["description"] == LONG
    assert (r["apply_method"], r["apply_url"]) == ("jobsdb", "https://hk.jobsdb.com/job/555/apply")
    assert json.loads(r["alt_urls"]) == ["https://hk.jobsdb.com/job/555"]
    assert enrich.enrich(conn) == 0  # not looked up twice


def test_failed_lookup_keeps_job_as_is(tmp_path, monkeypatch):
    conn = storage.connect(tmp_path / "jobs.db")
    li = Job("linkedin", "7", "Business Analyst", "Example Bank", "HK", "https://li/7")
    storage.save_jobs(conn, storage.start_run(conn, ["t"]), [li], {})
    storage.save_details(conn, 1, {"description": "Short.", "apply_url": "https://careers.example.com/7",
                                   "apply_method": "external"})

    def boom(*a):
        raise RuntimeError("403")
    monkeypatch.setattr(enrich, "from_employer_page", boom)
    monkeypatch.setattr(enrich, "find_on_jobsdb", lambda *a: None)
    assert enrich.enrich(conn) == 0
    r = conn.execute("SELECT * FROM jobs").fetchone()
    assert r["description"] == "Short." and r["apply_url"] == "https://careers.example.com/7"


def test_rate_limited_host_is_left_alone_for_the_run(monkeypatch):
    import pytest
    from jobhunt import http

    class Resp:
        status_code = 999

    calls = []
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    monkeypatch.setattr(http._session, "get", lambda url, **kw: calls.append(url) or Resp())
    monkeypatch.setattr(http, "_backed_off", set())
    with pytest.raises(http.RateLimited):
        http.get("https://www.linkedin.com/jobs-guest/x")
    assert len(calls) == 3
    with pytest.raises(http.RateLimited):
        http.get("https://www.linkedin.com/jobs-guest/y")
    assert len(calls) == 3  # no further requests to that host
