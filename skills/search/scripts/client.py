"""HTTP for the listing sites, paced like a person reading results.

Listing sites block clients whose TLS handshake does not look like a browser's, before any page is
served. curl_cffi sends Chrome's handshake, so a plain fetch gets the page a browser would. Requests
are spaced by a random pause, and one session keeps the site's cookies for the whole run.
"""

import random
import time
from typing import Protocol

PAUSE_SECONDS = (4.0, 9.0)
TIMEOUT_SECONDS = 30
HEADERS = {'Accept-Language': 'en-US,en;q=0.9'}


class Fetcher(Protocol):
    """What a site module needs of the client: the status code and text of a page."""

    def get(self, url: str) -> tuple[int, str]: ...


class Client:
    def __init__(self, pause: tuple[float, float] = PAUSE_SECONDS) -> None:
        from curl_cffi import requests

        self.errors: type[requests.RequestsError] = requests.RequestsError
        self.session: requests.Session = requests.Session(impersonate='chrome')
        self.pause: tuple[float, float] = pause
        self.last: float | None = None

    def get(self, url: str) -> tuple[int, str]:
        if self.last is not None:
            time.sleep(max(0.0, self.last + random.uniform(*self.pause) - time.monotonic()))
        try:
            response = self.session.get(url, headers=HEADERS, timeout=TIMEOUT_SECONDS)
        except self.errors as error:
            raise ConnectionError(f'{url} could not be reached: {error}') from None
        finally:
            self.last = time.monotonic()
        return response.status_code, response.text
