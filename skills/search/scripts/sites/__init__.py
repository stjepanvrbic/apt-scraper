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
from typing import Protocol, TypedDict
from urllib.parse import urlsplit

from client import Fetcher
from code_features import Facts
from shapes import Criteria
from sites import zillow
from sites.zillow import SiteListing, SourcedListing


class AreaSearch(TypedDict):
    """A site's search: the criteria area it covers, and whatever the site's own pages need."""
    area: str


class SiteCard(TypedDict):
    """A listing as a site's results page shows it: its id, its page and a signature of what the card showed."""
    id: str
    url: str
    signature: str


class Site[S: AreaSearch, C: SiteCard](Protocol):
    """A site module, as the search uses it, with its own search and card shapes."""

    __name__: str

    @property
    def NAME(self) -> str: ...

    @property
    def HOSTS(self) -> set[str]: ...

    def searches(self, criteria: Criteria, client: Fetcher, regions: dict[str, object], /) -> list[S]: ...

    def pages(self, search: S, client: Fetcher, /) -> Iterator[list[C]]: ...

    def read(self, card: C, client: Fetcher, /) -> tuple[SiteListing, Facts]: ...

    def reread(self, listing: SourcedListing, client: Fetcher, /) -> tuple[SiteListing, Facts]: ...


SITES: tuple[Site[zillow.Search, zillow.Card], ...] = (zillow,)


def site_for(url: str) -> Site[zillow.Search, zillow.Card] | None:
    host = (urlsplit(url).hostname or '').lower()
    return next((site for site in SITES if host in site.HOSTS), None)


def site_named(name: str) -> Site[zillow.Search, zillow.Card] | None:
    return next((site for site in SITES if site.__name__.rsplit('.', 1)[-1] == name), None)
