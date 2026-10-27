# Spec

## Scope

A Hermes Agent profile, its installer and a local gallery app that find, rank and pursue apartments for one user, per [the intent](intent.md). Actors: the user, listing sites, listing agents, a geocoding service, and the user's Gmail, Google Calendar, messaging gateway and iMessage. Excluded: phone calls, applications, leases, payments, negotiation, merging one apartment across listing sites, and texting on Windows.

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
- **Place**: a location the user goes to regularly, such as work or a gym, and how much being near it matters.
- **Location priorities**: how much the places, nearness to a rapid-transit station and each area of the criteria matter to the user.
- **Feature record**: what is stored about one listing content for ranking: what extraction read from its photos and text, and what code computed from the listing.
- **Extraction**: the one model read of a listing content that produces the model's part of its feature record.
- **Trait**: a short tag for something about a listing that the ratings and flags of a feature record do not cover.
- **Tag list**: the shared list of traits. Extraction reuses its traits and adds new ones to it.
- **Label**: what a user action says about one listing: a favorite, a contact request, a dismissal, a note's sentiment or a pairwise answer.
- **Scoring model**: the model of the user's taste fitted from the feature records, the labels and the preferences.
- **Rerank**: fitting the scoring model and ordering Rankings by it.
- **Leave-one-out check**: each favorite's rank when the scoring model is fitted without that favorite's label, with the favorite counted like any other unlabeled ranked listing.
- **Exploration listing**: a listing placed in the top 20 to learn the user's taste, chosen from what the scoring model is unsure of.
- **Review pass**: the model's review of a rerank's result.
- **Pairwise question**: two listings shown together with the question "Which of these two?".
- **Implicit signal**: a gallery interaction that is not an action on a listing: opening it, how long it stays open, and which photos are viewed.

## Behavior

### Installation and setup

- The user installs with one line pasted into a terminal, with nothing cloned by hand: `irm <bootstrap.ps1 URL> | iex` in PowerShell on Windows, `curl -fsSL <bootstrap.sh URL> | bash` on macOS. The line installs Git when it is missing, clones the repository into a fixed per-user location or updates the clone there, and runs the installer from it. On a Mac without the Xcode command line tools, it opens their installer, waits until they are installed and continues. Running the line again updates the repository and the profile and keeps the user's `config.yaml`, data and credentials. After `hermes profile delete apt-scraper`, or over what a failed install left, it installs the profile anew with the distribution's `config.yaml`.
- `install.ps1` and `install.sh` install everything the agent needs, check that every Python environment the profile's scripts run in loads its packages, send a test message that must succeed, and finish with the agent's chat open in the Hermes web dashboard in the user's browser. A question a command asks during the install shows in the terminal as it is asked and takes the user's answer from the keyboard. On Windows, adding the gateway's Scheduled Task needs administrator approval: the installer opens Windows's approval prompt for it directly, with no yes/no question in the terminal first, and a machine that already has the entry gets no prompt. Whatever happens there, the gateway and the dashboard run as the user who installed them, not as administrator. A step that fails shows what its command printed.
- The dashboard starts without a window when the user logs in. A shortcut named apt-scraper, in the Start menu on Windows and in Applications on macOS, opens its chat and starts the dashboard first when it is not running.
- On first run the agent connects Gmail, Google Calendar, a Hermes messaging gateway such as Telegram, and iMessage on macOS, and checks each connection. The setup is guided by the agent in the dashboard chat: it runs every command and does every step in the browser and in Telegram itself; the user only signs in, approves consent screens and macOS permissions, types their Mac password when an installer asks, and pastes the Telegram bot token into the dashboard. A connection that fails its check is reported, and its channel is unused until it passes.
- The agent works in the user's browser through Hermes's browser tools, in a window it opens with the user's logins: Hermes copies the active profile of the default browser (Chrome, Edge or Brave) and starts that browser on the copy. Web pages, Telegram included, are never driven with computer use, which remains only for macOS permission dialogs, macOS privacy settings and a terminal program. Nothing in the setup needs the user to keep a window in front, so they keep chatting and using the mouse while the agent works. The user's profile and the visible window are on only while a setup runs, and a setup that stops without turning them off is turned off after 30 minutes. The scheduled jobs share the profile's settings, so the setup waits for a listing correspondence run in progress, no run starts while the setup uses the browser, and a contact form checks that no setup uses it before its first browser action. Each setup takes a fresh copy. On Windows a running browser keeps its logins locked, so the copy needs the browser closed: the agent offers to close it, or the user signs in in the window instead.
- Running the install again keeps the interview, the connection results and the stored Google client and token, and the next conversation continues the setup from them.

