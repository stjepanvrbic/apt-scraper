import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from test_gallery_store import listing
from store import ListingStore
from server import make_server


class GalleryServerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = ListingStore(Path(self.directory.name) / 'listings.sqlite3')
        self.store.upsert_listing(listing())
        self.server = make_server(self.store, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, body=None, headers=None):
        request = Request(self.url + path, data=json.dumps(body).encode() if body is not None else None,
                          headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.server.access_token, **(headers or {})})
        with urlopen(request, timeout=3) as response:
            return json.load(response)

    def test_browser_actions_are_saved_in_shared_store(self):
        self.assertEqual(self.request('/api/listings/example:100')['title'], 'Courtyard apartment')
        self.request('/api/listings/example:100/feedback', {'favorite': True})
        self.request('/api/listings/example:100/notes', {'text': 'Would like to see it.'})
        self.request('/api/listings/example:100/contact', {})
        saved = ListingStore(self.store.path).state()
        self.assertTrue(saved['listings'][0]['favorite'])
        self.assertEqual(saved['listings'][0]['contact']['status'], 'pending')
        self.assertEqual(saved['listings'][0]['notes'][0]['text'], 'Would like to see it.')

    def test_bad_requests_and_cross_origin_writes_do_not_change_state(self):
        cases = [
            ('/api/listings/example:100/feedback', {'favorite': 'true'}, {}, 400),
            ('/api/listings/missing/notes', {'text': 'Note'}, {}, 404),
            ('/api/chat', {'text': '  ', 'request_id': 'empty'}, {}, 400),
            ('/api/chat', {'text': 'Note', 'role': 'agent'}, {}, 400),
            ('/api/listings/example:100/feedback', {'favorite': True}, {'Origin': 'https://evil.example'}, 403),
            ('/api/listings', None, {'Host': 'evil.example'}, 403),
        ]
        for path, body, headers, expected in cases:
            with self.subTest(path=path, headers=headers), self.assertRaises(HTTPError) as raised:
                self.request(path, body, headers)
            self.assertEqual(raised.exception.code, expected)
        self.assertFalse(self.store.state()['listings'][0]['favorite'])

    def test_browser_reads_a_bounded_page_and_small_revision(self):
        fingerprint = self.store.listing('example:100')['content_fingerprint']
        self.store.rank('example:100', fingerprint, 1, 'Quiet courtyard.')
        page = self.request('/api/listings?view=Rankings&page_size=1')
        self.assertEqual(len(page['items']), 1)
        self.assertNotIn('description', page['items'][0])
        self.assertEqual(set(self.request('/api/revision')), {'revision'})
        with self.assertRaises(HTTPError) as raised:
            self.request('/api/listings?page_size=10000')
        self.assertEqual(raised.exception.code, 400)

    def test_every_api_requires_launch_authentication_even_without_an_origin(self):
        for path, body in [('/api/health', None), ('/api/listings', None), ('/%61pi/health', None),
                           ('/api/chat', {'text': 'Hello', 'request_id': 'unauthorized'}),
                           ('/api/chat/control', {'message_id': 1, 'interrupt': True})]:
            request = Request(self.url + path, data=json.dumps(body).encode() if body else None,
                              headers={'Content-Type': 'application/json'})
            with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                urlopen(request, timeout=3)
            self.assertEqual(error.exception.code, 401)
        self.assertEqual(self.store.chat_state()['messages'], [])

    def test_browser_chat_does_not_expose_gateway_session_or_admission_identifiers(self):
        identity = self.store.start_chat('Hello', 'request')
        self.store.update_chat(identity, session_id='private-session', admission_id='private-admission', status='complete')
        response = self.request('/api/chat')
        self.assertNotIn('session_id', response)
        self.assertNotIn('admission_id', response['messages'][-1])
