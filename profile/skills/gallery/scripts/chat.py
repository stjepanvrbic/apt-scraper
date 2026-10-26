import asyncio
import contextlib
import queue
import threading
from pathlib import Path
from uuid import uuid4


class Controls(queue.Queue):
    def __init__(self):
        super().__init__()
        self.wake = None

    def put(self, item, block=True, timeout=None):
        super().put(item, block, timeout)
        if self.wake:
            self.wake()


class ChatController:
    def __init__(self, store, runner=None):
        self.store = store
        self.runner = runner or run_turn
        self.controls = Controls()
        self.lock = threading.Lock()
        self.thread = None
        self.answered = set()
        active = next((message for message in store.chat_state()['messages'] if message['status'] == 'running'), None)
        if active:
            self.thread = threading.Thread(target=self._run, args=(active['id'], None), daemon=True)
            self.thread.start()

    def submit(self, text, request_id):
        with self.lock:
            message_id = self.store.start_chat(text, request_id)
            self.controls = Controls()
            self.answered = set()
            self.thread = threading.Thread(target=self._run, args=(message_id, text), daemon=True)
            self.thread.start()
            return message_id

    def _run(self, message_id, text):
        try:
            asyncio.run(self.runner(self.store, message_id, text, self.controls))
        except Exception as error:
            self.store.update_chat(message_id, status='error', error=str(error), prompt=None)

    def respond(self, message_id, prompt_id=None, answer=None, interrupt=False):
        if type(interrupt) is not bool:
            raise ValueError('Interrupt must be true or false.')
        messages = self.store.chat_state()['messages']
        message = next((item for item in messages if item['id'] == message_id and item['status'] == 'running'), None)
        if not message:
            raise ValueError('This reply is no longer running.')
        if interrupt:
            self.controls.put('/stop')
            return
        prompt = message['prompt']
        if not prompt or prompt['prompt_id'] != prompt_id:
            raise ValueError('This question is no longer waiting for an answer.')
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 20000:
            raise ValueError('An answer is required.')
        if prompt['kind'] == 'approval' and answer not in prompt['choices']:
            raise ValueError('Choose one of the available approval responses.')
        command = '/approve' if prompt['kind'] == 'approval' else '/answer'
        with self.lock:
            if prompt_id in self.answered:
                raise ValueError('This answer was already submitted.')
            self.answered.add(prompt_id)
            self.controls.put(f'{command} {prompt_id} {answer}')


async def run_turn(store, message_id, text, controls):
    try:
        from hermes_cli.gateway_client import connect_gateway
        from hermes_cli.gateway_chat_view import GatewayChatView
        from hermes_constants import get_hermes_home
    except ImportError as error:
        raise RuntimeError('Notes needs the Hermes Python environment. Open this gallery through hermes -p apt-scraper.') from error
    profile_home = Path(get_hermes_home())
    if profile_home.name != 'apt-scraper':
        raise RuntimeError('Notes must use the apt-scraper Hermes profile.')

    class Events:
        def __init__(self):
            self.content = ''
            self.tools = []
            self.dirty = False
            self.timer = None

        def schedule(self):
            self.dirty = True
            if self.timer is None:
                self.timer = asyncio.get_running_loop().call_later(0.1, self.flush)

        def flush(self, **changes):
            if self.timer is not None:
                self.timer.cancel()
                self.timer = None
            if self.dirty or changes:
                store.update_chat(message_id, content=self.content, events=self.tools, **changes)
                self.dirty = False

        def on_text_delta(self, text):
            self.content += text or ''
            self.schedule()

        def on_tool_progress(self, event_type, tool_name=None, preview=None, args=None, **values):
            identity = values.get('tool_call_id') or tool_name
            if event_type == 'tool.started':
                self.tools.append({'id': identity, 'name': tool_name, 'input': args, 'status': 'running'})
            else:
                tool = next((item for item in reversed(self.tools) if item['id'] == identity), None)
                if tool:
                    tool.update(status='error' if values.get('is_error') else 'complete', output=str(values.get('result') or '')[:5000])
            self.schedule()

    events = Events()

    class GalleryView(GatewayChatView):
        def show_prompt(self, prompt):
            events.flush(prompt=prompt)

    async with connect_gateway() as client:
        session_id = store.chat_state()['session_id']
        if session_id:
            snapshot = await client.rpc('session.resume', session_id=session_id)
        else:
            # Untitled: a titled create resumes whichever session already holds the title. The profile home
            # outlives every gallery database's folder, and a session runs turns only in the folder it was created with.
            snapshot = await client.rpc('session.create', request_id=uuid4().hex, source='cli', cwd=str(profile_home))
        store.update_chat(message_id, session_id=snapshot['stored_session_id'])
        if text is not None and snapshot.get('pending'):
            raise RuntimeError('This Hermes session still has unfinished work. Wait for it to finish before sending another message.')
        view = GalleryView(client, snapshot, quiet=True, emitter=events)
        loop = asyncio.get_running_loop()
        controls.wake = lambda: loop.call_soon_threadsafe(view.changed.set)
        renderer = asyncio.create_task(view.render())
        try:
            context = (f'This conversation is in the apartment gallery. Its listing database is {store.path}. '
                       'Read the gallery skill when relevant. Search settings are kept only in the profile listing store, which may be another database. When the user states or changes criteria, '
                       'preferences, places, location priorities, listing sites or viewing availability, save the change with the settings script of the interview skill before you confirm it. '
                       'Reply here and do not duplicate replies with the note command.\n\n')
            if text is None:
                message = next(item for item in store.chat_state()['messages'] if item['id'] == message_id)
                admission = message['admission_id']
                if not admission:
                    raise RuntimeError('The gallery restarted before Hermes confirmed admission. Your message was not resent. Ask Hermes to check the conversation before retrying.')
                receipt = await client.rpc('prompt.receipt', session_id=view.session_id, admission_id=admission, include_result=True)
                if receipt.get('status') == 'terminal':
                    result = receipt.get('result') or {}
                    store.update_chat(message_id, content=result.get('final_response') or '', status='error' if result.get('failed') else 'complete', error=result.get('error'), prompt=None)
                    return
                if receipt.get('status') == 'unknown':
                    raise RuntimeError('Hermes reports an unknown execution outcome. The gallery did not resend the message.')
            else:
                receipt = await view.submit(context + text)
                admission = receipt['admission_id']
                store.update_chat(message_id, admission_id=admission)
            previous_prompt = object()
            while admission not in view.completions:
                view.changed.clear()
                if view.failure:
                    raise view.failure
                while not controls.empty():
                    await view.command(controls.get_nowait())
                prompt = next(iter(view.prompts.values()), None)
                if prompt != previous_prompt:
                    events.flush(prompt=prompt)
                    previous_prompt = prompt
                if admission not in view.completions:
                    await view.changed.wait()
            terminal = view.completions[admission]
            result = await client.rpc('prompt.receipt', session_id=view.session_id, admission_id=admission, include_result=True)
            failure = (result.get('result') or {}).get('error')
            if terminal.get('outcome') not in ('completed', 'cancelled'):
                failure = failure or 'Hermes could not complete the reply.'
            events.flush()
            store.update_chat(message_id, content=terminal.get('text') or terminal.get('content') or '', prompt=None,
                              status='error' if failure else 'complete', error=failure, session_id=view.session_id)
        finally:
            controls.wake = None
            events.flush()
            renderer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await renderer
