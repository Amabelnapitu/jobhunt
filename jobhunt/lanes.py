"""Sort each job into the lane it will be applied through, decided from where it is hosted.

  auto  the apply worker submits it unattended (plain forms with a real file upload)
  you   you finish it: the site needs your sign-in or account, has a bot check, or (LinkedIn
        Easy Apply) the CV can't be attached by the browser tool
  skip  not worth anyone's time: closed, not in Hong Kong, or not eligible

What we learned applying by hand decides the host lists below; add a host when a new one
turns up.
"""

from urllib.parse import parse_qs, unquote, urlparse

AUTO_HOSTS = {
    "greenhouse.io": "Greenhouse",
    "lever.co": "Lever",
    "ashbyhq.com": "Ashby",
}
YOU_HOSTS = {
    "myworkdayjobs.com": ("Workday", "needs your Workday account"),
    "icims.com": ("iCIMS", "needs your iCIMS sign-in"),
    "oraclecloud.com": ("Oracle", "creates a candidate profile under your email"),
    "successfactors.com": ("SuccessFactors", "needs your SuccessFactors sign-in"),
    "successfactors.eu": ("SuccessFactors", "needs your SuccessFactors sign-in"),
    "taleo.net": ("Taleo", "needs your Taleo account"),
    "hkstp.org": ("HKSTP", "needs your HKSTP OnePass sign-in"),
    "hibob.com": ("HiBob", "the CV upload has to be done by you"),
    "careers.hkjc.com": ("Paradox chatbot", "applies through a chatbot with screening questions"),
    "jobsdb.com": ("JobsDB", "JobsDB shows a bot check"),
}
HK_WORDS = ("hong kong", "kowloon", "hk", "remote")


def _host(url: str) -> str:
    host = urlparse(url or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host


def resolve(url: str) -> str:
    """Unwrap LinkedIn's safety redirect so the real host can be seen."""
    if "linkedin.com/safety/go" in (url or ""):
        target = parse_qs(urlparse(url).query).get("url")
        if target:
            return unquote(target[0])
    return url or ""


def ats(url: str) -> tuple[str, str, str]:
    """(platform, lane, reason) for an application page."""
    host = _host(resolve(url))
    for suffix, name in AUTO_HOSTS.items():
        if host == suffix or host.endswith("." + suffix):
            return name, "auto", ""
    for suffix, (name, reason) in YOU_HOSTS.items():
        if host == suffix or host.endswith("." + suffix):
            return name, "you", reason
    if "linkedin.com" in host:
        # Logged out, LinkedIn hides the employer's link; the worker finds it when it applies.
        return "LinkedIn link", "auto", ""
    return (host or "unknown"), "auto", ""


def lane(apply_method: str, apply_url: str, location: str, eligibility: str,
         closed: bool = False) -> dict:
    """The lane for one job, with a short reason shown on the dashboard."""
    if closed:
        return {"lane": "skip", "ats": "", "lane_reason": "Posting closed"}
    if eligibility == "ineligible":
        return {"lane": "skip", "ats": "", "lane_reason": "Not eligible"}
    loc = (location or "").lower()
    if loc and not any(w in loc for w in HK_WORDS):
        return {"lane": "skip", "ats": "", "lane_reason": f"Based in {location}, not Hong Kong"}
    if apply_method == "linkedin_easy_apply":
        return {"lane": "you", "ats": "LinkedIn Easy Apply",
                "lane_reason": "LinkedIn Easy Apply: upload the tailored CV yourself"}
    if apply_method == "jobsdb":
        return {"lane": "you", "ats": "JobsDB", "lane_reason": "JobsDB shows a bot check"}
    name, which, reason = ats(apply_url)
    return {"lane": which, "ats": name, "lane_reason": reason}
