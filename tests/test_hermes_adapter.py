import asyncio
import shutil
import sqlite3
import tempfile
import types
import unittest
from contextlib import asynccontextmanager, closing
from pathlib import Path
from unittest.mock import patch

from test_gallery_store import ListingStore
from chat import Controls, run_turn
from schema import MIGRATIONS, notes_session_per_database


class HermesAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_turn_streams_preserves_approval_and_submits_denial(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ListingStore(Path(directory) / 'listings.sqlite3')
            identity = store.start_chat('Test request', 'test-request')
            commands = []
            submitted = []
            class Client:
                async def rpc(self, method, **arguments):
                    if method == 'session.create':
                        return {'stored_session_id': 'session', 'pending': []}
                    return {'status': 'terminal', 'result': {'final_response': 'Action denied.'}}
            @asynccontextmanager
            async def connect():
                yield Client()
            class View:
                def __init__(self, client, snapshot, quiet, emitter):
                    self.session_id = 'session'
                    self.emitter = emitter
                    self.changed = asyncio.Event()
                    self.completions = {}
                    self.prompts = {}
                    self.failure = None
                async def submit(self, text):
                    submitted.append(text)
                    return {'admission_id': 'admission'}
                async def render(self):
                    for _ in range(100):
                        self.emitter.on_text_delta('x')
                    prompt = {'kind': 'approval', 'prompt_id': 'approval', 'choices': ['once', 'deny'], 'execution_generation': 1}
                    self.prompts['approval'] = prompt
                    self.show_prompt(prompt)
                    self.emitter.on_text_delta('Waiting')
                    self.changed.set()
                    await asyncio.Event().wait()
                async def command(self, command):
                    commands.append(command)
                    self.prompts.clear()
                    self.completions['admission'] = {'outcome': 'completed', 'text': 'Action denied.'}
                    self.changed.set()
            modules = {
                'hermes_cli.gateway_client': types.SimpleNamespace(connect_gateway=connect),
                'hermes_cli.gateway_chat_view': types.SimpleNamespace(GatewayChatView=View),
                'hermes_constants': types.SimpleNamespace(get_hermes_home=lambda: Path('/profiles/apt-scraper')),
            }
            controls = Controls()
            with patch.dict('sys.modules', modules), patch.object(store, 'update_chat', wraps=store.update_chat) as writes:
                task = asyncio.create_task(run_turn(store, identity, 'Test request', controls))
                for _ in range(100):
                    await asyncio.sleep(0.01)
                    if store.chat_state()['messages'][-1]['prompt']:
                        break
                self.assertIsNotNone(store.chat_state()['messages'][-1]['prompt'])
                controls.put('/approve approval deny')
                await asyncio.wait_for(task, 2)
                self.assertLess(writes.call_count, 15)
            message = store.chat_state()['messages'][-1]
            self.assertEqual(message['content'], 'Action denied.')
            self.assertIsNone(message['prompt'])
            self.assertEqual(commands, ['/approve approval deny'])
            self.assertIn('interview skill', submitted[0])
            self.assertNotIn('update_settings', submitted[0])

    async def test_each_database_has_its_own_session_that_outlives_its_folder(self):
        sessions = {}

        class Client:
            async def rpc(self, method, **arguments):
                if method == 'session.create':
                    # Hermes 0.22.5 resumes the session that holds a requested title instead of creating one.
                    titled = next((identity for identity, session in sessions.items()
                                   if arguments.get('title') and session['title'] == arguments['title']), None)
                    identity = titled or f'session-{len(sessions)}'
                    sessions.setdefault(identity, {'title': arguments.get('title'), 'cwd': arguments['cwd']})
                    return {'stored_session_id': identity, 'pending': []}
                if method == 'session.resume':
                    return {'stored_session_id': arguments['session_id'], 'pending': []}
                return {'result': {'error': sessions[arguments['session_id']].get('error')}}

        @asynccontextmanager
        async def connect():
            yield Client()

        class View:
            def __init__(self, client, snapshot, quiet, emitter):
                self.session_id = snapshot['stored_session_id']
                self.changed = asyncio.Event()
                self.completions = {}
                self.prompts = {}
                self.failure = None

            async def submit(self, text):
                session = sessions[self.session_id]
                # A session runs turns only in the working folder it was created with.
                session['error'] = None if Path(session['cwd']).is_dir() else 'gateway.errors.workspace_unavailable'
                self.completions['admission'] = {'outcome': 'failed' if session['error'] else 'completed', 'text': 'OK'}
                return {'admission_id': 'admission'}

            async def render(self):
                await asyncio.Event().wait()

        async def turn(store):
            identity = store.start_chat('Hello', f'request-{store.chat_state()["total"]}')
            await run_turn(store, identity, 'Hello', Controls())
            return store.chat_state()['messages'][-1]

        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / 'profiles' / 'apt-scraper'
            profile.mkdir(parents=True)
            modules = {
                'hermes_cli.gateway_client': types.SimpleNamespace(connect_gateway=connect),
                'hermes_cli.gateway_chat_view': types.SimpleNamespace(GatewayChatView=View),
                'hermes_constants': types.SimpleNamespace(get_hermes_home=lambda: profile),
            }
            first_folder = Path(directory) / 'first'
            other_folder = Path(directory) / 'other'
            first_folder.mkdir()
            other_folder.mkdir()
            sample = ListingStore(first_folder / 'sample.sqlite3')
            real = ListingStore(other_folder / 'listings.sqlite3')
            with patch.dict('sys.modules', modules):
                await turn(sample)
                await turn(real)
                self.assertNotEqual(sample.chat_state()['session_id'], real.chat_state()['session_id'])
                moved_folder = Path(directory) / 'moved'
                moved_folder.mkdir()
                shutil.copy2(sample.path, moved_folder / 'sample.sqlite3')
                shutil.rmtree(first_folder)
                moved = ListingStore(moved_folder / 'sample.sqlite3')
                for store in (moved, real):
                    message = await turn(store)
                    self.assertEqual((message['status'], message['error'], message['content']), ('complete', None, 'OK'))
            self.assertEqual(len(sessions), 2)

    async def test_a_session_written_back_by_a_process_that_predates_the_migration_is_not_resumed(self):
        calls = []

        class Client:
            async def rpc(self, method, **arguments):
                calls.append((method, arguments.get('session_id')))
                if method == 'session.create':
                    return {'stored_session_id': 'local-own', 'pending': []}
                if method == 'session.resume':
                    return {'stored_session_id': arguments['session_id'], 'pending': []}
                return {'result': {}}

        @asynccontextmanager
        async def connect():
            yield Client()

        class View:
            def __init__(self, client, snapshot, quiet, emitter):
                self.session_id = snapshot['stored_session_id']
                self.changed = asyncio.Event()
                self.completions = {}
                self.prompts = {}
                self.failure = None

            async def submit(self, text):
                self.completions['admission'] = {'outcome': 'completed', 'text': 'OK'}
                return {'admission_id': 'admission'}

            async def render(self):
                await asyncio.Event().wait()

        def write_shared_session(path):
            # The write a gallery server started before the update makes when its turn finishes.
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("UPDATE chat_session SET session_id = 'local-shared-titled' WHERE id = 1")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'listings.sqlite3'
            with closing(sqlite3.connect(path)) as connection, connection:
                for index, migration in enumerate(MIGRATIONS[:MIGRATIONS.index(notes_session_per_database)]):
                    migration(connection)
                    connection.execute(f'PRAGMA user_version = {index + 1}')
            write_shared_session(path)
            store = ListingStore(path)
            modules = {
                'hermes_cli.gateway_client': types.SimpleNamespace(connect_gateway=connect),
                'hermes_cli.gateway_chat_view': types.SimpleNamespace(GatewayChatView=View),
                'hermes_constants': types.SimpleNamespace(get_hermes_home=lambda: Path(directory) / 'apt-scraper'),
            }
            with patch.dict('sys.modules', modules):
                for turn in range(2):
                    write_shared_session(path)
                    identity = store.start_chat('Hello', f'request-{turn}')
                    await run_turn(store, identity, 'Hello', Controls())
                    write_shared_session(path)
            self.assertEqual(store.schema_version, len(MIGRATIONS))
            self.assertEqual(store.chat_state()['session_id'], 'local-own')
            sessions = [call for call in calls if call[0] in ('session.create', 'session.resume')]
            self.assertEqual(sessions, [('session.create', None), ('session.resume', 'local-own')])
