from jobhunt.lanes import ats, lane, resolve
from jobhunt.sources.linkedin import parse_detail


def test_resolve_unwraps_linkedin_redirect():
    url = "https://www.linkedin.com/safety/go/?url=https%3A%2F%2Fjob-boards%2Egreenhouse%2Eio%2Fokx%2Fjobs%2F1&urlhash=x"
    assert resolve(url) == "https://job-boards.greenhouse.io/okx/jobs/1"


def test_plain_forms_are_auto():
    assert ats("https://job-boards.greenhouse.io/okx/jobs/7821266003")[1] == "auto"
    assert ats("https://jobs.ashbyhq.com/bjakcareer/6a711a8f/application")[:2] == ("Ashby", "auto")
    assert ats("https://jobs.lever.co/acme/0f0e0d0c-0000-0000-0000-000000000000")[1] == "auto"


def test_sign_in_sites_are_yours():
    for url in ("https://tsys.wd1.myworkdayjobs.com/TSYS/job/x/apply",
                "https://careers-en-axa.icims.com/jobs/26970/job",
                "https://hdid.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/job/4940",
                "https://career5.successfactors.eu/careers?company=AZGROUPPROD",
                "https://talentjobseeker.hkstp.org/job/104123/Business-Analyst",
                "https://synpulse.careers.hibob.com/jobs/abc"):
        assert ats(url)[1] == "you", url


def test_lane_rules():
    assert lane("external", "https://job-boards.greenhouse.io/okx/jobs/1", "Hong Kong SAR", "eligible")["lane"] == "auto"
    assert lane("linkedin_easy_apply", "", "Hong Kong", "eligible")["lane"] == "you"
    assert lane("jobsdb", "https://hk.jobsdb.com/job/1/apply", "Kowloon", "check")["lane"] == "you"
    assert lane("external", "", "Hong Kong", "eligible", closed=True)["lane_reason"] == "Posting closed"
    assert lane("external", "", "Frankfurt, Germany", "eligible")["lane"] == "skip"
    assert lane("external", "", "Hong Kong", "ineligible")["lane"] == "skip"
    # LinkedIn hides the employer link when logged out; the worker resolves it.
    assert lane("external", "https://hk.linkedin.com/jobs/view/1", "Hong Kong", "eligible")["lane"] == "auto"


def test_linkedin_closed_posting_is_detected():
    html = '<div class="description__text">Great job</div><span>No longer accepting applications</span>'
    assert parse_detail(html)["closed"] is True
    assert parse_detail('<div class="description__text">Open</div>')["closed"] is False


def test_clock_is_hong_kong_time():
    from datetime import datetime, timedelta, timezone
    from jobhunt import clock
    expected = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=8)
    assert abs(clock.now() - expected) < timedelta(seconds=5)
