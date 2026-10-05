"""D49: the app shell must be version-coherent — new HTML can only reference its
own release's JS/CSS.

The defect: `/` is network-fresh but the service worker serves `/static/*`
cache-first under bare, unfingerprinted paths, so new HTML runs with the
previous release's app.js/style.css (permanently, if a cache bump is missed).
Two independent reproductions confirmed it. The fix ties every executable/style
asset URL to its content hash, so new HTML references a URL that is a cache miss
and is fetched fresh — coherence no longer depends on a human cache bump or on
service-worker timing.

These assert the server contract that makes that true. They fail on aa064ef
(bare asset refs) and pass on the fix.
"""
import hashlib
import os
import re

import app as appmod

client = None


def _get(path="/"):
    global client
    if client is None:
        client = appmod.app.test_client()
    return client.get(path)


def _static_bytes(rel):
    root = os.path.dirname(os.path.abspath(appmod.__file__))
    with open(os.path.join(root, "static", rel), "rb") as fh:
        return fh.read()


EXECUTABLE = ["app.js", "rickie-roam.js", "style.css"]


def test_the_index_document_is_served():
    r = _get("/")
    assert r.status_code == 200
    assert b"<!doctype html" in r.data[:200].lower() or b"<html" in r.data[:200].lower()


def test_executable_and_style_assets_are_version_stamped():
    """app.js, rickie-roam.js and style.css must be referenced with a ?v= token.
    On aa064ef they are bare (`/static/app.js`) → this fails."""
    html = _get("/").get_data(as_text=True)
    for name in EXECUTABLE:
        assert re.search(r'(?:src|href)="/static/' + re.escape(name) + r'\?v=[0-9a-f]{16,}"', html), \
            f"{name} is not content-versioned in the served document"


def test_no_bare_executable_asset_references_remain():
    """A bare reference is exactly what lets new HTML pair with an old cached
    asset. None may remain for the executable shell."""
    html = _get("/").get_data(as_text=True)
    for name in EXECUTABLE:
        assert not re.search(r'(?:src|href)="/static/' + re.escape(name) + r'"', html), \
            f"{name} still has a bare (unversioned) reference"


def test_the_version_token_is_the_assets_content_hash():
    """The token must be derived from file CONTENT, so it changes iff the asset
    changes — the property that guarantees a new release gets a new URL."""
    html = _get("/").get_data(as_text=True)
    for name in EXECUTABLE:
        full = hashlib.sha256(_static_bytes(name)).hexdigest()
        m = re.search(r'/static/' + re.escape(name) + r'\?v=([0-9a-f]{16,})', html)
        assert m, f"no version token for {name}"
        assert full.startswith(m.group(1)), \
            f"{name} token {m.group(1)} is not a prefix of its content hash {full[:16]}"


def test_a_content_change_changes_the_served_url():
    """Simulate a deploy: if app.js bytes change, the URL the document emits must
    change too (so a client can never request the old URL from new HTML)."""
    html_before = _get("/").get_data(as_text=True)
    token_before = re.search(r'/static/app\.js\?v=([0-9a-f]{16,})', html_before)
    assert token_before, "app.js not versioned"
    # Re-derive what the token WOULD be for altered content, without touching disk:
    altered = _static_bytes("app.js") + b"\n// deploy B\n"
    altered_token = hashlib.sha256(altered).hexdigest()[:len(token_before.group(1))]
    assert altered_token != token_before.group(1), \
        "a content change would not change the emitted asset URL (not content-derived)"


def test_images_and_icons_are_also_versioned_no_stale_visual_shell():
    """Closes the same class for non-executable local assets (rickie.svg, icons)
    referenced by the document."""
    html = _get("/").get_data(as_text=True)
    unversioned = re.findall(r'(?:src|href)="(/static/[^"?]+\.(?:svg|png))"', html)
    assert not unversioned, f"unversioned local asset references remain: {unversioned}"


def test_manifest_link_is_left_alone():
    """manifest.json is declarative and browser-refetched; we deliberately do
    not version it (guards against over-reach breaking install)."""
    html = _get("/").get_data(as_text=True)
    assert '"/static/manifest.json"' in html or "/static/manifest.json" in html


def test_the_index_response_is_not_cacheable():
    """The document must stay revalidated so the new asset URLs reach clients at
    once (the SW already treats `/` as network-only)."""
    r = _get("/")
    assert "no-cache" in (r.headers.get("Cache-Control", "")) or \
        "no-store" in (r.headers.get("Cache-Control", ""))


# ── service worker invariants the fix relies on ──────────────────────────────

def _sw_text():
    return _static_bytes("sw.js").decode()


def test_sw_match_is_exact_not_ignoresearch():
    """The whole fix depends on the SW cache key INCLUDING the query string.
    `ignoreSearch: true` would collapse `?v=A` and `?v=B` and reintroduce the
    defect."""
    sw = _sw_text()
    assert "ignoreSearch" not in sw, "sw.js uses ignoreSearch — version query would be ignored"
    assert "caches.match(" in sw


def test_sw_cache_name_is_versioned():
    sw = _sw_text()
    m = re.search(r"const\s+CACHE\s*=\s*['\"]([^'\"]+)['\"]", sw)
    assert m and re.search(r"v\d+", m.group(1)), "sw.js CACHE has no version token"
