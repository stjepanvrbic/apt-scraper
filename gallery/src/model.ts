export type Note = { id: number; text: string; role: 'user' | 'agent'; created_at: string };
export type ListingSummary = {
  id: string; title: string | null; price: number | null; price_note: string | null; currency: string | null; rental_period: string | null; size: string | null; rooms: string | null;
  address: string | null; photos: string[]; first_seen: string; status: 'available' | 'unavailable';
  rank: number | null; reason: string | null; exploration: boolean; favorite: boolean; dismissed: boolean;
  contact: { status: 'pending' | 'in_progress' | 'contacted' | 'failed' | 'closed'; channel: string | null; error: string | null } | null;
  correspondence: { id: number; direction: 'incoming' | 'outgoing'; channel: string; text: string; created_at: string }[];
  correspondence_total: number;
  viewing: { id: string; time: string; details: string } | null;
};
export type Listing = ListingSummary & {
  explanation: Explanation | null;
  content_fingerprint: string;
  description: string | null; amenities: string[]; sources: { label: string; url: string }[];
  notes: Note[]; notes_total: number;
  viewings: { id: string; time: string; details: string; status: 'booked' | 'agreed' | 'canceled'; calendar_event_id: string | null }[];
  viewings_total: number;
};
export type ListingPage = { items: ListingSummary[]; total: number; page: number; page_size: number };
export type Comparison = { question: { listings: [Listing, Listing] } | null; answered: number };

export type Explanation = {
  score: number;
  contributions: { feature: string; value: string | null; contribution: number }[];
  similar_favorite: { id: string; title: string } | null;
};
type Check = { median: number | null; mean: number | null };
export type ReviewResult = {
  outcome: 'kept' | 'reverted' | 'no moves'; reverted_because: string | null;
  check: { before: Check; after: Check };
  moves: { id: string; title: string | null; from: number; to: number; reason: string; confidence: number }[];
  reasons: number;
  flags: { id: string; title: string | null; kind: 'like' | 'dislike'; preference: string }[];
  findings: string[];
};
export type Review = { attempts: number; updated_at: string } & (
  { state: 'waiting' | 'running' | 'superseded'; result: null } |
  { state: 'failed'; result: { error: string } } |
  { state: 'done'; result: ReviewResult }
);
export type RerankSummary = {
  id: number; finished_at: string; ranked: number; waiting_for_features: number; favorites: number;
  held_out: { id: string; title: string; rank: number }[];
  held_out_median: number | null; held_out_mean: number | null;
  exploration: string[]; findings: string[]; stale: string[]; review: Review | null; seconds?: number;
};
export type RerankState = { running: boolean; last_rerank: RerankSummary | null };

export const price = (listing: ListingSummary) => listing.price === null ? listing.price_note ?? 'Price not listed' : new Intl.NumberFormat(undefined, {
  ...(listing.currency ? { style: 'currency', currency: listing.currency } : {}), maximumFractionDigits: 0,
}).format(listing.price);
export const title = (listing: ListingSummary) => listing.title ?? listing.address ?? 'Apartment listing';
export const period = (listing: ListingSummary, prefix: string) => listing.price === null || !listing.rental_period ? '' : listing.rental_period === 'total' ? 'total' : `${prefix} ${listing.rental_period}`;

export const date = (value: string) => new Date(value.length === 10 ? `${value}T12:00:00` : value)
  .toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });

export const viewingTime = (value: string) => new Date(value).toLocaleString(undefined, {
  weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short',
});
