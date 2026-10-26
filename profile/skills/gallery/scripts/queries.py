import json


RANKING_EXCLUSION = """CASE
    WHEN l.status != 'available' THEN 'Not ranked: unavailable.'
    WHEN COALESCE(f.dismissed, 0) != 0 THEN 'Not ranked: dismissed.'
    WHEN l.matches_criteria != 1 THEN 'Not ranked: outside the criteria.'
END"""
RANKABLE = f'({RANKING_EXCLUSION}) IS NULL'


def validate_page(page, page_size):
    if type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 100:
        raise ValueError('Page must be positive and page_size must be between 1 and 100.')


def browse(connection, view, page, page_size):
    validate_page(page, page_size)
    conditions = {
        'Rankings': RANKABLE,
        'Favorites': 'f.favorite = 1 AND f.dismissed = 0',
        'Contacted': 'c.listing_id IS NOT NULL',
        'Viewings': "v.status = 'booked'",
        'Dismissed': 'f.dismissed = 1',
    }
    if view not in conditions:
        raise ValueError('Unknown gallery view.')
    joins = 'FROM listings l LEFT JOIN ranking r ON r.listing_id = l.id LEFT JOIN feedback f ON f.listing_id = l.id LEFT JOIN contacts c ON c.listing_id = l.id'
    if view == 'Viewings':
        joins += ' JOIN viewings v ON v.listing_id = l.id'
    where = ' WHERE ' + ('' if view in ('Rankings', 'Contacted', 'Viewings') else 'l.matches_criteria = 1 AND ') + conditions[view]
    order = 'v.time, v.id' if view == 'Viewings' else 'CASE WHEN r.rank IS NOT NULL AND r.fingerprint = l.fingerprint THEN 0 ELSE 1 END, CASE WHEN r.fingerprint = l.fingerprint THEN r.rank END, l.id'
    fields = ['id', 'title', 'price', 'price_note', 'currency', 'rental_period', 'size', 'rooms', 'address', 'status', 'first_seen']
    summary = ', '.join(f"'{field}', json_extract(l.data, '$.{field}')" for field in fields)
    viewing = ', v.id AS viewing_id, v.time AS viewing_time, v.details AS viewing_details' if view == 'Viewings' else ''
    connection.execute('BEGIN')
    total = connection.execute('SELECT COUNT(*) ' + joins + where).fetchone()[0]
    rows = connection.execute(f"""SELECT json_object({summary}) AS data, json_extract(l.data, '$.photos[0]') AS photo,
        r.rank, r.reason, COALESCE(r.exploration, 0) AS exploration, r.fingerprint = l.fingerprint AS analysis_current,
        COALESCE(f.favorite, 0) AS favorite, COALESCE(f.dismissed, 0) AS dismissed,
        c.status AS contact_status, c.channel AS contact_channel, c.error AS contact_error {viewing}
        {joins} {where} ORDER BY {order} LIMIT ? OFFSET ?""", (page_size, (page - 1) * page_size)).fetchall()
    items = []
    for row in rows:
        item = json.loads(row['data'])
        item.update(photos=[row['photo']] if row['photo'] else [], rank=row['rank'], reason=row['reason'], exploration=bool(row['exploration']),
                    analysis_current=bool(row['analysis_current']), favorite=bool(row['favorite']), dismissed=bool(row['dismissed']),
                    contact={'status': row['contact_status'], 'channel': row['contact_channel'], 'error': row['contact_error']} if row['contact_status'] else None,
                    correspondence=[], correspondence_total=0, viewing=None)
        if view == 'Viewings':
            item['viewing'] = {'id': row['viewing_id'], 'time': row['viewing_time'], 'details': row['viewing_details']}
        items.append(item)
    if view == 'Contacted' and items:
        placeholders = ','.join('?' for _ in items)
        messages = connection.execute(f"""SELECT * FROM (
            SELECT id, listing_id, direction, channel, substr(text, 1, 500) AS text, created_at,
            COUNT(*) OVER (PARTITION BY listing_id) AS total,
            ROW_NUMBER() OVER (PARTITION BY listing_id ORDER BY id DESC) AS position
            FROM correspondence WHERE listing_id IN ({placeholders})
        ) WHERE position <= 3 ORDER BY id""", [item['id'] for item in items])
        by_id = {item['id']: item for item in items}
        for message in messages:
            by_id[message['listing_id']]['correspondence'].append(dict(message))
            by_id[message['listing_id']]['correspondence_total'] = message['total']
    return {'items': items, 'total': total, 'page': page, 'page_size': page_size}


def snapshot(connection):
    connection.execute('BEGIN')
    listings = {row['id']: {**json.loads(row['data']), 'content_fingerprint': row['fingerprint'],
                'matches_criteria': bool(row['matches_criteria']),
                'favorite': False, 'dismissed': False, 'rank': None, 'reason': None, 'features': None,
                'contact': None, 'notes': [], 'correspondence': [], 'viewings': []}
                for row in connection.execute('SELECT * FROM listings')}
    for row in connection.execute('SELECT * FROM ranking'):
        listing = listings[row['listing_id']]
        listing.update(rank=row['rank'], reason=row['reason'], features=json.loads(row['features']) if row['features'] else None,
                       analysis_current=row['fingerprint'] == listing['content_fingerprint'])
    for row in connection.execute('SELECT * FROM feedback'):
        listings[row['listing_id']].update(favorite=bool(row['favorite']), dismissed=bool(row['dismissed']))
    for row in connection.execute('SELECT * FROM contacts'):
        listings[row['listing_id']]['contact'] = {**dict(row), 'agent': json.loads(row['agent'])}
    for table in ('notes', 'correspondence', 'viewings'):
        order = 'time, id' if table == 'viewings' else 'id'
        for row in connection.execute(f'SELECT * FROM {table} ORDER BY {order}'):
            listings[row['listing_id']][table].append(dict(row))
    return {'listings': list(listings.values()),
            'settings': {row['name']: json.loads(row['value']) for row in connection.execute('SELECT * FROM settings')}}
