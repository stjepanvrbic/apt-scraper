# Spec

## Scope

A Hermes Agent profile, its installer and a local gallery app that find, rank and pursue apartments for one user, per [the intent](intent.md). Actors: the user, listing sites, listing agents, and the user's Gmail, Google Calendar and iMessage. Excluded: phone calls, applications, leases, payments, negotiation, and texting on Windows.

## Terms

- **Criteria**: the user's hard filters (location, size, price range and others). A listing outside them is never shown.
- **Preferences**: the user's soft likes and dislikes, used only for ranking.
- **Listing**: one apartment, merged across every listing site that shows it, with links to each source.
- **Listing site**: a site the user named for the search.
- **Listing agent**: the person or office named on a listing as its contact.
- **Gallery**: the local app that shows listings and takes the user's actions.
- **Contact request**: the user's action asking the agent to pursue a listing.
- **Viewing**: an agreed time to see a listing, held as a Google Calendar event.
- **Note**: text the user attaches to a listing.

## Behavior

### Installation and setup

- `install.ps1` and `install.sh` install everything the agent needs and finish with a test message that must succeed.
- On first run the agent connects Gmail, Google Calendar, browser access and computer use, plus iMessage on macOS, and checks each connection. A connection that fails its check is reported, and its channel is unused until it passes.

### Interview

- Before the first search, the agent interviews the user until it has the criteria, the preferences, the listing sites and the user's viewing availability.
- The user changes any of these at any time by telling the agent.

### Search

- The search runs continuously over every listing site and finds at least 90% of the listings that match the criteria within a day of their posting.
- On each listing site, the listings that site places highest are searched first.
- A listing whose source shows it as gone is marked unavailable. Contacted listings keep their history.
- Re-reading an unchanged listing uses no model tokens. A listing is analyzed by the model once, and again only when its content changes.

### Ranking

- Every available listing has a rank and a one-line reason.
- Favorites, notes and preferences move listings with similar photos, price, description, location and amenities up the ranking.

### Gallery

- The gallery has the tabs Rankings, Favorites, Contacted, Viewings and Notes, in that order.
- Each listing shows its photos, price, size, rooms, address, description, amenities, date first seen, status and links to every source.
- On any listing the user can favorite or unfavorite it, add a note, and send a contact request.
- Contacted shows each contacted listing with its correspondence. Viewings shows each booked viewing with its time.
- Notes is where the user discusses preferences with the agent in general.

### Contact and viewings

- A contact request starts correspondence with the listing agent without further approval, by email, by iMessage on macOS, or through the listing site's contact form, whichever the listing offers.
- Messages are written in the first person as the user.
- The agent books a viewing only within the user's availability and never over an existing calendar event. The event holds the address, a listing link and the listing agent's contact details, and is updated or removed when the viewing moves or is canceled.
- When a listing agent or a form asks for personal information the agent does not already have approval to share, the agent asks the user and shares only what the user provides.

## Contracts and invariants

- The agent never submits an application, signs anything, pays, or negotiates rent or terms.
- All listing data, correspondence and notes stay on the user's machine.

## Acceptance

- Installer: a fresh Windows machine and a fresh Mac each finish `install` with the test message passing.
- Search: for a fixed search, the listings found within a day cover at least 90% of a manual search of the same listing sites.
- Ranking: after the user favorites five similar listings, unseen listings similar to them rank above dissimilar ones of similar price.
- Gallery: each listing field above is shown and each source link opens the original listing.
- Contact: a contact request on a test listing produces a first message in Gmail's Sent folder, or in Messages on macOS, within ten minutes.
- Viewing: a confirmed time from a test listing agent appears as a Google Calendar event with the required details, and a moved time updates that event.
- Personal information: a test request for the user's date of birth reaches the user, and nothing is sent until the user answers.

## Decisions

- Windows corresponds by email only, because iMessage exists only on macOS.
- Correspondence runs without per-message approval, so slow replies from the user don't cost viewings. The contracts above limit what it can commit to.
- Messages are written as the user, by the user's choice.
- Notes attach to listings, and the Notes tab holds general preference discussion, so a note stays next to the listing it is about.
- Coverage means 90% of matching listings within a day, highest-placed first, as the measurable form of "as exhaustive as possible".
- Listing sites' terms of use don't limit the search. Avoiding blocks does, because a blocked search finds nothing.
