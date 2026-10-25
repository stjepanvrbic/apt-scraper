"""HTTP for the listing sites, paced like a person reading results.

Listing sites block clients whose TLS handshake does not look like a browser's, before any page is
served. curl_cffi sends Chrome's handshake, so a plain fetch gets the page a browser would. Requests
are spaced by a random pause, and one session keeps the site's cookies for the whole run.
"""

import random
import time

PAUSE_SECONDS = (4.0, 9.0)
TIMEOUT_SECONDS = 30
HEADERS = {'Accept-Language': 'en-US,en;q=0.9'}


class Client:
    def __init__(self, pause=PAUSE_SECONDS):
        from curl_cffi import requests

        self.errors = requests.RequestsError
        self.session = requests.Session(impersonate='chrome')
        self.pause = pause
        self.last = None

    def get(self, url):
        if self.last is not None:
            time.sleep(max(0.0, self.last + random.uniform(*self.pause) - time.monotonic()))
        try:
            response = self.session.get(url, headers=HEADERS, timeout=TIMEOUT_SECONDS)
        except self.errors as error:
            raise ConnectionError(f'{url} could not be reached: {error}') from None
        finally:
            self.last = time.monotonic()
        return response.status_code, response.text
