from jobhunt.sources import boards


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


def test_board_keeps_hong_kong_jobs_and_closes_missing_ones(monkeypatch):
    listing = {"jobs": [
        {"id": 1, "title": "Product Manager", "location": {"name": "Hong Kong"},
         "absolute_url": "https://job-boards.greenhouse.io/okx/jobs/1", "first_published": "2026-10-20T00:00:00Z",
         "content": "&lt;p&gt;Own the roadmap&lt;/p&gt;"},
        {"id": 2, "title": "Product Manager", "location": {"name": "Singapore"},
         "absolute_url": "https://job-boards.greenhouse.io/okx/jobs/2", "content": ""},
    ]}
    monkeypatch.setattr(boards, "get", lambda url, params=None: FakeResponse(listing))
    monkeypatch.setattr(boards, "BOARDS", [("greenhouse", "okx", "OKX")])
    monkeypatch.setattr(boards, "_COMPANY", {("greenhouse", "okx"): "OKX"})
    monkeypatch.setattr(boards, "_read", False)
    monkeypatch.setattr(boards, "_details", {})
    monkeypatch.setattr(boards, "_listings", {})

    jobs = list(boards.search("product manager", "Hong Kong", 14, 1))
    assert [(j.job_id, j.company, j.posted) for j in jobs] == [("greenhouse:okx:1", "OKX", "2026-10-20")]
    assert boards.fetch_details("greenhouse:okx:1")["description"] == "Own the roadmap"
    assert list(boards.search("analyst", "Hong Kong", 14, 1)) == []  # read once per run
    assert boards.fetch_details("greenhouse:okx:99")["closed"] is True
