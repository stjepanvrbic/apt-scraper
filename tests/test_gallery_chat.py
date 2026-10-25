import asyncio
import time
import unittest

import test_gallery_server


async def gateway_reply(store, message_id, text, controls):
    store.update_chat(message_id, content='Which street?', prompt={'kind': 'clarify', 'prompt_id': 'question-1', 'question': 'Which street?', 'choices': ['Oak', 'Elm']})
    answer = await asyncio.to_thread(controls.get, True, 5)
    if answer != '/answer question-1 Oak':
        raise ValueError('Unexpected answer')
    store.update_chat(message_id, content='Oak Street it is.', status='complete', session_id='test-session')


class ChatHTTPTests(unittest.TestCase):
    def setUp(self):
        self.server_test = test_gallery_server.GalleryServerTests()
        self.server_test.setUp()
        self.addCleanup(self.server_test.doCleanups)

    def test_http_chat_and_choices_with_a_controlled_runner(self):
        from server import make_server
        import threading
        setup = self.server_test
        setup.close_server()
        setup.server = make_server(setup.store, port=0, chat_runner=gateway_reply)
        setup.thread = threading.Thread(target=setup.server.serve_forever, daemon=True)
        setup.thread.start()
        setup.url = f'http://127.0.0.1:{setup.server.server_port}'
        message_id = setup.request('/api/chat', {'text': 'I prefer quiet streets.', 'request_id': 'first-request'})['message_id']
        for _ in range(100):
            message = setup.request('/api/chat')['messages'][-1]
            if message['prompt']:
                break
            time.sleep(0.01)
        self.assertEqual(message['prompt']['choices'], ['Oak', 'Elm'])
        setup.request('/api/chat/control', {'message_id': message_id, 'prompt_id': 'question-1', 'answer': 'Oak'})
        for _ in range(100):
            message = setup.request('/api/chat')['messages'][-1]
            if message['status'] != 'running':
                break
            time.sleep(0.01)
        self.assertEqual(message['content'], 'Oak Street it is.')
        self.assertEqual(message['status'], 'complete')

    def test_reopening_reconnects_an_in_progress_reply_without_resending(self):
        from chat import ChatController
        store = self.server_test.store
        message_id = store.start_chat('Original message', 'resume-test')
        async def reconnect(store, identity, text, controls):
            self.assertEqual(identity, message_id)
            self.assertIsNone(text)
            store.update_chat(identity, content='Recovered reply', status='complete')
        controller = ChatController(store, reconnect)
        controller.thread.join(timeout=2)
        self.assertEqual(store.chat_state()['messages'][-1]['content'], 'Recovered reply')
        self.assertEqual(len(store.chat_state()['messages']), 2)
