"""Turn a scored job plus its description analysis into eligibility, interview odds and a rank.

rank_score (0-100) = weighted mix of
  fit        - how well the role matches the profile (keyword score, normalized)
  interview  - how likely an application gets an interview (see interview_score)
  freshness  - newer postings first (applying early gets noticed)
Ineligible jobs get no rank. "check" jobs (unclear or stretch) are discounted.
"""

from dataclasses import dataclass, field
from datetime import date

from .analysis import Analysis

ELIGIBLE, CHECK, INELIGIBLE = "eligible", "check", "ineligible"
_JUNIOR_WORDS = ("junior", "associate", "assistant", "analyst", "graduate", "trainee", "entry")
_SENIOR_WORDS = ("senior", "sr", "lead", "head", "director", "principal", "vp", "vice president", "chief")


@dataclass
class Ranked:
    eligibility: str
    reasons: list[str] = field(default_factory=list)
    interview: int = 0
    freshness: int = 0
    rank_score: float | None = None


def _words(title: str) -> set[str]:
    return set(title.lower().replace(",", " ").replace("-", " ").replace("/", " ").split())


def eligibility(a: Analysis, title: str, candidate: dict, has_description: bool) -> tuple[str, list[str]]:
    langs = candidate.get("languages", {})
    qualified = candidate.get("max_years_qualified", 1)
    stretch = candidate.get("max_years_stretch", 2)
    hard, soft = [], []

    if a.languages.get("cantonese") == "required" and langs.get("cantonese", "none") == "none":
        hard.append("Requires Cantonese")
    if a.languages.get("chinese_written") == "required" and langs.get("written_chinese", "none") == "none":
        hard.append("Requires written Chinese")
    if a.languages.get("mandarin") == "required" and langs.get("mandarin") in (None, "none", "conversational", "basic"):
        hard.append("Requires fluent Mandarin")
    if a.languages.get("bilingual_unspecified"):
        soft.append("Asks for 'bilingual': check which languages")
    if a.pr_only and not candidate.get("permanent_resident", False):
        hard.append("HK permanent residents only")

    if a.years_required is not None:
        if a.years_required > stretch:
            hard.append(f"Requires {a.years_required}+ years' experience")
        elif a.years_required > qualified:
            soft.append(f"Requires {a.years_required} years' experience (stretch)")

    if _words(title) & {"director", "head", "chief", "vp"}:
        hard.append("Executive-level title")
    if not has_description:
        soft.append("Description not fetched yet")

    if hard:
        return INELIGIBLE, hard + soft
    return (CHECK if soft else ELIGIBLE), soft


def freshness(posted: str, today: date) -> int:
    """100 for today, falling linearly to 0 at 30 days. Unknown dates count as 2 weeks old."""
    try:
        age = (today - date.fromisoformat(posted[:10])).days
    except ValueError:
        age = 14
    return max(0, min(100, round(100 - age * 100 / 30)))


def interview_score(a: Analysis, title: str, matched: list[str], applicants: int | None,
                    candidate: dict, domain_hit: bool) -> int:
    """Heuristic 0-100: odds this candidate gets a callback. Each factor is explainable."""
    s = 50
    qualified = candidate.get("max_years_qualified", 1)
    if a.years_required is None:
        s += 5
    elif a.years_required <= qualified:
        s += 15
    else:
        s -= 10
    words = _words(title)
    if words & set(_JUNIOR_WORDS):
        s += 10
    if words & set(_SENIOR_WORDS):
        s -= 15
    positive = [m for m in matched if not m.startswith("-")]
    s += 10 if len(positive) >= 5 else 5 if len(positive) >= 3 else 0
    if applicants is not None:
        s += 15 if applicants < 25 else 5 if applicants < 100 else -10 if applicants >= 200 else 0
    # Languages few applicants have are a real edge in HK.
    for rare in ("korean", "bahasa"):
        if rare in a.languages and candidate.get("languages", {}).get(rare, "none") != "none":
            s += 15
    for lang in ("cantonese", "mandarin", "chinese_written"):
        if a.languages.get(lang) == "preferred":
            s -= 8
    if a.agency:
        s -= 5
    if domain_hit:  # insurance/actuarial background stands out for these employers
        s += 5
    return max(0, min(100, s))


def rank(fit_score: int, a: Analysis, title: str, matched: list[str], posted: str,
         applicants: int | None, candidate: dict, weights: dict, has_description: bool,
         domain_hit: bool, today: date | None = None) -> Ranked:
    status, reasons = eligibility(a, title, candidate, has_description)
    r = Ranked(status, reasons)
    r.interview = interview_score(a, title, matched, applicants, candidate, domain_hit)
    r.freshness = freshness(posted, today or date.today())
    if status == INELIGIBLE:
        return r
    fit = max(0, min(100, fit_score * 100 / weights.get("fit_full_marks", 50)))
    score = (weights.get("fit", 0.40) * fit + weights.get("interview", 0.35) * r.interview
             + weights.get("freshness", 0.25) * r.freshness)
    if status == CHECK:
        score *= weights.get("check_factor", 0.85)
    r.rank_score = round(score, 1)
    return r
