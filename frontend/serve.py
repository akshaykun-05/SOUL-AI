"""Serve the static frontend and inject its API URL from the environment."""

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import os


FRONTEND_DIR = Path(__file__).resolve().parent


class FrontendHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND_DIR), **kwargs)

    def do_GET(self):
        if self.path == "/config.js":
            api_url = os.environ.get("SOUL_FRONTEND_API_URL", "http://127.0.0.1:8000/api")
            payload = ("window.SOUL_API_BASE_URL = " + json.dumps(api_url.rstrip("/")) + ";\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        super().do_GET()


if __name__ == "__main__":
    port = int(os.environ.get("SOUL_FRONTEND_PORT", "3000"))
    print(f"SOUL AI frontend: http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), FrontendHandler).serve_forever()
