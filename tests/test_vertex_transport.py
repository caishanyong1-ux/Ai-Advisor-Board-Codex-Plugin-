"""Vertex wire contracts and disconnect boundaries; loopback only, no ADC or spend."""
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

from runtime.providers import CallFailed, Vertex
from runtime.streaming import HTTPConnection


class VertexTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aab-vertex-transport-")
        self.provider = Vertex(self.temp.name, auth=SimpleNamespace(token=lambda: "synthetic-token"))
        self.requests = []
        self.mode = "success"
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                fixture.requests.append(dict(self.headers))
                if fixture.mode == "before_headers":
                    self.close_connection = True
                    return
                if fixture.mode == "deadline":
                    time.sleep(.2)
                    self.close_connection = True
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                events = [{"candidates": [{"content": {"parts": [{"text": "Synthetic answer"}]}}]}]
                if fixture.mode != "partial":
                    events.append({"candidates": [{"finishReason": "STOP"}]})
                for event in events:
                    self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
                self.wfile.flush()
                self.close_connection = True

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()
        self.transport = patch("runtime.providers.HTTPSConnection", lambda host, timeout, context:
                               HTTPConnection("127.0.0.1", self.server.server_port, timeout=timeout))
        self.transport.start()

    def tearDown(self):
        self.transport.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, timeout=900, progress=None):
        return self.provider._request("aiplatform.googleapis.com", "/synthetic:streamGenerateContent?alt=sse",
                                      {"contents": "Synthetic"}, {"quota_project_id": "synthetic-project"},
                                      timeout=timeout, stream_family="gemini", progress=progress)

    def test_gemini_rest_propagates_each_calls_server_deadline(self):
        for timeout in (900, 120):
            self.assertEqual("STOP", self.request(timeout)["candidates"][0]["finishReason"])
        self.assertEqual(["900", "120"], [headers.get("X-Server-Timeout") for headers in self.requests])

    def test_close_before_headers_is_uncertain_and_not_replayed(self):
        self.mode = "before_headers"
        with self.assertRaises(CallFailed) as failed:
            self.request()
        self.assertTrue(failed.exception.uncertain)
        self.assertIn("RemoteDisconnected", str(failed.exception))
        self.assertIn("waiting for response headers", str(failed.exception))
        self.assertEqual(1, len(self.requests))

    def test_incomplete_stream_preserves_visible_output_without_retry(self):
        self.mode = "partial"
        progress = []
        with self.assertRaises(CallFailed) as failed:
            self.request(progress=progress.append)
        self.assertTrue(failed.exception.uncertain)
        self.assertIn("completion marker", str(failed.exception))
        self.assertEqual("Synthetic answer", progress[-1])
        self.assertEqual(1, len(self.requests))

    def test_local_deadline_still_stops_request_without_retry(self):
        self.mode = "deadline"
        started = time.monotonic()
        with self.assertRaises(CallFailed) as failed:
            self.request(timeout=.05)
        self.assertTrue(failed.exception.uncertain)
        self.assertLess(time.monotonic() - started, .5)
        self.assertEqual(1, len(self.requests))

    def test_real_connection_keeps_idle_socket_alive_before_first_response(self):
        conn = HTTPConnection('127.0.0.1', self.server.server_port, timeout=900)
        try:
            conn.connect()
            self.assertNotEqual(0, conn.sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE))
            idle = getattr(socket, 'TCP_KEEPIDLE', None)
            if idle is None and sys.platform == 'darwin':
                idle = getattr(socket, 'TCP_KEEPALIVE', 0x10)
            if idle is not None:
                self.assertEqual(30, conn.sock.getsockopt(socket.IPPROTO_TCP, idle))
            self.assertEqual(900, conn.sock.gettimeout())
            conn.request('POST', '/synthetic', body='{}')
            response = conn.getresponse()
            self.assertEqual(200, response.status)
            response.read()
        finally:
            conn.close()

    def test_optional_keepalive_options_do_not_break_transport(self):
        from runtime.streaming import enable_keepalive
        from unittest.mock import Mock
        sock = Mock()
        sock.setsockopt.side_effect = OSError('unsupported synthetic socket option')
        enable_keepalive(sock)


if __name__ == "__main__":
    unittest.main()
