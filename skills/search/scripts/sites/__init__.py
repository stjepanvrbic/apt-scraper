"""Listing sites the search can read, one module per site.

A site module has NAME, HOSTS and these functions, all taking the polite HTTP client:

  searches(criteria, client, regions)  one search per criteria area, in criteria order; regions caches
                                        area lookups between runs and is updated in place
  pages(search, client)                 the search's result pages in the site's own order, each a list of
                                        cards: dicts with id, url and signature, a hash of what the card shows
  read(card, client)                    (listing, facts) for the store and matching.py, or raises Gone
  reread(listing, client)               the same for a stored listing, from its source link

A search is a dict with the criteria area it covers under 'area'. Every listing id starts with the
site module's name and a colon. Failures are the exceptions in sites.errors.
"""

from collections.abc import Iterator
from typing import Protocol
from urllib.parse import urlsplit

from client import Fetcher
from matching import Facts
from shapes import Criteria
from sites import zillow
from sites.zillow import Card, Search, SiteListing, SourcedListing


class Site(Protocol):
    """A site module, as the search uses it."""

    __name__: str

    @property
    def NAME(self) -> str: ...

    @property
    def HOSTS(self) -> set[str]: ...

    def searches(self, criteria: Criteria, client: Fetcher, regions: dict[str, object]) -> list[Search]: ...

    def pages(self, search: Search, client: Fetcher) -> Iterator[list[Card]]: ...

    def read(self, card: Card, client: Fetcher) -> tuple[SiteListing, Facts]: ...

    def reread(self, listing: SourcedListing, client: Fetcher) -> tuple[SiteListing, Facts]: ...


SITES: tuple[Site, ...] = (zillow,)


def site_for(url: str) -> Site | None:
    host = (urlsplit(url).hostname or '').lower()
    return next((site for site in SITES if host in site.HOSTS), None)


def site_named(name: str) -> Site | None:
    return next((site for site in SITES if site.__name__.rsplit('.', 1)[-1] == name), None)
