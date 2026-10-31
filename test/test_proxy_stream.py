import os
import socket
import subprocess
import sys
import threading
import time
import unittest

HERE = os.path.dirname(__file__)
PROXY = os.path.join(HERE, "..", "proxy", "csswitch_proxy.py")
SEC = "streamtok"

# Use chunked framing so a truncated second chunk deterministically raises
# IncompleteRead. Wait for downstream headers before truncating the upstream,
# and consume the request body to avoid Windows resetting an unread socket.


def start_dropping_upstream():
    """假上游：chunked 传输，首块恰好填满一次 read(4096)（让代理先正常发出 200
    响应头），随后声明第二块但提前断连，逼代理在头已发出后的下一次 read 抛异常。"""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]
    downstream_headers = threading.Event()

    def serve():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            c.settimeout(5)
            # Drain the complete request before closing: on Windows, unread
            # request bytes can turn close() into an RST that discards chunk 1.
            request = b""
            while b"\r\n\r\n" not in request:
                part = c.recv(65536)
                if not part:
                    break
                request += part
            request_head, _, received_body = request.partition(b"\r\n\r\n")
            length = next((int(line.split(b":", 1)[1]) for line in request_head.split(b"\r\n")
                           if line.lower().startswith(b"content-length:")), 0)
            while len(received_body) < length:
                part = c.recv(length - len(received_body))
                if not part:
                    break
                received_body += part
            payload = ("event: content_block_delta\n"
                       "data: {\"type\":\"content_block_delta\"}\n\n")
            pad = ":" + "p" * (4096 - len(payload) - 2) + "\n"  # SSE 注释行，纯 padding
            payload += pad
            assert len(payload) == 4096, len(payload)
            head = ("HTTP/1.1 200 OK\r\n"
                    "Content-Type: text/event-stream\r\n"
                    "Transfer-Encoding: chunked\r\n\r\n")
            chunk1 = hex(len(payload))[2:] + "\r\n" + payload + "\r\n"
            chunk2_partial = "1f4\r\n" + "0123456789"  # 声明 500 字节，只发 10 字节就断
            c.sendall((head + chunk1).encode())
            # Exercise a midstream failure, not a race before response headers.
            downstream_headers.wait(5)
            c.sendall(chunk2_partial.encode())
            c.shutdown(socket.SHUT_WR)
            c.close()

    threading.Thread(target=serve, daemon=True).start()
    return f"http://127.0.0.1:{port}/up", srv, downstream_headers


def raw_post(host, port, path, body, downstream_headers):
    s = socket.create_connection((host, port), timeout=5)
    req = (f"POST {path} HTTP/1.1\r\nHost: {host}\r\n"
           f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n"
           f"Connection: close\r\n\r\n").encode() + body
    s.sendall(req)
    chunks = []
    while True:
        d = s.recv(65536)
        if not d:
            break
        chunks.append(d)
        if b"\r\n\r\n" in b"".join(chunks):
            downstream_headers.set()
    s.close()
    return b"".join(chunks)


class StreamInterruption(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.up_url, cls.up_sock, cls.downstream_headers = start_dropping_upstream()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            cls.port = listener.getsockname()[1]
        env = dict(os.environ, DEEPSEEK_API_KEY="fake",
                   CSSWITCH_UPSTREAM_URL=cls.up_url)
        cls.proc = subprocess.Popen(
            [sys.executable, PROXY, "--provider", "deepseek",
             "--port", str(cls.port), "--auth-token", SEC],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.0)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=5)
        cls.up_sock.close()

    def test_midstream_error_yields_clean_sse_not_spliced_json(self):
        body = b'{"model":"claude-opus-4-8","max_tokens":10,"stream":true,' \
               b'"messages":[{"role":"user","content":"hi"}]}'
        raw = raw_post("127.0.0.1", self.port, f"/{SEC}/v1/messages", body, self.downstream_headers)
        head, _, tail = raw.partition(b"\r\n\r\n")
        self.assertIn(b"200", head.split(b"\r\n")[0])          # 头已发 200
        self.assertIn(b"event: error", tail)                   # 干净的 SSE 错误帧
        self.assertTrue(raw.rstrip().endswith(b"0"))           # chunked 终止块
        self.assertNotIn(b"HTTP/1.1 502", tail)                # 未把 502 拼进流


if __name__ == "__main__":
    unittest.main()
