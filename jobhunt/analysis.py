"""Read a job description and pull out what matters for applying: documents and info the
employer asks for, language requirements, years of experience, and residency limits.

Everything here is keyword/regex based, so it reports what the text *says*; anything it
can't classify is surfaced as "check" rather than guessed.
"""

import re
from dataclasses import dataclass, field

# (label, pattern) pairs. A CV is assumed for every application.
DOCUMENTS = [
    ("Cover letter", r"cover(?:ing)? letter"),
    ("Academic transcript", r"transcripts?"),
    ("Portfolio / work samples", r"portfolio|work samples?|writing samples?"),
    ("References", r"\breferees?\b|references? (?:from|of|letters?|available|upon)|(?:provide|two|2) references"),
    ("Copies of certificates", r"copies of (?:academic |professional )?(?:certificates?|qualifications?)"),
    ("Recent photo", r"recent photo|photograph"),
]
INFO = [
    ("Current salary", r"current (?:and expected )?salary|present salary|current package"),
    ("Expected salary", r"expected salary|salary expectations?|expected package"),
    ("Availability / notice period", r"availability|available date|notice period|date available"),
    ("Right to work / visa status", r"right to work|work permit|visa status|eligib\w+ to work"),
]

_PREFERRED = r"prefer|advantage|a plus|\bplus\b|nice to have|desirable|bonus|beneficial|ideally"
_SENTENCE_SPLIT = re.compile(r"(?<=[.;!?\n•·▪●])\s*|\s+-\s+")

_YEARS = re.compile(
    r"(?:minimum (?:of )?|at least |over )?(\d{1,2})\s*(?:\+|-\s*\d{1,2}|to \d{1,2}|or more)?\s*"
    r"(?:years?|yrs?)['’]?\s*(?:of\s+)?(?:[a-z/&-]+\s+){0,3}?experience",
    re.I,
)
_PR_ONLY = re.compile(
    r"permanent resident|permanent (?:hk )?identity card|hkpr\b|right of abode|local candidates only", re.I)

AGENCIES = ("michael page", "robert walters", "hays", "randstad", "morgan mckinley", "page personnel",
            "adecco", "manpower", "kelly services", "pagegroup", "harvey nash", "robert half",
            "links international", "ambition", "venturi", "charterhouse", "jobs on demand")


@dataclass
class Analysis:
    documents: list[str] = field(default_factory=lambda: ["CV / resume"])
    info_needed: list[str] = field(default_factory=list)
    # language -> "required" | "preferred"; "chinese_written" covers reading/writing Chinese.
    languages: dict[str, str] = field(default_factory=dict)
    years_required: int | None = None
    pr_only: bool = False
    agency: bool = False


def _sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_SPLIT.split(text) if s and s.strip()]


def _languages(text: str) -> dict[str, str]:
    found: dict[str, str] = {}

    def note(lang: str, level: str) -> None:
        if found.get(lang) != "required":  # required wins over preferred
            found[lang] = level

    for sentence in _sentences(text):
        s = sentence.lower()
        level = "preferred" if re.search(_PREFERRED, s) else "required"
        if "trilingual" in s:
            note("cantonese", level)
            note("mandarin", level)
        if "cantonese" in s:
            note("cantonese", level)
        if re.search(r"mandarin|putonghua", s):
            # Conversational/basic Mandarin is fine for this candidate; fluent/native is not.
            if level == "required" and not re.search(r"fluent|native|excellent|proficien|strong|good command|business", s):
                level = "preferred"
            note("mandarin", level)
        if re.search(r"written chinese|chinese writing|read(?:ing)? (?:and|&) writ\w+ (?:in )?chinese|"
                     r"traditional chinese|simplified chinese|chinese (?:reading|writing)|"
                     r"(?:english|cantonese) and chinese", s):
            note("chinese_written", level)
        if re.search(r"\bbilingual\b", s) and not re.search(r"cantonese|mandarin|putonghua|chinese", s):
            note("bilingual_unspecified", level)
        if "korean" in s:
            note("korean", level)
        if re.search(r"bahasa|indonesian", s):
            note("bahasa", level)
    return found


def _years(text: str) -> int | None:
    found = [int(m.group(1)) for m in _YEARS.finditer(text)]
    found = [y for y in found if y <= 20]
    return max(found) if found else None


def analyze(description: str, company: str = "") -> Analysis:
    text = description or ""
    lower = text.lower()
    a = Analysis()
    a.documents += [label for label, pat in DOCUMENTS if re.search(pat, lower)]
    a.info_needed = [label for label, pat in INFO if re.search(pat, lower)]
    a.languages = _languages(text)
    a.years_required = _years(text)
    a.pr_only = bool(_PR_ONLY.search(text))
    a.agency = any(name in company.lower() for name in AGENCIES)
    return a
