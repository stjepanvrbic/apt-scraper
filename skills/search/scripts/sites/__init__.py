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

from urllib.parse import urlsplit

from sites import zillow

SITES = (zillow,)


def site_for(url):
    host = (urlsplit(url).hostname or '').lower()
    return next((site for site in SITES if host in site.HOSTS), None)


def site_named(name):
    return next((site for site in SITES if site.__name__.rsplit('.', 1)[-1] == name), None)