### Interview

- Before the first search, the agent interviews the user until it has the criteria, the preferences, the places, the location priorities, the listing sites and the user's viewing availability. "No places" is an answer.
- The user changes any of these at any time by telling the agent.

### Search

- The search runs continuously over every listing site and finds at least 90% of the listings that match the criteria within a day of their posting.
- On each listing site, the listings that site places highest are searched first.
- A listing whose source shows it as gone is marked unavailable. Contacted listings keep their history.

### Listing features

- The model reads a listing only when something about it or about the user's preferences needs judging: new or changed content, a criterion code cannot judge, a stated preference its feature record does not express, or a note. Reranks and unchanged listings cost no model calls, apart from the review pass.
- Each listing content has one feature record. A new or changed listing gets its record within an hour of being found, without the user asking. Unchanged content is never extracted again.
- Extraction reads the listing's text and every one of its photos, after exact and near duplicates are removed. It opens a photo at full resolution only when a detail needs it.
- The model's part of the record has the room type of each photo; ratings from 1 to 5, each with a confidence, or "not shown", for qualities such as light, condition, kitchen, bathroom and ceiling height; flags read from the text, such as in-unit laundry, air conditioning, pets and utilities included; traits; and, for each stated preference the rest of the record does not express, whether the listing has it, rules it out or does not say.
- Extraction uses a trait from the tag list when one fits. A new trait joins the tag list, so later listings use the same word for the same thing.
- Code adds what it reads reliably: monthly price, size, price per square foot, rooms, unit level from unit labels such as `#G` or "Garden", days since first seen, photo count, coordinates, straight-line distance to the nearest rapid-transit station, and straight-line distance to each place.
- A stated preference added later is judged for each listing from its feature record and text, without the photos. Until then the listing counts as not saying.
- When extraction of one content fails three times, it stops. The listing is ranked from its code-derived features alone, its reason says so, and the agent reports it to the user.

### Ranking

- Ranks change only on a rerank, which the user starts with Score and rerank in the gallery or by asking the agent. Between reranks the order holds still.
- A rerank ranks every available listing that has a feature record, and gives each a rank and a short reason, listing the values for and against it as separate items. A listing found or changed since the last rerank, or waiting for its feature record, shows after the ranked listings, marked as not ranked yet with the reason why.
- Favorites and contact requests count for their listings, and dismissals against them, so listings with similar photos, price, description, location and amenities rise or fall. A note is read once into a sentiment, which labels its listing, and into likes and dislikes on the feature record's ratings, flags and traits. A pairwise answer counts the chosen listing over the other.
- Stated likes and dislikes, and the likes and dislikes read from notes, set the starting weight of the features they name. Labels can outweigh them: when the favorites keep having something the user said they dislike, it stops counting against listings.
- Location counts through the areas of the criteria, nearness to the favorites' locations, distance to the nearest rapid-transit station and distance to each place, each weighted by the location priorities.
- A listing's reason names the features that moved its score most and the favorite it most resembles, citing only its feature record. Its details show the full explanation: each feature's contribution to its score, computed from the scoring model, and that favorite.
- Each rerank runs the leave-one-out check.
- When more than 20 listings are ranked, up to two of the top 20 are exploration listings, marked as shown to learn the user's taste. They are drawn from the listings outside the top 18 that have no label from the user, and are fewer when fewer such listings exist. The rerank reports how many it placed, and the agent tells the user that number.
- Pairwise questions pair real listings whose order the scoring model is least sure of. After the interview, once the first listings have feature records, the agent asks the user to answer 5 to 8 of them.
- After each rerank, the review pass reads the top 50 and the state of the whole ranking. It rewrites their reasons from their feature records, flags ranks that conflict with a stated preference, may move listings, giving a reason for each move, and reports anything in the ranking state that looks wrong. The leave-one-out check runs before and after the review. When the review's moves make the favorites rank worse, all of them are reverted.
- Implicit signals are recorded in the listing store. The scoring model does not use them, and each rerank reports whether they would raise the favorites' held-out ranks.
- A dismissed listing leaves Rankings and Favorites.

