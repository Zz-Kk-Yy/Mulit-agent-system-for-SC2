import json
import socketserver
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler

from local_joint_debug import cancel_chat_request, cancel_verdict
from local_joint_judge import build_chat_request


class _HangHandler(BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        type(self).received.append(raw)
        time.sleep(30)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"choices":[{"message":{"content":"{\\"actions\\":[]}"}}]}')

    def log_message(self, format, *args):
        return


class _Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


class CancelChatTests(unittest.TestCase):
    def test_client_closes_a_hanging_request_and_does_not_execute(self):
        _HangHandler.received = []
        server = _Server(("127.0.0.1", 0), _HangHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        try:
            request = build_chat_request({"game": {"game_time_s": 92.9}}, max_tokens=6144)
            started = time.monotonic()
            outcome = cancel_chat_request(f"http://127.0.0.1:{port}", request, 0.4)
            elapsed = time.monotonic() - started
            verdict = cancel_verdict(outcome)
        finally:
            server.shutdown()
            server.server_close()
        self.assertLess(elapsed, 3)
        self.assertTrue(outcome["closed_by_client"])
        self.assertIsNone(outcome["http_status"])
        self.assertEqual(outcome["bytes_read"], 0)
        self.assertGreaterEqual(outcome["elapsed_send_to_disconnect_s"], 0.3)
        self.assertLess(outcome["elapsed_send_to_disconnect_s"], 1.5)
        self.assertEqual(verdict["decision"], "已断开")
        self.assertFalse(verdict["executed"])
        self.assertEqual(verdict["actions_from_content"], [])
        self.assertEqual(len(_HangHandler.received), 1)
        sent = json.loads(_HangHandler.received[0].decode("utf-8"))
        self.assertEqual(set(sent), {"model", "messages", "max_tokens"})
        self.assertEqual(sent["model"], "swarmbrain-qwen3-4b")
        self.assertEqual(sent["max_tokens"], 6144)
        self.assertNotIn("stop", sent)
        self.assertNotIn("thinking_token_budget", sent)

    def test_a_json_body_is_still_not_executed(self):
        verdict = cancel_verdict(
            {
                "closed_by_client": True,
                "http_status": 200,
                "body_text": '{"actions":[{"type":"expand","base_id":"A5"}]}',
            }
        )
        self.assertEqual(verdict["decision"], "已断开")
        self.assertFalse(verdict["executed"])
        self.assertEqual(verdict["actions_from_content"], [])


if __name__ == "__main__":
    unittest.main()
