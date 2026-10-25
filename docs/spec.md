# Spec

## Scope

A Hermes Agent profile, its installer and a local gallery app that find, rank and pursue apartments for one user, per [the intent](intent.md). Actors: the user, listing sites, listing agents, and the user's Gmail, Google Calendar, messaging gateway and iMessage. Excluded: phone calls, applications, leases, payments, negotiation, merging one apartment across listing sites, and texting on Windows.

## Terms

- **Criteria**: the user's hard filters (location, size, price range and others). A listing outside them is never shown.
- **Preferences**: the user's soft likes and dislikes, used only for ranking.
- **Listing**: one apartment as a listing site shows it, with a link to the source.
- **Listing site**: a site the user named for the search.
- **Listing agent**: the person or office named on a listing as its contact.
- **Gallery**: the local app that shows listings and takes the user's actions.
- **Contact request**: the user's action asking the agent to pursue a listing.
- **Viewing**: an agreed time to see a listing, held as a Google Calendar event.
- **Note**: text the user attaches to a listing.

## Behavior

### Installation and setup

- `install.ps1` and `install.sh` install everything the agent needs and finish with a test message that must succeed.
- On first run the agent connects Gmail, Google Calendar, a Hermes messaging gateway such as Telegram, and iMessage on macOS, and checks each connection. A connection that fails its check is reported, and its channel is unused until it passes.

### Interview

- Before the first search, the agent interviews the user until it has the criteria, the preferences, the listing sites and the user's viewing availability.
- The user changes any of these at any time by telling the agent.

### Search

- The search runs continuously over every listing site and finds at least 90% of the listings that match the criteria within a day of their posting.
- On each listing site, the listings that site places highest are searched first.
- A listing whose source shows it as gone is marked unavailable. Contacted listings keep their history.
- The model analyzes a listing once, and again only when its content changes.

### Ranking

- Every available listing has a rank and a one-line reason.
- Favorites and positive notes raise listings with similar photos, price, description, location and amenities. Dismissals and negative notes lower them.
- A dismissed listing leaves Rankings and Favorites.

### Gallery

- The gallery has the tabs Rankings, Favorites, Contacted, Viewings and Notes, in that order.
- Each listing shows its photos, price, size, rooms, address, description, amenities, date first seen, status and links to every source.
- Rankings is a grid led by each listing's main photo, and a listing opens to its full photo set and details.
- On any listing the user can favorite or unfavorite it, dismiss it, add a note, and send a contact request.
- Contacted shows each contacted listing with its correspondence. Viewings shows each booked viewing with its time.
- Notes is where the user discusses preferences with the agent in general.

### Contact and viewings

- A contact request starts correspondence with the listing agent without further approval, by email, by iMessage on macOS, or through the listing site's contact form, whichever the listing offers.
- Messages are written in the first person as the user.
- The agent books a viewing only within the user's availability and never over an existing calendar event. The event holds the address, a listing link and the listing agent's contact details, and is updated or removed when the viewing moves or is canceled.
- When a listing agent or a form asks for personal information the agent does not already have approval to share, the agent asks the user and shares only what the user provides.
- The agent sends its questions to the user, and proposed viewing times it cannot settle alone, through the messaging gateway.

## Contracts and invariants

- The agent never submits an application, signs anything, pays, or negotiates rent or terms.

## Acceptance

- Installer: a fresh Windows machine and a fresh Mac each finish `install` with the test message passing.
- Search: of the matching listings a manual search of the listing sites finds that were posted more than a day earlier, the agent has already found at least 90%.
- Ranking: the tester favorites five listings they consider alike, and the unseen listings the tester judges alike to them rank above the rest of similar price.
- Gallery: Rankings shows a photo grid, each listing field above is shown, and each source link opens the original listing.
- Contact: within ten minutes of a contact request on a test listing, a first message is in Gmail's Sent folder, in Messages on macOS, or submitted through the test listing's contact form.
- Viewing: a confirmed time from a test listing agent appears as a Google Calendar event with the required details, and a moved time updates that event.
- Personal information: a test request for the user's date of birth reaches the user through the messaging gateway, and nothing is sent until the user answers.

## Decisions

- Windows has no texting, because iMessage exists only on macOS.
- Correspondence runs without per-message approval, so slow replies from the user don't cost viewings. The contracts above limit what it can commit to.
- Messages are written as the user, by the user's choice.
- Notes attach to listings, and the Notes tab holds general preference discussion, so a note stays next to the listing it is about.
- Coverage means 90% of matching listings within a day, highest-placed first, as the measurable form of "as exhaustive as possible".
- The user can dismiss listings, so the ranking learns dislikes as well as likes (`0008`).
- The agent reaches the user through a Hermes messaging gateway, so questions arrive while the gallery is closed and stay apart from the user's own messages (`0009`).
- Listing sites' terms of use don't limit the search. Avoiding blocks does, because a blocked search finds nothing.
