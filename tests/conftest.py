"""Controlled HTTP fixture with interchangeable states and no internet access."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def block_external_http(monkeypatch):
    """Fail immediately if a test attempts HTTP outside the fixture host."""
    original_send = requests.Session.send

    def send(session, request, **kwargs):
        assert urlsplit(request.url).hostname == "127.0.0.1", request.url
        return original_send(session, request, **kwargs)

    monkeypatch.setattr(requests.Session, "send", send)


@pytest.fixture
def fixture_site():
    site = SimpleNamespace(state="site_v1", requests=[], routes={})

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            site.requests.append((self.path, self.headers.get("User-Agent")))
            if self.path in site.routes:
                status, headers, body = site.routes[self.path]
            else:
                path = urlsplit(self.path).path
                path = "/index.html" if path == "/" else path
                file_path = FIXTURES / site.state / path.lstrip("/")
                if file_path.is_file():
                    status = 200
                    headers = {"Content-Type": "text/html; charset=utf-8"}
                    body = file_path.read_text().replace("{{PORT}}", str(self.server.server_port))
                else:
                    status, headers, body = 404, {}, "Not found"
            payload = body.encode("utf-8")
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    site.url = f"http://127.0.0.1:{server.server_port}"
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield site
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
