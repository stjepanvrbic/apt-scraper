import http.client
import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from test_gallery_store import listing
from store import ListingStore
from server import DISCARD_LIMIT, make_server


class GalleryServerCase(unittest.TestCase):
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

    def request(self, path, body=None, headers=None, timeout=3):
        request = Request(self.url + path, data=json.dumps(body).encode() if body is not None else None,
                          headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.server.access_token, **(headers or {})})
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)

    def raw_post(self, path, content, headers=None):
        fields = {'Host': f'127.0.0.1:{self.server.server_port}', 'Content-Type': 'application/json',
                  'Authorization': 'Bearer ' + self.server.access_token, 'Content-Length': str(len(content)),
                  'Connection': 'close', **(headers or {})}
        head = ''.join(f'{name}: {value}\r\n' for name, value in fields.items() if value is not None)
        with socket.create_connection(('127.0.0.1', self.server.server_port), timeout=10) as connection:
            connection.sendall(f'POST {path} HTTP/1.1\r\n{head}\r\n'.encode() + content)
            response = b''
            while chunk := connection.recv(65536):
                response += chunk
        status_line, _, rest = response.partition(b'\r\n')
        return status_line.decode(), json.loads(rest.partition(b'\r\n\r\n')[2])


class GalleryServerTests(GalleryServerCase):
    def test_one_connection_serves_several_requests(self):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        self.addCleanup(connection.close)
        sockets = set()
        for extra in ({}, {'Content-Length': '0'}, {}):
            connection.request('GET', '/api/health', headers={'Authorization': 'Bearer ' + self.server.access_token, **extra})
            response = connection.getresponse()
            self.assertEqual((response.status, response.version, response.getheader('Connection')), (200, 11, None))
            response.read()
            sockets.add(id(connection.sock))
        self.assertEqual(len(sockets), 1)

    def test_a_request_the_server_cannot_read_past_closes_the_connection_and_says_so(self):
        headers = {'Authorization': 'Bearer ' + self.server.access_token, 'Content-Type': 'application/json'}
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        self.addCleanup(connection.close)
        for method, path, body, status in (('GET', '/api/health', b'x', 200),
                                           ('POST', '/api/chat', b'{' * (DISCARD_LIMIT + 1), 400)):
            with self.subTest(method):
                connection.request(method, path, body=body, headers=headers)
                response = connection.getresponse()
                response.read()
                self.assertEqual((response.status, response.getheader('Connection')), (status, 'close'))
                connection.request('GET', '/api/health', headers=headers)
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 200)

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
        self.store.rank('example:100', fingerprint, 1, {'text': 'Quiet courtyard.'})
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

    def test_a_body_of_unknown_or_oversized_length_gets_its_answer_before_the_close(self):
        oversized, padded = b'{}'.ljust(2000000), b'{}'.ljust(99000)
        chunked = (b'10000\r\n' + b' ' * 65536 + b'\r\n') * 4 + b'0\r\n\r\n'
        chunked_headers = {'Content-Length': None, 'Transfer-Encoding': 'chunked'}
        length = ('400 Bad Request', 'Request must declare a Content-Length from 1 to 100000 bytes.')
        for content, headers, (status, error) in [
                (oversized, {}, length), (padded, {'Content-Length': 'many'}, length), (chunked, chunked_headers, length),
                (oversized, {'Authorization': ''}, ('401 Unauthorized', 'Gallery access is missing or expired. Open the gallery again through Hermes.')),
                (chunked, {**chunked_headers, 'Origin': 'https://evil.example'}, ('403 Forbidden', 'Only requests from this local gallery are accepted.')),
                (padded, {'Content-Length': 'many', 'Content-Type': 'text/plain'}, ('400 Bad Request', 'Request must use application/json.'))]:
            with self.subTest(length=len(content), headers=headers):
                for _ in range(20):
                    self.assertEqual(self.raw_post('/api/chat', content, headers), ('HTTP/1.1 ' + status, {'error': error}))
        self.assertEqual(self.store.chat_state()['messages'], [])
