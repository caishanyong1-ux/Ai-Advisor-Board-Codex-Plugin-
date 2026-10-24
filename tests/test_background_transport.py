"""Real loopback sockets for background deadline/cancel boundaries; no cloud."""
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

from runtime.background import Background, RequestError
from runtime.streaming import HTTPConnection


class BackgroundWireTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='aab-background-wire-')
        self.backend = Background(self.temp.name, SimpleNamespace(token=lambda: 'synthetic-token'))
        self.backend.read_timeout = .03
        self.delay = 0
        self.requests = []
        fixture = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def handle_request(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                fixture.requests.append((self.command, self.path, dict(self.headers), body))
                time.sleep(fixture.delay)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Connection', 'close')
                self.end_headers()
                try:
                    self.wfile.write(b'{"id":"synthetic_id","status":"in_progress"}')
                except (BrokenPipeError, ConnectionResetError):
                    pass
            do_POST = do_GET = do_DELETE = handle_request
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        self.thread.start()
        self.transport = patch('runtime.background.HTTPSConnection', lambda host, timeout, context: HTTPConnection('127.0.0.1', self.server.server_port, timeout=timeout))
        self.transport.start()
        self.value = {'member': {'vertex_target': {'project_id': 'synthetic-project', 'quota_project_id': 'synthetic-quota'}}}

    def tearDown(self):
        self.transport.stop()
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.temp.cleanup()

    def test_create_uses_approved_deadline_not_short_poll_timeout(self):
        self.delay = .08
        body = {'background': True, 'store': True}
        result = self.backend.request(self.value, 'POST', body=body, not_after=time.time() + 2)
        self.assertEqual('synthetic_id', result['id'])
        method, path, headers, raw = self.requests[0]
        self.assertEqual('POST', method)
        self.assertEqual('/v1beta1/projects/synthetic-project/locations/global/interactions', path)
        self.assertEqual('2', headers['X-Server-Timeout'])
        self.assertEqual('synthetic-quota', headers['x-goog-user-project'])
        self.assertEqual(body, json.loads(raw))

    def test_get_is_bounded_and_local_timeout_is_identified(self):
        self.delay = .15
        with self.assertRaises(RequestError) as failed:
            self.backend.request(self.value, 'GET', '/synthetic_id')
        self.assertRegex(str(failed.exception), 'Local (deadline expired|request timeout)')
        self.assertEqual(1, len(self.requests))

    def test_expired_or_cancelled_before_auth_sends_nothing(self):
        event = threading.Event(); event.set()
        with self.assertRaises(RequestError) as failed:
            self.backend.request(self.value, 'POST', body={}, cancelled=event)
        self.assertTrue(failed.exception.not_dispatched)
        with self.assertRaises(RequestError) as failed:
            self.backend.request(self.value, 'POST', body={}, not_after=0)
        self.assertTrue(failed.exception.not_dispatched)
        self.assertFalse(self.requests)

    def test_cancel_during_adc_refresh_sends_nothing(self):
        event = threading.Event()
        def token():
            event.set()
            return 'synthetic-token'
        self.backend.auth.token = token
        with self.assertRaises(RequestError) as failed:
            self.backend.request(self.value, 'POST', body={}, not_after=time.time() + 2, cancelled=event)
        self.assertTrue(failed.exception.not_dispatched)
        self.assertFalse(self.requests)

    def test_deadline_expiring_during_adc_refresh_sends_nothing(self):
        def token():
            time.sleep(.04)
            return 'synthetic-token'
        self.backend.auth.token = token
        with self.assertRaises(RequestError) as failed:
            self.backend.request(self.value, 'POST', body={}, not_after=time.time() + .01)
        self.assertTrue(failed.exception.not_dispatched)
        self.assertFalse(self.requests)
