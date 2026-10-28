import random
import time

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


def get(url: str, params: dict | None = None, retries: int = 3) -> requests.Response:
    """GET with a polite delay and simple backoff on 429/5xx."""
    for attempt in range(retries):
        time.sleep(1.0 + random.random())
        resp = _session.get(url, params=params, timeout=30)
        if resp.status_code == 429 or resp.status_code >= 500:
            time.sleep(5 * (attempt + 1))
            continue
        resp.raise_for_status()
        return resp
    resp.raise_for_status()
    return resp
