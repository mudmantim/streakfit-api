"""uicheck's Browser must leave no Chrome profile behind in /tmp (which is RAM).

Each browser writes a ~40 MB profile. Removing it once Chrome's main process
had exited was not enough: child processes wrote Default/ back afterwards,
and E2E R1 agents left 14 such directories in one round. Measured on
1613dfd: 2 of 10 browsers leaked against a real page.

Drives real headless Chrome against a page served from this process, so it
is skipped where no Chrome is installed.
"""
import http.server
import os
import shutil
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "scripts"))

CHROME = any(shutil.which(b) for b in ("google-chrome", "google-chrome-stable",
                                       "chromium", "chromium-browser"))
pytestmark = pytest.mark.skipif(not CHROME, reason="no Chrome/Chromium on PATH")

PAGE = b"<!doctype html><title>t</title><script>fetch('/x').catch(()=>0)</script>ok"


class _Page(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *_):
        pass


def test_no_profile_survives_close():
    import uicheck
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    left = []
    try:
        for _ in range(10):
            b = uicheck.Browser()
            profile = b.profile
            b.goto(url, wait=1.0)
            b.close()
            time.sleep(1.5)
            if os.path.exists(profile):
                left.append(profile)
                shutil.rmtree(profile, ignore_errors=True)
    finally:
        server.shutdown()
    assert left == [], f"{len(left)} of 10 profiles survived close(): {left}"
