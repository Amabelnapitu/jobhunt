import copy

from jobhunt import cv

BASE = {
    "variant": "T",
    "header": {"name": "DOE, Jane", "lines": ["jane@example.com"]},
    "sections": [
        {"title": "SUMMARY", "text": "Analyst with a Finance background using Python and SQL."},
        {"title": "WORK EXPERIENCE", "entries": [{
            "org": "Acme Bank", "location": "Hong Kong", "role": "Analyst", "dates": "Jan 2024 – Jun 2024",
            "bullets": ["Automated Excel workflows, cutting refresh time by ~30%.",
                        "Coordinated 4 teams across Finance and Risk."]}]},
        {"title": "LEADERSHIP EXPERIENCE", "entries": [{
            "org": "Chess Club", "role": "Secretary", "dates": "2022", "bullets": ["Ran weekly events."]}]},
    ],
}
CORPUS = "\n".join(cv.texts(BASE)) + "\nBuilt a Power BI dashboard used by 12 actuaries."


def tailor(**changes):
    t = copy.deepcopy(BASE)
    for path, value in changes.items():
        if path == "bullet0":
            t["sections"][1]["entries"][0]["bullets"][0] = value
        elif path == "summary":
            t["sections"][0]["text"] = value
        elif path == "drop_leadership":
            t["sections"] = t["sections"][:2]
    return t


def test_rephrasing_with_sourced_facts_passes():
    t = tailor(bullet0={"text": "Built a Power BI dashboard used by 12 actuaries.", "change": 0},
               drop_leadership=True)
    r = cv.check(t, BASE, CORPUS, droppable=("LEADERSHIP EXPERIENCE",))
    assert r["ok"], r["problems"]


def test_invented_number_and_tool_are_flagged():
    t = tailor(bullet0="Automated Tableau workflows, cutting refresh time by ~45%.")
    r = cv.check(t, BASE, CORPUS)
    assert not r["ok"]
    assert r["unsourced"] == ["Tableau", "~45%"]


def test_sentence_start_words_are_not_terms():
    t = tailor(bullet0="Streamlined Excel workflows, cutting refresh time by ~30%.")
    assert cv.unsourced(t, CORPUS) == []


def test_longer_cv_fails_word_budget():
    t = tailor(summary=BASE["sections"][0]["text"] + " Analyst using Python and SQL for Finance.")
    r = cv.check(t, BASE, CORPUS)
    assert any("more than the base" in p for p in r["problems"])


def test_section_rules():
    t = tailor(drop_leadership=True)
    assert not cv.check(t, BASE, CORPUS)["ok"]                      # not allowed by default
    t["sections"].append({"title": "AWARDS", "text": "None."})
    problems = cv.check(t, BASE, CORPUS, droppable=("LEADERSHIP EXPERIENCE",))["problems"]
    assert problems == ["New sections not in the base CV: AWARDS"]


def test_header_must_not_change():
    t = tailor()
    t["header"]["lines"] = ["other@example.com"]
    assert "Header differs from the base CV" in cv.check(t, BASE, CORPUS)["problems"]
