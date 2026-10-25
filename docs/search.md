# Apartment search

The search reads the user's listing sites every hour and keeps the listing store current. It adds new listings, updates changed ones and marks gone ones unavailable. The gallery shows what it finds. Zillow is the only listing site it can read so far.

## How it runs

The profile ships the Hermes cron job `apt-scraper-search`, named "Listing search", in `profile/cron/jobs.json`. Hermes installs a shipped job paused, and the installers resume it. It runs every hour. A run before the interview is complete searches nothing. Once a run has searched a listing site, later runs need only `criteria` and `listing_sites`, so settings the interview gains later never stop a running search (`SearchState.missing_settings`). When the interview becomes complete, the agent runs the job once with its `cronjob_manage` tool, so the first listings do not wait for the hour, as `profile/SOUL.md` and the [search skill](../profile/skills/search/SKILL.md) tell it to. Hermes runs scheduled jobs only while its gateway runs. `search.py status` reports a search that has not run for three hours, and the agent then checks the job and the gateway.

Each run has two parts. Hermes first runs `profile/scripts/search-run.py`, which the profile installs into `HERMES_HOME/scripts`, the only place Hermes takes cron scripts from. While the search waits for the interview it stops there. Otherwise it runs `search.py run` from the search skill, in a Python environment of its own under `HERMES_HOME/workspace/search/environment`. The installers build that environment with `search-run.py install`, using the Python `hermes pm env` lists, from the wheels pinned with their hashes in `profile/skills/search/requirements.txt`. A failed build stops the installer. The cron run rebuilds the environment only when Hermes's Python or the requirements changed. The search prints a report. Its last line is `{"wakeAgent": false}` when no listing waits for a judgment, and Hermes then ends the run without starting the agent, so the run makes no model call. Otherwise Hermes starts the agent with the report in its prompt, and the agent judges the waiting listings.

Hermes starts the agent whenever a cron script fails, to report the failure. So `search-run.py` never fails. A missing environment, a failed rebuild or a search that stopped is recorded instead, and ends the run with `{"wakeAgent": false}`. `search.py status` reports it, and the agent tells the user at the start of the next conversation. Inside a run, a listing page, result page, area or site that fails is recorded in the run's report, with up to ten messages per site, and the run goes on with the rest.

A run stops reading after 25 minutes, so a run ends before the next one is due. What it did not reach waits for the next run.

## What a run reads

For each site in `listing_sites` and each area in `criteria.location`, the run reads the site's search results in the site's own order and with the criteria the site can filter by. It reads the first result page of every area, then the second page of every area, and so on, so each site's highest-placed listings are read first. Within a query split into price bands, as described under Zillow, the query's own first page comes first, then the first page of every band, then the later pages of the bands in turn.

A search result card that the run has seen before, unchanged, costs nothing more than its result page. The run keeps a hash of what each card shows: price, units, rooms, size, availability date, address and photos, together with the site reader's version, so a reader that reads new facts opens every stored listing once more. When the hash differs, or the card is new, the run opens the listing page and saves the listing hidden with `upsert_listing`. At its end the run applies the criteria then saved to every stored listing, in one store transaction, so a listing saved during the run shows only if it meets them.

When a run has read every result page of every area a stored listing was found under, and that listing was not among the results, the run opens the listing's page. A page that shows the listing as no longer for rent marks it unavailable. Its notes, favorite, correspondence and viewings stay. If the listing is still for rent, the run saves its current content, and the criteria decide whether it stays shown. A listing that returns to the results is available again. At most 20 listings are checked this way per run.

## Criteria and must-haves

The run stores facts with each listing as its features, tied to the listing's content fingerprint: the price, bedrooms, bathrooms, size, availability date, label and floor of every unit, the price currency, the listing's coordinates and building height when the site gives them, and the criteria areas whose search found the listing. Code in `matching.py` decides from these facts whether a listing meets the criteria. A fact the listing does not state never puts it outside. A building meets the criteria when one of its units does.

