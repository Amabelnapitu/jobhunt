from dataclasses import dataclass


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

    @property
    def key(self) -> tuple[str, str]:
        """Identity used to dedupe the same posting across queries and sources."""
        return (normalize(self.title), normalize(self.company))


def normalize(text: str) -> str:
    return " ".join(text.lower().split())
