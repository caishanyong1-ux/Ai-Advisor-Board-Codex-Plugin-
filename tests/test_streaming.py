import http.client
import io
import json
import socket
import threading
import time
import unittest

from runtime.streaming import read_response, StreamError, close_connection, deadline, HTTPConnection
from runtime.providers import CallFailed
from test_board import BoardFixture


def frame(data):
    return ("data: " + (data if isinstance(data,str) else json.dumps(data, ensure_ascii=False)) + "\r\n\r\n").encode()


class Response:
    def __init__(self, data, chunk=3, content_type="text/event-stream"):
        self.data, self.chunk, self.content_type = io.BytesIO(data), chunk, content_type
    def getheader(self, name, default=""): return self.content_type
    def read1(self, count): return self.data.read(min(self.chunk,count))
    def read(self, count): return self.data.read(count)


class StreamTests(unittest.TestCase):
    def test_deadline_or_cancel_during_connect_never_sends_a_late_post(self):
        for cancel in (False,True):
            left,right=socket.socketpair()
            conn=HTTPConnection("synthetic.invalid",timeout=1)
            class ConnectedSocket:
                def setsockopt(self,*args):pass
                def __getattr__(self,name):return getattr(left,name)
            def delayed(*args,**kwargs):
                time.sleep(.07)
                return ConnectedSocket()
            conn._create_connection=delayed
            timer=threading.Timer(.02,close_connection,args=(conn,)) if cancel else deadline(conn,.02)
            if cancel:timer.start()
            with self.assertRaises(StreamError):conn.request("POST","/paid",body=b"must not transmit")
            right.settimeout(.2)
            self.assertEqual(b"",right.recv(4096))
            with self.assertRaises(StreamError):conn.request("POST","/paid",body=b"no reconnect")
            timer.cancel();left.close();right.close()
    def test_chat_utf8_crlf_usage_and_completion(self):
        events = b": heartbeat\r\n\r\n" + frame({"choices":[{"delta":{"content":"你好"}}]})
        events += frame({"choices":[{"delta":{},"finish_reason":"stop"}]})
        events += frame({"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":5,"cost_in_usd_ticks":123}}) + frame("[DONE]")
        checkpoints=[]
        raw=read_response(Response(events,1),"chat",checkpoints.append)
        self.assertEqual("你好",raw["choices"][0]["message"]["content"])
        self.assertEqual(123,raw["usage"]["cost_in_usd_ticks"])
        self.assertIn("你好",checkpoints)

    def test_eof_error_malformed_and_missing_final_reason_are_not_success(self):
        prefix=frame({"choices":[{"delta":{"content":"Partial"}}]})
        for tail in (b"",frame("[DONE]"),frame("{broken"),frame({"error":{"message":"bad"}}),b"data: {",frame({"choices":[{"delta":{},"finish_reason":"stop"}]})):
            checkpoints=[]
            with self.subTest(tail=tail), self.assertRaises(StreamError):
                read_response(Response(prefix+tail),"chat",checkpoints.append)
            self.assertEqual("Partial",checkpoints[-1])

    def test_native_gemini_and_claude_terminal_usage_and_hidden_reasoning(self):
        gemini = frame({"candidates":[{"content":{"parts":[{"text":"HIDDEN","thought":True},{"text":"Visible"}]}}]})
        gemini += frame({"candidates":[{"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":4,"thoughtsTokenCount":8,"candidatesTokenCount":2}})
        raw=read_response(Response(gemini),"gemini")
        self.assertEqual("Visible",raw["candidates"][0]["content"]["parts"][0]["text"])
        self.assertEqual(8,raw["usageMetadata"]["thoughtsTokenCount"])
        claude = frame({"type":"message_start","message":{"id":"msg_test","usage":{"input_tokens":4}}})
        claude += frame({"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":"HIDDEN"}})
        claude += frame({"type":"content_block_delta","delta":{"type":"text_delta","text":"Visible"}})
        claude += frame({"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":10}})
        checkpoints=[]
        with self.assertRaises(StreamError): read_response(Response(claude),"claude",checkpoints.append)
        raw=read_response(Response(claude+frame({"type":"message_stop"})),"claude",checkpoints.append)
        self.assertEqual({"input_tokens":4,"output_tokens":10},raw["usage"])
        self.assertEqual("Visible",raw["content"][0]["text"])
        self.assertNotIn("HIDDEN",str(checkpoints))

    def test_credentials_and_reasoning_split_across_events_never_checkpoint(self):
        secret="xai-synthetic-credential"
        text="Start <think>HIDDEN</think> " + secret + " End"
        events=b"".join(frame({"choices":[{"delta":{"content":c,"reasoning_content":"HIDDEN"}}]}) for c in text)
        events+=frame({"choices":[{"delta":{},"finish_reason":"stop"}]})+frame("[DONE]")
        checkpoints=[]
        raw=read_response(Response(events,11),"chat",checkpoints.append,secret)
        self.assertNotIn(secret,str(checkpoints))
        self.assertNotIn("HIDDEN",str(checkpoints))
        self.assertIn("[redacted credential]",raw["choices"][0]["message"]["content"])

    def test_same_response_json_compatibility_does_not_require_replay(self):
        response=Response(b'{"choices":[{"message":{"content":"Done"},"finish_reason":"stop"}]}',content_type="application/json")
        self.assertEqual("stop",read_response(response,"chat")["choices"][0]["finish_reason"])

    def test_absolute_timeout_and_cancel_close_detached_response_socket(self):
        for use_deadline in (True,False):
            left,right=socket.socketpair()
            conn=http.client.HTTPConnection("synthetic.invalid")
            conn.sock=left
            conn._aab_response_socket=left
            conn._HTTPConnection__state=http.client._CS_REQ_SENT
            right.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n")
            response=conn.getresponse()
            self.assertIsNone(conn.sock)
            def writer():
                try:
                    for _ in range(50):
                        right.sendall(frame({"choices":[{"delta":{"content":"x"}}]}))
                        time.sleep(.01)
                except OSError: pass
                finally: right.close()
            thread=threading.Thread(target=writer);thread.start()
            start=time.monotonic()
            timer=deadline(conn,.06) if use_deadline else threading.Timer(.06,close_connection,args=(conn,))
            if not use_deadline:timer.start()
            with self.assertRaises((StreamError,OSError)):
                read_response(response,"chat")
            self.assertLess(time.monotonic()-start,.35)
            timer.cancel();response.close();left.close();thread.join()