The must-haves in `criteria.other` are free text, so the model judges them. A listing whose facts meet the criteria waits, hidden, until each must-have has a judgment for its current content. A listing without facts from a listing site, such as one imported by hand with the gallery's `import`, waits for a judgment of every criterion as well, each written as a phrase such as "Price at most 2,400 USD a month". The model judges a listing once, and again only when its content changes. When the user changes the criteria, `settings.py save` judges every stored listing again from its stored features, in code, in the transaction that saves the criteria. Only a phrase that a listing has not been judged on yet needs the model, and the agent judges those listings in the same conversation.

A listing the user asked to contact, or has a viewing for, stays in the gallery's Contacted and Viewings tabs when it falls outside the criteria, and its details still open.

## Blocks

Listing sites block automated access. Zillow answers a client whose TLS handshake is not a browser's with a 403 and a "Press & Hold" bot check, before it serves any page. It does the same for headless Chromium, including the one Hermes installs. `client.py` fetches with `curl_cffi`, which sends Chrome's TLS handshake, waits 4 to 9 seconds between requests and keeps one cookie session for the run. The search does not solve bot checks and does not use the user's accounts, browser or a proxy.

When a site answers with a bot check, the run stops reading that site and leaves it alone for 30 minutes, doubling the wait after each further block up to a day. A run that reads the site without a block resets the wait. `search.py status` reports the block, and the agent tells the user when the search tries again.

## Zillow

Zillow pages carry their data as JSON in a `__NEXT_DATA__` script. The search reads that JSON.

- Areas are resolved to Zillow regions through Zillow's search box suggestions, once per area, and kept. An area Zillow does not know is reported and the other areas are searched. Zillow names some neighborhoods differently from common use, such as "Lake View" for Lakeview.
- Results come from `/homes/for_rent/` with a `searchQueryState` that holds the region, Zillow's default order `priorityscore`, rentals only, and the price, bedroom and size limits. A weekly or yearly price limit becomes a monthly one. A price in another currency than USD is not filtered by Zillow. Page 2 onwards needs the page in the path as well, `/homes/for_rent/2_p/`.
- Zillow shows at most 20 pages of 41 results per query. A query with more results is split into price bands until each band fits. The query's first page is read before the bands, because those are Zillow's highest-placed results of the whole query.
- A card is a building with several units, id `zillow:building-<lot id>`, or a single unit, floor plan or home, id `zillow:<zpid>`. Buildings, their units and their floor plans are read from the building page, where a floor plan listed with a price but no units counts as one unit. Homes are read from their home page.
- The listing's agent details hold the contact name and phone the page shows, and the listing's Zillow page as its contact form.

## Adding a listing site

A listing site is a module in `profile/skills/search/scripts/sites/`, listed in `SITES` in `sites/__init__.py`. Its `HOSTS` are the host names of the site's home page as the user names it. The module docstring of `sites/__init__.py` gives the functions a site module provides: `searches`, `pages`, `read` and `reread`. The errors in `sites/errors.py` report a block, a gone listing and a page the module cannot read. Listing ids start with the module's name and a colon.

## Commands

```bash
python "$HERMES_HOME/skills/search/scripts/search.py" status
python "$HERMES_HOME/skills/search/scripts/search.py" pending
python "$HERMES_HOME/skills/search/scripts/search.py" judge "$HERMES_HOME/cache/scratch/judgments.json"
python "$HERMES_HOME/skills/search/scripts/search.py" reported
```

`run` needs `curl_cffi` and runs from the cron job. The other commands need only the Python standard library. The launcher's own commands are `search-run.py` for a cron run and `search-run.py install`, which exits 5 when the environment cannot be built. The search keeps its own records, card hashes, area lookups, blocks and runs, in `search.sqlite3` next to `listings.sqlite3`.

## Checks

```powershell
python -m unittest discover -s tests -v
ruff check profile tests
```

`tests/test_search_matching.py` covers the criteria decisions, `tests/test_search_zillow.py` the Zillow pages, with small pages in the shape of Zillow's, `tests/test_search_run.py` the runs against a fake site and real SQLite stores, and `tests/test_search_launcher.py` the cron script. No test reaches a real site.