### Gallery

- The gallery has the tabs Rankings, Favorites, Compare, Contacted, Viewings and Notes, in that order.
- Each listing shows its photos, price, size, rooms, address, description, amenities, date first seen, status and links to every source.
- Rankings is a grid led by each listing's main photo, and a listing opens to its full photo set and details.
- Rankings has the Score and rerank button. After a rerank it shows each favorite's held-out rank, the review pass's moves with their reasons, and a reverted review with its moves.
- Compare shows one pairwise question at a time, each listing with its main photo, price, size, rooms and address. The user picks one or skips the question, and either listing opens to its details.
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
- Listing photos reach the model only inside that listing's extraction, never in the agent's main conversation.
- A feature record is stored only when it is valid: ratings from 1 to 5 or "not shown", confidences from 0 to 1, room types from a fixed set, and traits from the tag list or added to it.
- Apart from the exploration listings and the review pass, a rerank is reproducible: the same feature records, labels and settings give the same ranks and reasons.

## Acceptance

- Installer: a fresh Windows machine and a fresh Mac each finish the one-line install with the test message passing and the dashboard's chat for `apt-scraper` open in the browser.
- Search: of the matching listings a manual search of the listing sites finds that were posted more than a day earlier, the agent has already found at least 90%.
- Interview: a first interview stores places and the location priorities.
- Listing features: every unique photo of a test listing has a room type in its feature record, the agent's main conversation holds no image, and searching the unchanged listing again makes no extraction.
- Ranking: the tester favorites five listings they consider alike, and the unseen listings the tester judges alike to them rank above the rest of similar price.
- Rerank: with 300 listings and 50 favorites, pressing Score and rerank shows the new order and the leave-one-out check within 2 seconds, and Hermes's session usage shows no model call before the review pass.
- Overruled preference: with the stated dislike "Carpet" and five carpeted favorites, carpet does not count against an unseen carpeted listing in its explanation.
- Location: of two test listings alike except for the distance to a place, the closer one ranks higher.
- Exploration: with more than 20 ranked listings and at least two listings without a label from the user outside the top 18, exactly two of the top 20 are marked as exploration listings, and neither has a label from the user. With 21 listings alike and notes on the three ranked last, none is marked and the rerank reports none.
- Pairwise questions: on a fresh profile, the agent asks for 5 to 8 pairwise answers once the first feature records exist, and a single answer ranks the chosen listing above the other at the next rerank.
- Review pass: a review whose moves make the favorites' held-out ranks worse is reverted, and the gallery shows the reverted moves.
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
- Ranks change only when the user asks for a rerank, so the order holds still while the user browses, and fitting the scoring model in code costs no model call ([preference learning research](research/preference_learning_for_listing_ranking.md)).
- The review pass may move listings, but its moves stand only when the leave-one-out check after the review is no worse than before it, because language-model rankers show position and popularity bias.
- Up to two of the top 20 are exploration listings, drawn only from outside the top 18, because a model that sees labels only on the listings it already ranks high cannot learn where it is wrong.
- Pairwise questions have their own tab, because a choice between two real listings is a label the scoring model uses at once, also before the user has any favorites.