class StreamPersistenceTests(BoardFixture):
    def test_one_visible_delta_is_saved_even_when_provider_then_stalls(self):
        original=self.mock.call
        release=threading.Event()
        def stalled(member,messages,timeout,call_id,cancelled,progress=None):
            if member["model"]=="mock/one" and "SHARED SCORING RUBRIC" not in messages[-1]["content"]:
                progress("")
                progress("Only one visible delta")
                release.wait(3)
            return original(member,messages,timeout,call_id,cancelled,progress)
        self.mock.call=stalled
        proposal=self.prepare();self.approve(proposal)
        try:
            for _ in range(150):
                with self.board.store.connect() as db:
                    row=db.execute("SELECT * FROM attempts WHERE model='mock/one'").fetchone()
                if row and row["content"]=="Only one visible delta":break
                time.sleep(.01)
            self.assertEqual("running",row["state"])
            self.assertEqual("Only one visible delta",row["content"])
        finally:release.set()
        self.wait(proposal["job_id"])
    def test_partial_checkpoint_survives_failure_and_never_counts_as_a_vote(self):
        original=self.mock.call
        def broken(member,messages,timeout,call_id,cancelled,progress=None):
            if member["model"]=="mock/one":
                progress("A durable partial answer")
                progress("A durable partial answer ending mid-sentence")
                raise CallFailed("Synthetic lost stream",True)
            return original(member,messages,timeout,call_id,cancelled,progress)
        self.mock.call=broken
        proposal=self.prepare();self.approve(proposal)
        result=self.wait(proposal["job_id"])
        self.assertEqual("needs_attention",result["state"])
        with self.board.store.connect() as db:
            row=db.execute("SELECT * FROM attempts WHERE model='mock/one'").fetchone()
            self.assertEqual("uncertain",row["state"])
            self.assertIsNone(row["charged"])
            self.assertEqual("A durable partial answer ending mid-sentence",row["content"])
            self.assertTrue(json.loads(row["usage"])["partial"])
            self.assertEqual(0,db.execute("SELECT COUNT(*) FROM attempts WHERE stage='review'").fetchone()[0])
