"""Serve the static frontend and inject its API URL from the environment."""

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import os


FRONTEND_DIR = Path(__file__).resolve().parent
API_CONFIG = FRONTEND_DIR.parent / "dist" / "api-config.js"


class FrontendHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND_DIR), **kwargs)

    def do_GET(self):
        if self.path == "/config.js":
            payload = API_CONFIG.read_bytes()
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
