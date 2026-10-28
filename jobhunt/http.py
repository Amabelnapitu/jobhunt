import random
import time
from urllib.parse import urlparse

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Accept-Language": "en-HK,en;q=0.9",
}

_session = requests.Session()
_session.headers.update(HEADERS)

# Hosts that told us to slow down this run. We stop asking them rather than work around it.
_backed_off: set[str] = set()


class RateLimited(Exception):
    pass


def get(url: str, params: dict | None = None, retries: int = 3) -> requests.Response:
    """GET with a polite delay and backoff. A site that keeps rate-limiting (HTTP 429, or
    LinkedIn's 999) is left alone for the rest of the run."""
    host = urlparse(url).netloc
    if host in _backed_off:
        raise RateLimited(f"{host} asked us to slow down earlier this run")
    for attempt in range(retries):
        time.sleep(1.0 + random.random())
        resp = _session.get(url, params=params, timeout=30)
        if resp.status_code in (429, 999):
            time.sleep(10 * (attempt + 1))
            continue
        if resp.status_code >= 500:
            time.sleep(5 * (attempt + 1))
            continue
        resp.raise_for_status()
        return resp
    if resp.status_code in (429, 999):
        _backed_off.add(host)
        raise RateLimited(f"{host} is rate-limiting; skipping it for the rest of this run")
    resp.raise_for_status()
    return resp
