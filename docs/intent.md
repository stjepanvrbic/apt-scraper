# Intent

## Problem

Finding an apartment means searching several listing sites again and again, comparing listings by hand, and arranging each viewing with a different listing agent over email, phone or text. Most of the time goes to repetitive searching and back-and-forth messages rather than to deciding.

## Outcome

- Before any search, the agent interviews the user until it knows their criteria (location, size, price range and the rest) and what they like.
- The user sees most of the listings that match those criteria on the listing sites they name, and the search keeps running so new listings appear while they are still available.
- Listings are ranked by how well they fit the user. Each one shows the photos, price, description, location, amenities and a link to the original listing, so the user can judge it without opening the source site.
- The ranking moves closer to the user's taste over time, learning from favorites, viewing notes and stated preferences across photos, price, description, location and amenities.
- When the user asks to contact a listing, the agent reaches the listing agent by email, text or the listing site's contact form, handles the correspondence, and books a viewing that appears on the user's Google Calendar. The user only has to show up.
- After a viewing, the user records what they saw, and it feeds back into the ranking.
- The user can change any part of the setup or the agent's behavior by talking to the agent.
- A new user downloads the repository, runs the installer, connects their own accounts and has all of this working.

## Constraints

- Runs on Windows and macOS. Texting goes through iMessage, which exists only on macOS, so Windows has no texting.
- Every account is the user's own: the model account, a Claude subscription by default or any provider Hermes supports, Google (Gmail and Calendar) and, on macOS, iMessage. Many listing agents correspond only by text.
- Listing sites detect and block automated access. The search must keep working without the user's accounts or network getting blocked.

## Non-goals

- Submitting rental applications, signing leases, or paying deposits or rent.
- Negotiating rent or lease terms.
- Sharing personal information the user has not provided or approved for that request. When a listing agent or form asks for something, the agent asks the user, and shares it only if the user supplies it.
- A hosted service. Each user runs their own copy on their own machine.

## Stakeholders

The single user who installs and runs their own copy decides and uses it.
