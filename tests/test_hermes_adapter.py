import asyncio
import tempfile
import types
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

from test_gallery_store import ListingStore
from chat import Controls, run_turn


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
            self.assertIn('update_settings', submitted[0])
