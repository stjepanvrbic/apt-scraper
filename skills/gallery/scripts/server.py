import json
import mimetypes
import os
import secrets
import socket
import socketserver
import threading
import sqlite3
import time
import traceback
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TypedDict, cast, final, override
from urllib.parse import parse_qs, unquote, urlsplit
from chat import ChatController, MessageStatus, Runner, ToolEvent
from launch import Health
from rerank import pairwise_questions, rerank
from hypotheses import ActiveHypothesis, active as active_hypotheses, decide as decide_hypothesis
from review import Rerank
from shapes import is_object, loads
from store import ListingDetail, ListingStore


STATIC = Path(__file__).resolve().parents[1] / 'web'
BODY_LIMIT = 100000
DISCARD_LIMIT = 1000000
CLOSE_SECONDS = 5


class CompareQuestion(TypedDict):
    listings: list[ListingDetail]


class Comparison(TypedDict):
    """What /api/compare answers: the next pairwise question, if any, and how many were answered."""
    question: CompareQuestion | None
    answered: int


class RerankStatus(TypedDict):
    """What /api/rerank answers to a GET: whether scoring runs, the last rerank and the hypotheses under test."""
    running: bool
    last_rerank: Rerank | None
    hypotheses: list[ActiveHypothesis]


class PublicMessage(TypedDict):
    """A chat message as the browser sees it: without the gateway's session and admission ids, and with the prompt
    stripped of its execution generation."""
    id: int
    role: str
    content: str
    status: MessageStatus
    events: list[ToolEvent]
    prompt: Mapping[str, object] | None
    error: str | None
    created_at: str


class ChatPage(TypedDict):
    active: bool
    total: int
    messages: list[PublicMessage]


def comparison(store: ListingStore) -> Comparison:
    pairs = pairwise_questions(store, 1)
    question: CompareQuestion | None = None
    if pairs:
        question = {'listings': [store.listing(identifier, visible_only=True) for identifier in pairs[0]['listings']]}
    return {'question': question, 'answered': len(store.pairwise_answers())}


@final
class GalleryServer(ThreadingHTTPServer):
    access_token: str

    @override
    def server_bind(self) -> None:
        # Not HTTPServer's server_bind, whose reverse lookup of the address through socket.getfqdn can take a
        # minute on macOS and leaves the gallery unreachable while the launcher waits for it.
        socketserver.TCPServer.server_bind(self)
        host, port = cast(tuple[str, int], self.server_address)
        self.server_name, self.server_port = host, port


