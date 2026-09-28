import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


class Site:
    """A throwaway HTTP server whose answers come from a {path: (status, location)} table."""

    def __init__(self):
        self.routes: dict[str, tuple[int, str | None]] = {}
        self.seen: list[tuple[str, str, dict]] = []
        self.refuse_head = False
        site = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self):
                site.seen.append((self.command, self.path, dict(self.headers)))
                if self.command == "HEAD" and site.refuse_head:
                    self.send_response(405)
                    self.end_headers()
                    return
                status, loc = site.routes.get(self.path, (404, None))
                self.send_response(status)
                if loc is not None:
                    self.send_header("Location", loc)
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_HEAD = _answer
            do_GET = _answer

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def site():
    s = Site()
    yield s
    s.close()


@pytest.fixture
def other_site():
    s = Site()
    yield s
    s.close()
