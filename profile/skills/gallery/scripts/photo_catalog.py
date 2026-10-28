"""The named text probes of the photo encoder. Standard library only: the store and the reading vocabulary read the
names, and the build step turns the phrasings into the text vectors that ship in probes.npy.

The text tower runs only at build time, so a probe exists only when it is in this catalog. Each probe has several
phrasings; a photo's score for it is the cosine with their mean or with the best of them, as model.json's pooling says,
and a listing's score is that of its best photo. The room types are those of extraction (record.py ROOM_TYPES), each
with phrasings scored the same way."""

ROOM_PROMPTS = {
    'kitchen': ['a photo of a kitchen', 'a kitchen in an apartment', 'kitchen cabinets, countertops and appliances'],
    'bathroom': ['a photo of a bathroom', 'a bathroom with a toilet, a sink and a shower', 'a bathroom in an apartment'],
    'bedroom': ['a photo of a bedroom', 'a bedroom with a bed', 'an empty bedroom in an apartment'],
    'living': ['a photo of a living room', 'a living room with a sofa', 'an empty living room in an apartment'],
    'dining': ['a photo of a dining room', 'a dining table with chairs', 'a dining area in an apartment'],
    'exterior': ['a photo of the outside of a building', 'the facade of an apartment building from the street',
                 'the front of a house'],
    'view': ['the view from an apartment window', 'a city skyline seen from a window', 'a view over the city'],
    'outdoor space': ['a photo of a balcony', 'a patio, deck or terrace', 'a private backyard'],
    'laundry': ['a laundry room with a washer and dryer', 'a washing machine and a dryer', 'a shared laundry room'],
    'floor plan': ['a floor plan drawing', 'an architectural floor plan diagram', 'a floor plan of an apartment'],
    'building amenity': ['the lobby of an apartment building', 'a fitness center in an apartment building',
                         'a swimming pool', 'a rooftop deck with seating', 'a residents lounge'],
    'other': ['a hallway', 'a closet', 'a storage room', 'the entryway of an apartment'],
}

# The first five are the quality gate's sanity probes, measured against reference labels before any probe enters the
# model. The rest name visual qualities that stated preferences and hypotheses commonly use.
PROBE_PROMPTS = {
    'carpet': ['a room with wall-to-wall carpet', 'a carpeted floor'],
    'exposed brick': ['a room with exposed brick walls', 'an interior wall of exposed brick'],
    'balcony': ['a private balcony of an apartment', 'a balcony with a railing'],
    'bathtub': ['a bathroom with a bathtub', 'a bathtub'],
    'open kitchen': ['an open kitchen that flows into the living room', 'an open-plan kitchen and living area'],
    'hardwood floors': ['a room with hardwood floors', 'wooden floorboards'],
    'bright rooms': ['a bright room full of daylight', 'a sunny apartment with lots of natural light'],
    'dark rooms': ['a dark room with little daylight', 'a dim apartment'],
    'floor-to-ceiling windows': ['a room with floor-to-ceiling windows', 'a wall of tall windows'],
    'high ceilings': ['a room with very high ceilings', 'a loft with tall ceilings'],
    'low ceilings': ['a room with a low ceiling', 'a basement apartment with low ceilings'],
    'white kitchen cabinets': ['a kitchen with white cabinets', 'white shaker kitchen cabinets'],
    'dark kitchen cabinets': ['a kitchen with dark wood cabinets', 'espresso kitchen cabinets'],
    'stainless steel appliances': ['a kitchen with stainless steel appliances', 'a stainless steel refrigerator and range'],
    'kitchen island': ['a kitchen with an island', 'a kitchen island with stools'],
    'renovated kitchen': ['a newly renovated modern kitchen', 'a brand new kitchen with quartz countertops'],
    'dated kitchen': ['an old dated kitchen', 'a worn kitchen with old appliances'],
    'renovated bathroom': ['a newly renovated modern bathroom', 'a bathroom with new tile and fixtures'],
    'dated bathroom': ['an old dated bathroom', 'a worn bathroom with old fixtures'],
    'vintage details': ['original vintage woodwork and trim', 'a vintage apartment with period details'],
    'modern interior': ['a sleek modern apartment interior', 'a minimalist contemporary apartment'],
    'fireplace': ['a room with a fireplace', 'a fireplace with a mantel'],
    'walk-in closet': ['a walk-in closet', 'a large closet with shelves'],
    'in-unit washer and dryer': ['a washer and dryer inside an apartment', 'a stacked washer and dryer in a closet'],
    'skyline view': ['a view of the city skyline', 'a high-rise view over the city'],
    'green view': ['a view of trees and greenery from a window', 'a view of a park'],
    'roof deck': ['a rooftop deck', 'a roof terrace with city views'],
    'private yard': ['a private backyard with grass', 'a fenced yard'],
    'built-in shelves': ['built-in bookshelves', 'built-in shelving in a living room'],
    'bay window': ['a bay window', 'a room with a bay window'],
    'small rooms': ['a small cramped room', 'a tiny bedroom'],
    'spacious rooms': ['a large spacious room', 'a big open living room'],
}
GATE_PROBES = ('carpet', 'exposed brick', 'balcony', 'bathtub', 'open kitchen')

# Per-room best-match similarity to the favorites is a column for these rooms.
MATCH_ROOMS = ('kitchen', 'bathroom', 'living', 'bedroom')


def entries():
    """Every phrasing in the order of the rows of probes.npy: rooms first, then probes."""
    return ([('room', name, text) for name, texts in ROOM_PROMPTS.items() for text in texts]
            + [('probe', name, text) for name, texts in PROBE_PROMPTS.items() for text in texts])


def vocabulary():
    """The reading vocabulary's photo features: what the photos look like, by probe."""
    return {f'photo:{name}': f'the photos show {texts[0].removeprefix("a ").removeprefix("an ")}'
            for name, texts in PROBE_PROMPTS.items()}
