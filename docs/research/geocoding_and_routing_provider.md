# Geocoding and routing provider

Question: which services or local tools should geocode the user's places, find each listing's distance to the nearest rapid-transit stop, and compute its travel time to each place by walking, cycling, transit or car, for one user on their own Windows or Mac, with Chicago as the first city?

It informs the first acceptance item of the transit and travel-time ticket (`0019`, "Distance to rapid transit and travel time to the user's places"), on which the owner chooses the provider before it is built. The requirements are taken from that ticket, from the interview places ticket (`0018`: each place has a name, an address or landmark, a travel mode of walk, bike, transit or car, a longest accepted trip and visits per week), from the code-derived features ticket (`0017`: listings carry coordinates from Zillow), and from [Spec › Listing features](../spec.md#listing-features) and [Spec › Ranking](../spec.md#ranking).

Each claim cites the page read, listed under [Sources](#sources) by number. "Measured" means run on the owner's Windows 11 machine (32 threads, 62 GB RAM) on 2026-10-25, in a temporary directory that was deleted afterwards. "Estimate" marks my arithmetic. "Proposal" marks a design recommendation. Where a page could not be read, the text says so.

## Summary

- Places: geocode with the public OpenStreetMap Nominatim server. Its [usage policy](https://operations.osmfoundation.org/policies/nominatim/) allows at most 1 request per second, requires an identifying User-Agent, requires caching results, and bans bulk and systematic queries [1]. One user saving 1 to 5 places is a handful of requests, well inside it. In a measured probe it resolved all four Chicago test places, including two named only by landmark. Photon placed one street address at the wrong building, and the US Census geocoder matched street addresses only.
- Nearest rapid-transit stop: compute locally from the CTA GTFS feed. Its `stops.txt` has 143 rail stations (`location_type` 1) for the 8 L lines (measured). A straight-line search over them takes 0.1 ms per listing, so no service is needed. Walking distance to the nearest few stations costs one local walking query.
- Transit: no free hosted API both covers Chicago transit and allows storing results. OpenRouteService's hosted API has no transit [15]. OSRM, GraphHopper's free plan and Mapbox route only car, bike and foot [9], [19], [36]. Google's terms forbid caching route results beyond coordinates [33], [34], and every account needs a billing account [31]. TravelTime's free plan is for internal use and evaluation, and caching needs TravelTime's express permission [43]. HERE allows 5,000 free transit requests a month [38], but its storage terms and signup requirements could not be read.
- Local routing works for all four modes. r5py, a Python wrapper of the R5 engine, built a network from the Illinois OpenStreetMap extract and CTA's feed in 125 s, then routed 300 listings to 3 places by transit in 78 s and by car in 11 s (measured). It needs a JDK 21 or later [51], about 3 GB of disk besides the JDK, and about 5 GB of memory while routing: it finished under a 5 GB Java heap and ran out under 3 GB (measured). CTA's feed as published fails to load in R5 because of an empty optional file, which the build must drop.
- Valhalla's Python wheel (`pyvalhalla`) is the lighter engine for walking, cycling and car: 22 MB, 777 MB of tiles built in 75 s, a listing-to-3-places matrix in 60 to 280 ms, no JDK (measured). Its transit build crashed, and it has no Intel Mac wheel [27].
- Recommended architecture (proposal): Nominatim for places, with the coordinates stored by address; the nearest L station from the feed's stops, measured as walking distance; r5py for travel time in every mode, stored by listing coordinates, place and mode. A rerank makes no provider call.
- Volume for 300 listings and 3 places: 3 Nominatim requests when the places are saved, then 1 per changed place. Travel times are 900 local origin-destination pairs for the first fill and 3 per new or moved listing. Only Nominatim has a limit, and these volumes are far below it.

## What the product needs

- Places: 1 to 5 per user, each an address or a landmark as the user said it. Each is geocoded once when saved and again only when its address changes (`0018` Deviations put the coordinates in their own store keyed by address).
- Listings: coordinates come from Zillow's page data (`0017` Result), so listings are not geocoded.
- Per listing with coordinates: the distance to the nearest rapid-transit stop, and the travel time to each place by that place's mode. Rapid transit means rail, metro and bus rapid transit stops (`0019` Design).
- Scale: tens to low hundreds of listings per city. The first fill for 300 listings and 3 places is 900 origin-destination pairs (estimate). After that, each new listing adds 3, and a changed place address re-routes all 300 listings to that place.
- Timing: listings get their feature record within an hour of being found ([Spec › Listing features](../spec.md#listing-features)), so routing can run in the background and does not need to answer within a request.
- The product runs on the user's machine, the user owns every account, and a paid key is acceptable only if the user supplies it (brief).

## Candidates compared

"Fits this use" means the terms and limits allow an automated agent on one user's machine to geocode 1 to 5 places, compute about 900 travel times once and 3 per new listing afterwards, and store the results for reranks.

| Candidate | Offers | Limits and terms for this use | Cost and key | Chicago transit | Footprint if local | Risks |
|---|---|---|---|---|---|---|
| Nominatim, public server | Geocoding of addresses and named places, free-form or structured, `countrycodes` filter [2] | At most 1 request per second, identifying User-Agent, results must be cached, attribution, no bulk or systematic queries, no autocomplete; limits apply to the application as a whole, across all its users [1] | Free, no key [1] | None | None | Policy may change and access may be withdrawn [1]. Fits this use |
| Nominatim, self-hosted | Same | None | Free | None | US address data from TIGER alone adds about 10 GB to the database [3] | An import was not tried; TIGER alone is 10 GB |
| Photon, public server | Geocoding, reverse geocoding, bounding-box and tag filters [5] | "Extensive usage will be throttled", no availability guarantee, no stated rate [4] | Free, no key | None | Planet database about 95 GB, 64 GB RAM recommended [5] | Placed one test address at a wrong building (measured) |
| US Census geocoder | Street addresses only, US only, batches up to 10,000 [6], [7] | No key, rate limit or terms stated on the pages read [6], [7] | Free | None | None | Does not resolve landmarks such as "First Ascent Avondale" (measured) |
| OSRM public server (routing.openstreetmap.de) | Car, bike and foot routing worldwide, table service for many-to-many durations [8], [9], [11] | "Reasonable, non-commercial use", at most 1 request per second, valid User-Agent, attribution and a "fix the map" link, no scraping or heavy use, no uptime guarantee [8], [9]. The full FOSSGIS policy is in German and returned a bot-check page instead of the text [10] | Free, no key | None [11] | None | Third-party server with no guarantee; "non-commercial" is the only stated scope |
| OSRM, self-hosted | Car, bike and foot, table service [11] | None | Free | None [11] | Docker or a Linux build; native Windows is not described [11] | Docker on every user machine |
| OpenRouteService, hosted (HeiGIT) | Directions, matrix, geocoding [12] | Daily and per-minute quotas per endpoint. The FAQ's example is 2,000 directions a day and 40 a minute [12]; a user reported a dashboard showing 2,000 and 3,000 a day in September 2026 [13]. The old host `api.openrouteservice.org` dropped to 10% of quota on 2026-08-27; the announcement names both 2026-09-28 and 2 to 6 November 2026 for its shutdown [14] | Free Standard key with a HeiGIT account, one key per person [12] | "GTFS and public-transit are currently not supported in our live API" [15] | None | The plans page returned 404 and the dashboard rendered no text, so the full quota table was not read [18] |
| OpenRouteService, self-hosted | Car, bike, foot and an experimental `public-transport` profile with GTFS [16] | None | Free | With CTA's GTFS (not tested) | RAM about the size of the OSM extract; Germany (3 GB) builds in about 8 GB [17] | Java service, experimental transit |
| GraphHopper, hosted | Routing, matrix, geocoding [19] | Free plan: 500 credits a day, non-commercial only, limited credits per minute, car, bike or foot only [19], [20]. A route costs 1 credit, a matrix costs the lower of origins × destinations ÷ 2 and max(origins, destinations) × 10 [20] | Free key; paid plans from €69 a month [19] | None on the hosted API | None | Non-commercial scope |
| GraphHopper, self-hosted | Car, bike, foot and GTFS public transit [21] | None | Free, Apache-2.0 [21] | With CTA's GTFS (not tested) | Java 25 or later [21] | Java service |
| Valhalla, public demo | Auto, bicycle, pedestrian, multimodal, matrix [22], [23] | Fair use like the OSRM and Nominatim servers, enforced by rate limits; distributed apps should announce themselves and send `X-Client-Id` [24] | Free | Not stated whether the demo loads transit | None | The demo app renders only with JavaScript, so its own terms were not read |
| Valhalla, local (`pyvalhalla`) | Auto, bicycle, pedestrian, matrix; multimodal walk-and-transit from GTFS [23], [25] | None | Free, MIT [27] | Built from CTA's GTFS: crashed (measured, below) | Wheels for Windows x64 and macOS arm64 only, Python 3.12 or later [27]; measured footprint below | No Intel Mac wheel; transit build failed |
| Google Maps Platform | Geocoding; Routes API Compute Routes and Compute Route Matrix with `TRANSIT`, `WALK`, `BICYCLE`, `DRIVE` [29], [30] | 3,000 elements a minute, 100 elements per transit matrix request [29]. Terms: no caching except as the service terms allow [33]; Routes API content may be cached only as latitude and longitude for 30 days [34]; geocoded coordinates for 30 days, or indefinitely only where the cache is not used to replace another call [34]; no use with a non-Google map [34] | Billing account required even for free use [31]. Free monthly caps per SKU: Geocoding 10,000; Compute Route Matrix 10,000 Essentials, 5,000 Pro, 1,000 Enterprise; then $5, $10 or $15 per 1,000 [28]. Which tier a transit request bills as is not stated [29] | Yes; CTA names its feed `google_transit.zip` [46] (inference: it is built for Google) | None | Storing travel times for reranks conflicts with the caching terms; the user must create a billing account |
| Mapbox | Geocoding; Directions and Matrix for driving, driving with traffic, walking and cycling [35], [36] | Geocoding 1,000 requests a minute; temporary results may not be cached, and permanent geocoding needs a credit card on file [37] | Free monthly: 100,000 temporary geocodes, 100,000 directions, 100,000 matrix elements; permanent geocoding $5 per 1,000 from the first request [35] | None [35], [36] | None | No transit; card needed to store geocodes |
| HERE | Geocoding, car, bicycle and pedestrian routing, public transit, matrix [38] | Storage terms not read | Free monthly: 30,000 geocodes, 30,000 car, bicycle or pedestrian routes, 5,000 public transit requests, 2,500 matrix transactions [38]. Signup requirements could not be read: HERE's billing article returned 404 | Not checked | None | Unknown storage terms and signup requirements |
| TravelTime | Travel-time matrix one-to-many or many-to-one for public transport, driving, cycling, walking and combinations; geocoding [39], [41] | Free plan: 120 hits a minute; one matrix search is one hit and destinations are never counted [40]. Up to 2,000 destinations per search [41]. Terms: free services "are for internal use and evaluation only", no commercial use; results may be cached only where TravelTime expressly permits, refreshed every 60 days and deleted when the agreement ends [43] | Free key with an account, no card [39] | United States has "Full" public transport coverage, 95% or more of stops [42] | None | Free plan scope is evaluation; storing results needs permission |
| CTA GTFS feed | Schedules and stops for the L and buses [44] | Licensed "for the sole purpose of assisting mass transportation (i.e., bus or rail) riders"; credit optional; revocable [45] | Free, no key, direct download [44], [46] | It is the source | 68.7 MB zip, 433 MB unpacked (measured) | Feed covers about 11 weeks at a time (measured), so it must be refreshed |
| Transitland | Feed directory and REST API [47] | API key required; free monthly quota, numbers not stated; datasets "free for non-commercial use" [47] | Free key | Not checked: the API needs a key | None | Adds an account where CTA's own URL suffices |
| OpenTripPlanner | Transit, walk, bike and car from GTFS and OSM; GraphQL API [48] | None | Free | With CTA's GTFS (not tested) | Java 25 or later, a 191 MB jar (2.10.0) [48], [49] | A Java server process per user |
| r5py | Travel-time matrices for walk, bike, transit and car from GTFS and OSM [50] | None | Free | With CTA's GTFS (measured, below) | JDK 21 or later [51]; downloads the R5 jar (64 MB) on first use [52]; measured footprint below | Java dependency; see measurements |

## Measurements

The probes sent each public geocoder four requests, Nominatim at no more than one every 1.5 s with a User-Agent naming the application [1].

### Geocoding four Chicago places

The places are those used to verify the interview places ticket (`0018` Result): one office address, two climbing gyms named as landmarks, and a street address.

| Query | Nominatim | Photon | Census |
|---|---|---|---|
| 233 S Wacker Dr, Chicago, IL | Willis Tower, 233 South Wacker Drive (building) | Willis Tower, 233 South Wacker Drive | 233 S WACKER DR, 60606 |
| First Ascent Avondale, Chicago | First Ascent Avondale, 3516 North Spaulding Avenue | Same | No match |
| 3516 N Clark St, Chicago, IL | 3516 North Clark Street, Wrigleyville | McDonald's, 600 North Clark Street (wrong, about 6 km south) | 3516 N CLARK ST, 60657 |
| Movement Wrigleyville, Chicago | Movement Wrigleyville, 1115 West Addison Street | Same | No match |

Nominatim and Photon returned identical coordinates for the three places both found, because both read OpenStreetMap. Census coordinates differ from OpenStreetMap's by tens of meters, because Census interpolates along address ranges [7].

### CTA feed and nearest station

- The feed downloaded from CTA's URL without a key: a 68.7 MB zip, published 2026-09-14, with service dates from 2026-09-11 to 2026-11-30 [46]. CTA says it updates "generally, once every week or two" [44].
- `stops.txt` has 11,138 rows: 10,981 stops, 143 stations and 14 entrances. `routes.txt` has 8 rail routes (`route_type` 1: Red, Blue, Brown, Green, Orange, Pink, Purple, Yellow) and 124 bus routes.
- The feed's `frequencies.txt` holds only a header. R5 refuses the whole feed because of it ("Table is present in zip file, but it has no entries"); removing that optional file fixes it.
- Nearest station by straight line over the 143 stations, then walking distance to the nearest five by Valhalla's pedestrian matrix:

| Point | Nearest station | Straight line | Walking |
|---|---|---|---|
| Logan Square | Logan Square | 874 m | 976 m, 11.6 min |
| Wicker Park | Damen (Blue) | 125 m | 148 m, 1.8 min |
| Rogers Park | Morse | 187 m | 259 m, 3.0 min |
| Garfield Ridge | Midway | 2,822 m | 3,691 m, 43.6 min |

The straight-line search took 0.1 ms per point; the walking matrix 21 to 124 ms.

### Valhalla through `pyvalhalla`

- `pip install pyvalhalla` installed version 3.9.1 from a 22 MB Windows wheel. It ships `valhalla_build_tiles`, `valhalla_ingest_transit`, `valhalla_convert_transit` and `valhalla_service`, and runs them through `python -m valhalla` [27].
- The Illinois extract from Geofabrik is 362 MB, ODbL [53]. Road tiles for walk, bike and car built in 75 s into 777 MB.
- A matrix from one Logan Square point to the three places geocoded above took 227 ms walking, 279 ms cycling and 60 ms driving. In the order Willis Tower, First Ascent Avondale, Movement Wrigleyville, walking: 103, 38 and 81 min. Cycling: 35, 13 and 25 min. Driving: 11, 7 and 11 min, without traffic data, since none was loaded.
- Transit: `valhalla_ingest_transit` read CTA's feed in 74 s and `valhalla_convert_transit` built 750 MB of transit tiles in 66 s, but `valhalla_build_tiles` with transit then crashed with a segmentation fault after 32 s. Valhalla's Docker scripts build a timezone database whenever they build transit [26], and the wheel does not ship `valhalla_build_timezones`, so the likely cause is the missing timezone database (inference, not confirmed). Valhalla publishes its timezone database as a GitHub Actions artifact, not as a release file [26].

### r5py

- `pip install r5py` installed version 1.1.7 into a fresh virtual environment in 41 s, 549 MB with its dependencies (GeoPandas, scikit-learn, JPype and others) [50]. It needs a JDK 21 or later [51]. This machine has JDK 26, which r5py found only once `JAVA_HOME` was set; without it, the import failed with "No JVM shared library file (jvm.dll) found". On first use it downloaded `r5-v7.5.1-r5py-all.jar` (64 MB) into `%LOCALAPPDATA%\r5py` [52].
- Inputs: the Illinois extract and CTA's feed without its empty `frequencies.txt`. The network built in 125 s and is cached as 1.8 GB in `%LOCALAPPDATA%\r5py` (a 1.0 GB network and a 0.8 GB street store). Later runs load it in 5 to 7 s.
- Memory: r5py gives Java 80% of RAM unless told otherwise [52]. Capped at 8 GB, the build peaked at a 9.4 GB working set. Loading the cached network and routing 900 pairs finished under a 5 GB cap with a 5.5 GB peak, and ran out of Java heap under a 3 GB cap.
- Travel times in minutes, departing Tuesday 2026-10-27 between 8:30 and 9:00 (r5py reports the median over the window):

| From | Mode | Willis Tower | First Ascent Avondale | Movement Wrigleyville |
|---|---|---|---|---|
| Logan Square | Transit | 49 | 31 | 47 |
| Wicker Park | Transit | 28 | 31 | 35 |
| Rogers Park | Transit | 54 | 53 | 30 |
| Logan Square | Walk | 145 | 52 | 113 |
| Logan Square | Bike | 65 | 20 | 38 |
| Logan Square | Car | 12 | 8 | 11 |

- At scale: 300 random points in Chicago to the 3 places. Transit from listings to places took 78 s (0.26 s per listing); from places to listings, 0.9 s. Car from places to listings took 11 s. Of 900 pairs, 3 to 5 had no route within 3 hours, so they stay unknown. The median was 48 to 49 min by transit and 14 min by car.
- r5py's defaults are 3.6 km/h walking, 12 km/h cycling and bicycle traffic stress up to 3 [52]. They are the likely reason its walking and cycling times are longer than Valhalla's for the same trip (inference, Valhalla's defaults were not read): from Logan Square to Willis Tower, 145 against 103 min walking and 65 against 35 min cycling. Both speeds are parameters.
- The transit times were not checked against another trip planner.

## Recommended architecture (proposal)

One local engine, r5py, for every mode, with Nominatim for the places. It computes all four modes and the walk to a station from one street network and one transit feed, sends no routing request to any service, and its results are the user's to store. Its costs are a JDK, about 3 GB of disk besides the JDK and about 5 GB of memory while routing.

### Geocoding

- When a place is saved or its address changes, one Nominatim search request for the address as the user said it, with `countrycodes=us` and a `viewbox` around the city to prefer local results without excluding others [2]. Requests are serialized at no more than one a second, carry a User-Agent naming the application, and are never retried in a loop [1].
- The coordinates are stored by address in their own table, as the places ticket's Deviations require. The policy requires caching results, and notes that the data is under the ODbL, under which small extractions may fall under fair use [1].
- No result, or a result outside the city's area, is reported to the user, who is asked to describe the place again (`0019` Acceptance).
- The gallery shows "© OpenStreetMap contributors" wherever it shows travel times or place locations, as the policy requires attribution [1].
- Nominatim's policy says an LLM may suggest the service only when it points to the usage policy and explains its restrictions [1]. The policy is linked at the top of this report, and the implementation must follow it: at most 1 request per second, an identifying User-Agent, cached results, no bulk or systematic queries, no autocomplete.

### Data on the user's machine

- The OpenStreetMap extract for the city's state from Geofabrik (Illinois: 362 MB [53]) and the city's transit feeds (CTA: 68.7 MB, measured). Header-only optional GTFS files are dropped before the build, because R5 rejects a feed that has one (measured).
- r5py builds and caches the network (125 s and 1.8 GB for Illinois and CTA, measured). The feed is downloaded again when CTA publishes a new one, which it does "generally, once every week or two" [44], and at the latest before the current feed's last service date. Each refresh rebuilds the network.
- Geofabrik's download terms were not read.

### Nearest rapid-transit stop

- Stations are the feed's `location_type` 1 stops served by a rail route, `route_type` 1 (measured: 143 for CTA).
- For each listing, take the 5 nearest stations by straight line (0.1 ms), then the shortest walking distance to them from r5py's walking matrix. Straight-line distance understates the walk where streets do not run straight to the station: at Garfield Ridge, 2.8 km straight against 3.7 km walking (measured).
- CTA's feed has no route type other than bus and rail (measured), so bus rapid transit cannot be told apart from other buses in it. Until a feed marks one, no Chicago bus stop counts as rapid transit.

### Travel time per place

- One r5py travel-time matrix per mode, from the listings that lack a value to the places of that mode, departing on a weekday morning with a 30-minute window. Transit uses walking to and from stops. A listing with no route within 3 hours, or without coordinates, has the travel time as unknown, as `0019` requires.
- Each value is stored with the listing's coordinates, the place's address, the mode and the network's build date. Reranks only read the store. A listing is routed again only when its coordinates change, a place's address or mode changes, or the network is rebuilt. Keying by coordinates instead of content means a price change does not re-route the listing, so routing still happens at most once per content and place.
- Car times have no traffic data in either local engine. They are free-flow estimates, which likely read low for rush-hour trips (inference).

### Request counts and daily volumes, 300 listings and 3 places

| Step | Network requests per listing | First fill | After that |
|---|---|---|---|
| Geocode places | 0 | 3 Nominatim requests, at most one a second | 1 request per changed place |
| Listing coordinates | 0, from Zillow (`0017`) | 0 | 0 |
| Nearest station | 0, local | 300 local lookups | 1 per new or moved listing |
| Travel times | 0, local | 900 pairs: 78 s by transit, 11 s by car (measured) | 3 pairs per new listing, about 0.26 s by transit; 300 pairs when a place changes |
| Rerank | 0 | 0 | 0 |
| Data refresh | n/a | 362 MB OSM and 69 MB GTFS downloads, 125 s build | One feed download and rebuild every week or two |

The only service with a limit is Nominatim, which these volumes do not approach: 3 requests per user at setup, against a limit of 1 a second [1].

### If the owner rejects the local transit engine

Two other designs avoid the JDK, each with a terms question still open:

- Valhalla through `pyvalhalla` for walking, cycling, car and the walk to stations (22 MB wheel, 777 MB of tiles, no JDK), plus HERE's public transit API with the user's own key: 900 of its 5,000 free transit requests a month for the first fill [38]. HERE's storage terms and signup requirements were not read, so this needs that check first. `pyvalhalla` has no Intel Mac wheel [27].
- The same Valhalla setup, plus TravelTime's free key: one hit per place and mode covers up to 2,000 listings [40], [41]. Its free plan is for "internal use and evaluation only", and storing results needs TravelTime's express permission [43], so this needs written permission from TravelTime first.

Google Maps Platform fits least. Its terms allow caching only coordinates, for 30 days [34], so storing travel times for reranks breaks them, and every user would need a Google Cloud billing account [31].

## Open questions for the owner

1. Local engine or API for transit? The ticket prefers free services and the user's own accounts. A local r5py engine is free, unlimited and storable, but each install gains a JDK, about 3 GB of disk besides it and about 5 GB of memory while routing (measured with the Illinois extract). An API keeps the install small, but no free transit API found allows storing results: HERE's terms are unread, TravelTime's free plan is evaluation only and Google forbids caching. Recommendation: r5py for all four modes, because it is the only option that meets "a rerank makes no provider call" without a terms question, and the memory is needed only while a routing job runs.
2. When does a transit or car trip depart? Places record mode, longest trip and visits per week, but no time of day, and transit times depend on it. The alternatives are one fixed weekday-morning window for every place, or a time per place asked in the interview, which adds a question per place. Recommendation: a fixed weekday departure between 8:00 and 9:00 for every place, and a time per place only if a user asks for one, because it adds no interview question and keeps the places comparable, and the interview already asks five things per place.
3. Is Metra rapid transit? The ticket counts rail, metro and bus rapid transit stops. Metra is commuter rail: its feed URL answered (651 KB, modified 2026-10-22), but its developer page returned 403, so its terms are unread [54]. The alternatives are to count only L stations as rapid transit, or to count Metra stations too. Recommendation: count only CTA L stations for "nearness to rapid transit", and route transit trips over both CTA and Metra once Metra's terms are read. A trip that uses Metra is still the user's real travel time, while whether a Metra station counts as rapid transit is the owner's reading of the term.
4. Is distance to the nearest stop straight-line or walking? Straight-line is a pure function of the coordinates and needs no engine. Walking follows streets and crossings and needs the engine, which the travel times need anyway. Recommendation: walking distance, because straight-line understated one measured case by 0.9 km (Garfield Ridge to Midway).
5. Are r5py's default speeds acceptable? Its defaults are 3.6 km/h walking and 12 km/h cycling on streets of traffic stress up to 3 [52], which gave longer walking and cycling times than Valhalla for the same trips (measured). A slower assumed speed counts more listings as beyond a place's longest trip. Recommendation: keep the default values but store them as explicit settings, so an r5py release cannot change them unnoticed, and let the user change them by telling the agent.
6. Cache travel times by coordinates rather than by listing content? The ticket says once per content and place. Coordinates decide the route, and content also changes with price or photos. Recommendation: key by coordinates, place address, mode and network build date, which still routes at most once per content and place and skips needless re-routing.

Not questions, but checks the implementation needs: Geofabrik's download terms, HERE's storage terms if the fallback is chosen, and why Valhalla's transit build crashed (unconfirmed missing timezone database).

## Sources

1. OpenStreetMap Foundation, Nominatim Usage Policy: https://operations.osmfoundation.org/policies/nominatim/
2. Nominatim documentation, Search: https://nominatim.org/release-docs/latest/api/Search/
3. Nominatim documentation, Adding TIGER housenumbers: https://nominatim.org/release-docs/latest/customize/Tiger/
4. Photon public server: https://photon.komoot.io/
5. Photon README: https://github.com/komoot/photon
6. US Census Bureau, Geocoding Services API: https://geocoding.geo.census.gov/geocoder/Geocoding_Services_API.html
7. US Census Bureau, Census Geocoder documentation: https://www.census.gov/programs-surveys/geography/technical-documentation/complete-technical-documentation/census-geocoder.html
8. OSRM wiki, Demo server: https://github.com/Project-OSRM/osrm-backend/wiki/Demo-server
9. FOSSGIS, About routing.openstreetmap.de: https://routing.openstreetmap.de/about.html
10. FOSSGIS, Nutzungsbedingungen (not readable, bot check): https://fossgis.de/arbeitsgruppen/osm-server/nutzungsbedingungen/
11. OSRM README: https://github.com/Project-OSRM/osrm-backend
12. openrouteservice, FAQ, quotas: https://giscience.github.io/openrouteservice/frequently-asked-questions
13. openrouteservice forum, "Quota exceeded on Directions while dashboard shows full quota", 2026-09-28: https://ask.openrouteservice.org/t/quota-exceeded-on-directions-while-dashboard-shows-full-quota-standard-key/8068
14. openrouteservice forum, "Reducing the quota of deprecated API api.openrouteservice.org": https://ask.openrouteservice.org/t/reducing-the-quota-of-deprecated-api-api-openrouteservice-org/8013
15. openrouteservice forum, "Public transport directions show driving directions", 2025-01-07: https://ask.openrouteservice.org/t/public-transport-directions-show-driving-directions/6790
16. openrouteservice documentation, profiles: https://giscience.github.io/openrouteservice/run-instance/configuration/engine/profiles/
17. openrouteservice documentation, system requirements: https://giscience.github.io/openrouteservice/run-instance/system-requirements
18. openrouteservice plans (404) and HeiGIT dashboard (no text without JavaScript): https://openrouteservice.org/plans/, https://account.heigit.org/info/plans
19. GraphHopper pricing: https://www.graphhopper.com/pricing/
20. GraphHopper support, "What is one credit?": https://support.graphhopper.com/support/solutions/articles/44000718211-what-is-one-credit-
21. GraphHopper README: https://github.com/graphhopper/graphhopper
22. Valhalla documentation: https://valhalla.github.io/valhalla/
23. Valhalla, Route API reference: https://valhalla.github.io/valhalla/api/route/api-reference/
24. Valhalla README: https://github.com/valhalla/valhalla
25. Valhalla Docker README: https://github.com/valhalla/valhalla/blob/master/docker/README.md
26. Valhalla Docker build script and tzdb workflow: https://github.com/valhalla/valhalla/blob/master/docker/scripts/configure_valhalla.sh, https://github.com/valhalla/valhalla/blob/master/.github/workflows/publish_tz_db.yml
27. pyvalhalla on PyPI, version 3.9.1: https://pypi.org/project/pyvalhalla/
28. Google Maps Platform pricing (updated 2026-10-23): https://developers.google.com/maps/billing-and-pricing/pricing
29. Google Routes API, usage and billing: https://developers.google.com/maps/documentation/routes/usage-and-billing
30. Google Routes API, transit routes: https://developers.google.com/maps/documentation/routes/transit-route
31. Google Maps Platform, Get started: https://developers.google.com/maps/get-started
32. Google Geocoding API, usage and billing: https://developers.google.com/maps/documentation/geocoding/usage-and-billing
33. Google Maps Platform Terms of Service, 3.2.3: https://cloud.google.com/maps-platform/terms
34. Google Maps Platform Service Specific Terms, Geocoding API 6, Routes API 19: https://cloud.google.com/maps-platform/terms/maps-service-terms
35. Mapbox pricing: https://www.mapbox.com/pricing
36. Mapbox Directions API: https://docs.mapbox.com/api/navigation/directions/
37. Mapbox Geocoding API: https://docs.mapbox.com/api/search/geocoding/
38. HERE supplemental pricing (updated 2026-04-01): https://www.here.com/get-started/pricing/supplemental-pricing
39. TravelTime pricing: https://traveltime.com/pricing
40. TravelTime API usage limits: https://docs.traveltime.com/api/overview/usage-limits
41. TravelTime API, travel time distance matrix: https://docs.traveltime.com/api/reference/travel-time-distance-matrix
42. TravelTime API, supported countries: https://docs.traveltime.com/api/overview/supported-countries
43. TravelTime Terms of Service, 7.2, 7.3, 12.5: https://traveltime.com/terms-of-service
44. CTA, GTFS developer page: https://www.transitchicago.com/developers/gtfs/
45. CTA, Developer License Agreement and Terms of Use: https://www.transitchicago.com/developers/terms/
46. CTA, schedule data directory: https://www.transitchicago.com/downloads/sch_data/
47. Transitland documentation: https://www.transit.land/documentation
48. OpenTripPlanner, basic tutorial: https://docs.opentripplanner.org/en/latest/Basic-Tutorial/
49. OpenTripPlanner release 2.10.0: https://github.com/opentripplanner/OpenTripPlanner/releases/tag/v2.10.0
50. r5py on PyPI, version 1.1.7: https://pypi.org/project/r5py/
51. r5py installation guide: https://r5py.readthedocs.io/stable/user-guide/installation/installation.html
52. r5py source, `util/classpath.py`, `util/memory_footprint.py` and `r5/regional_task.py` (the installed 1.1.7 downloads R5 7.5.1; `main` pins 7.6, 65 MB): https://github.com/r5py/r5py/tree/main/src/r5py
53. Geofabrik, Illinois extract: https://download.geofabrik.de/north-america/us/illinois.html
54. Metra GTFS schedule feed (developer page returned 403): https://schedules.metrarail.com/gtfs/schedule.zip, https://metra.com/developers
