from dataclasses import dataclass, fields


@dataclass
class Job:
    source: str
    job_id: str
    title: str
    company: str
    location: str
    url: str
    posted: str = ""      # ISO date (YYYY-MM-DD) when known
    salary: str = ""
    snippet: str = ""
    query: str = ""       # the search query that found it
    score: int = 0
    matched: str = ""     # keywords that contributed to the score
    first_seen: str = ""  # filled in by storage when merging with earlier runs

    @property
    def key(self) -> tuple[str, str]:
        """Identity used to dedupe the same posting across queries and sources."""
        return (normalize(self.title), normalize(self.company))

    @classmethod
    def columns(cls) -> list[str]:
        return [f.name for f in fields(cls)]


def normalize(text: str) -> str:
    return " ".join(text.lower().split())
