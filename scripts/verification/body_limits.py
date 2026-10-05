#!/usr/bin/env python3
"""The request-body size boundary (D64), checked from outside.

Every route takes at most 256 KB (photo upload 2 MB). That was enforced from
the Content-Length header only, so a CHUNKED upload -- no Content-Length --
skipped it, and Werkzeug's bounded stream silently kept its first 2 MB: valid
JSON plus padding registered users and posted messages. These checks send a
chunked body over the real network path, so they also cover whatever sits in
front of the app (a proxy that de-chunks and adds Content-Length must still
end in a 413).

Production-safe: unauthenticated PATCH /api/me, which has no rate limit and
no side effect -- the over-limit body must be refused for SIZE before the
missing token is even looked at, and a small one must get as far as the
token check (401). Nothing is created or changed. The over-limit body is
exactly one byte over, so the server reads all of it before answering.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verification._client import run_module_standalone

LIMIT = 256 * 1024


def _padded(total):
    raw = json.dumps({"display_name": "qa_smoke_body"}).encode()
    return raw + b" " * (total - len(raw))


def run(api, results, scenario):
    status, payload = api.request("PATCH", "/api/me", raw_body=_padded(LIMIT + 1),
                                  content_type="application/json", chunked=True)
    results.check("body.chunked_over_limit_is_refused_for_size",
                  status == 413 and (payload or {}).get("error") == "payload_too_large",
                  f"status={status} payload={payload}")

    status, payload = api.request("PATCH", "/api/me", raw_body=_padded(1024),
                                  content_type="application/json", chunked=True)
    results.check("body.chunked_under_limit_reaches_the_route",
                  status == 401, f"status={status} payload={payload}")
    return scenario


def _build_scenario(api, results):
    return None


if __name__ == "__main__":
    run_module_standalone("Request body boundary verification", _build_scenario, run)