def make_server(store: ListingStore, port: int = 8765, chat_runner: Runner | None = None, install_id: str = 'development') -> GalleryServer:
    access_token = secrets.token_urlsafe(32)
    chat = ChatController(store, chat_runner)
    rerank_lock = threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        # Keeps a browser's connections open across requests. The HTTP/1.0 default closes one per request, and on
        # Windows each closed connection holds a port for minutes, which a photo-heavy page exhausts.
        protocol_version: str = 'HTTP/1.1'
        close_connection: bool

        @override
        def log_message(self, format: str, *arguments: object) -> None:
            pass

        def reply(self, status: int, value: object) -> None:
            self.send_bytes(status, json.dumps(value, ensure_ascii=False).encode(), 'application/json; charset=utf-8')

        def send_bytes(self, status: int, content: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(content)))
            if self.close_connection:
                self.send_header('Connection', 'close')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https: http: data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            _ = self.wfile.write(content)

        def allowed(self) -> bool:
            port = cast(GalleryServer, self.server).server_port
            hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
            origin = self.headers.get('Origin')
            if self.headers.get('Host') not in hosts or (origin and origin not in {f'http://{host}' for host in hosts}):
                self.reply(403, {'error': 'Only requests from this local gallery are accepted.'})
                return False
            if unquote(urlsplit(self.path).path).startswith('/api/') and not secrets.compare_digest(
                    self.headers.get('Authorization', '').encode(), ('Bearer ' + access_token).encode()):
                self.reply(401, {'error': 'Gallery access is missing or expired. Open the gallery again through Hermes.'})
                return False
            return True

        def do_GET(self) -> None:
            # A body on a GET is left unread, so the connection closes rather than read it as the next request.
            length = self.headers.get('Content-Length', '0')
            if 'Transfer-Encoding' in self.headers or not length.isdigit() or int(length) > 0:
                self.close_connection = True
            if not self.allowed():
                return
            path = unquote(urlsplit(self.path).path)
            if path == '/api/health':
                health: Health = {'application': 'apt-scraper-gallery', 'database': str(store.path), 'process_id': os.getpid(), 'install_id': install_id}
                self.reply(200, health)
            elif path.startswith('/api/'):
                try:
                    query = parse_qs(urlsplit(self.path).query)
                    if path == '/api/revision':
                        result = {'revision': store.revision()}
                    elif path == '/api/rerank':
                        status: RerankStatus = {'running': rerank_lock.locked(), 'last_rerank': store.last_rerank(), 'hypotheses': active_hypotheses(store)}
                        result = status
                    elif path == '/api/compare':
                        result = comparison(store)
                    elif path == '/api/compare/invitation':
                        invitation = store.compare_invitation()
                        result = {'shown': invitation['due'] and 'gallery' in invitation['delivered']}
                    elif path == '/api/listings':
                        result = store.browse(query.get('view', ['Rankings'])[0], page=int(query.get('page', ['1'])[0]),
                                              page_size=int(query.get('page_size', ['24'])[0]))
                    elif path.startswith('/api/listings/'):
                        result = store.listing(path.removeprefix('/api/listings/'), visible_only=True, **{
                            name: int(query.get(name, ['1'])[0]) for name in ('notes_page', 'correspondence_page', 'viewings_page')})
                    elif path == '/api/chat':
                        state = store.chat_state(int(query.get('page', ['1'])[0]))
                        page: ChatPage = {'active': state['active'], 'total': state['total'], 'messages': []}
                        for message in state['messages']:
                            public: PublicMessage = {'id': message['id'], 'role': message['role'], 'content': message['content'],
                                                     'status': message['status'], 'events': message['events'], 'prompt': message['prompt'],
                                                     'error': message['error'], 'created_at': message['created_at']}
                            if public['prompt']:
                                public['prompt'] = {key: value for key, value in public['prompt'].items() if key != 'execution_generation'}
                            page['messages'].append(public)
                        result = page
                    else:
                        raise KeyError('Page not found.')
                    self.reply(200, result)
                except ValueError as error:
                    self.reply(400, {'error': str(error)})
                except KeyError as error:
                    self.reply(404, {'error': str(cast(object, error.args[0]))})
                except sqlite3.Error:
                    self.close_connection = True
                    self.reply(500, {'error': 'Could not read the listing store. Check the gallery log.'})
                    raise
            else:
                target = (STATIC / ('index.html' if path == '/' else path.lstrip('/'))).resolve()
                if not target.is_relative_to(STATIC.resolve()) or not target.is_file():
                    self.reply(404, {'error': 'Page not found.'})
                    return
                content_type = {'.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html'}.get(target.suffix)
                self.send_bytes(200, target.read_bytes(), content_type or mimetypes.guess_type(target)[0] or 'application/octet-stream')

        def read_body(self) -> bytes | None:
            if 'Transfer-Encoding' in self.headers:
                return None
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                return None
            return self.rfile.read(length) if 0 <= length <= DISCARD_LIMIT else None

        def close_unread(self) -> None:
            self.close_connection = True
            deadline = time.monotonic() + CLOSE_SECONDS
            connection = cast(socket.socket, self.connection)
            try:
                connection.shutdown(socket.SHUT_WR)
                while (remaining := deadline - time.monotonic()) > 0:
                    connection.settimeout(remaining)
                    if not connection.recv(65536):
                        return
            except OSError:
                pass

        def do_POST(self) -> None:
            # Closing a socket with unread data resets the connection and the client loses the reply, so the
            # body is read before any check, and a body of unknown or excessive length is drained after the reply.
            content = self.read_body()
            if content is None:
                self.close_connection = True
            try:
                if not self.allowed():
                    return
                if self.headers.get_content_type() != 'application/json':
                    raise ValueError('Request must use application/json.')
                if content is None or not 0 < len(content) <= BODY_LIMIT:
                    raise ValueError(f'Request must declare a Content-Length from 1 to {BODY_LIMIT} bytes.')
                body = loads(content)
                if not is_object(body):
                    raise ValueError('Request must be an object.')
                parts = unquote(urlsplit(self.path).path).strip('/').split('/')
                if parts == ['api', 'stop']:
                    self.require_fields(body, set())
                    self.reply(200, {'stopping': True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                elif parts == ['api', 'chat']:
                    self.require_fields(body, {'text', 'request_id'})
                    message_id = chat.submit(body['text'], body['request_id'])
                    self.reply(202, {'message_id': message_id})
                    return
                elif parts == ['api', 'chat', 'control']:
                    if not set(body) <= {'message_id', 'prompt_id', 'answer', 'interrupt'} or 'message_id' not in body:
                        raise ValueError('Invalid conversation response.')
                    chat.respond(**body)
                elif parts == ['api', 'signals']:
                    self.require_fields(body, {'events'})
                    self.reply(200, {'recorded': store.record_signals(body['events'])})
                    return
                elif parts == ['api', 'compare', 'answer']:
                    self.require_fields(body, {'chosen', 'other'})
                    store.answer_pair(body['chosen'], body['other'])
                elif parts == ['api', 'compare', 'skip']:
                    self.require_fields(body, {'first', 'second'})
                    store.skip_pair(body['first'], body['second'])
                elif len(parts) == 4 and parts[:2] == ['api', 'hypotheses']:
                    self.require_fields(body, set())
                    decide_hypothesis(store, parts[2], parts[3])
                elif parts == ['api', 'rerank']:
                    self.require_fields(body, set())
                    if not rerank_lock.acquire(blocking=False):
                        self.reply(409, {'error': 'Scoring is already running. Wait for the new ranking.'})
                        return
                    try:
                        result = rerank(store)
                    except Exception:
                        traceback.print_exc()
                        status, result = 500, {'error': 'Could not score the listings. Try again. Check the gallery log if this continues.'}
                    else:
                        status = 200
                    finally:
                        rerank_lock.release()
                    self.reply(status, result)
                    return
                elif len(parts) == 4 and parts[:2] == ['api', 'listings']:
                    listing_id, action = parts[2:]
                    if action == 'feedback':
                        if not body or not set(body) <= {'favorite', 'dismissed'}:
                            raise ValueError('Feedback accepts favorite or dismissed.')
                        if any(type(value) is not bool for value in body.values()):
                            raise ValueError('Feedback must be true or false.')
                        store.feedback(listing_id, **body)
                    elif action == 'notes':
                        self.require_fields(body, {'text'})
                        store.add_note(body['text'], listing_id=listing_id)
                    elif action == 'contact':
                        self.require_fields(body, set())
                        store.request_contact(listing_id)
                    else:
                        raise KeyError('Action not found.')
                else:
                    raise KeyError('Action not found.')
                self.reply(200, {'saved': True})
            except (ValueError, UnicodeError) as error:
                self.reply(400, {'error': str(error)})
            except KeyError as error:
                self.reply(404, {'error': str(cast(object, error.args[0]))})
            except sqlite3.Error:
                self.close_connection = True
                self.reply(500, {'error': 'Could not save changes. Try again. Check the gallery log if this continues.'})
                raise
            finally:
                if content is None:
                    self.close_unread()

        @staticmethod
        def require_fields(body: Mapping[str, object], fields: set[str]) -> None:
            if set(body) != fields:
                raise ValueError('Request fields do not match this action.')

    server = GalleryServer(('127.0.0.1', port), Handler)
    server.access_token = access_token
    return server
