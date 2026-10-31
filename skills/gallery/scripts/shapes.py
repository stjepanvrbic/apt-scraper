"""The shapes of the records the gallery keeps and exchanges as JSON, and the narrowing of a parsed JSON value into
them. A module that validates or produces a shape owns it; the shapes here are the ones every skill shares, so this
module imports nothing of the profile and anything may import it."""

import json
from typing import Literal, TypedDict, TypeIs, cast

RentalPeriod = Literal['day', 'week', 'month', 'year', 'total']
ListingStatus = Literal['available', 'unavailable']
Weekday = Literal['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']


class Source(TypedDict):
    label: str
    url: str


AgentField = Literal['name', 'email', 'phone', 'contact_form']
AGENT_FIELDS: tuple[AgentField, ...] = ('name', 'email', 'phone', 'contact_form')


class Agent(TypedDict, total=False):
    name: str
    email: str
    phone: str
    contact_form: str


class Listing(TypedDict):
    """A listing as validate_listing keeps it: every field present, the ones the source did not state null."""
    id: str
    title: str | None
    price: int | float | None
    currency: str | None
    size: str | None
    rooms: str | None
    address: str | None
    description: str | None
    amenities: list[str]
    photos: list[str]
    sources: list[Source]
    first_seen: str
    status: ListingStatus
    rental_period: RentalPeriod | None
    agent: Agent
    price_note: str | None


class Range(TypedDict, total=False):
    min: int | float | None
    max: int | float | None


class PriceLimit(Range, total=False):
    currency: str
    period: RentalPeriod


class SizeLimit(Range, total=False):
    unit: Literal['m2', 'sqft']


class Criteria(TypedDict, total=False):
    location: list[str]
    price: PriceLimit
    size: SizeLimit
    rooms: Range
    move_in: str | None
    other: list[str]


PreferenceKind = Literal['likes', 'dislikes']


class Preferences(TypedDict, total=False):
    likes: list[str]
    dislikes: list[str]


class Place(TypedDict):
    name: str
    address: str


class WeeklyWindow(TypedDict):
    day: Weekday
    start: str
    end: str


UnavailablePeriod = TypedDict('UnavailablePeriod', {'from': str, 'to': str})


class ViewingAvailability(TypedDict, total=False):
    timezone: str
    weekly: list[WeeklyWindow]
    unavailable: list[UnavailablePeriod]


Importance = Literal['low', 'medium', 'high']


class LocationPriorities(TypedDict, total=False):
    places: Importance
    rapid_transit: Importance
    areas: dict[str, Importance]


class Settings(TypedDict, total=False):
    """The settings rows a store holds, each present once it was saved."""
    criteria: Criteria
    preferences: Preferences
    listing_sites: list[str]
    viewing_availability: ViewingAvailability
    places: list[Place]
    location_priorities: LocationPriorities
    transit_feed: str


def is_object(value: object) -> TypeIs[dict[str, object]]:
    """Whether a parsed JSON value is an object. JSON object keys are always text."""
    return isinstance(value, dict)


def is_array(value: object) -> TypeIs[list[object]]:
    return isinstance(value, list)


def loads(text: str | bytes) -> object:
    """A parsed JSON value whose shape the caller narrows."""
    return cast(object, json.loads(text))
