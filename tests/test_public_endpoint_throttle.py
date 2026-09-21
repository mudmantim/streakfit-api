"""The unauthenticated endpoints are rate limited, and stay that way.

Found by the post-merge readiness audit: `/api/verification/self` lost its
`@limiter.limit("60 per minute")` in the integration merge. The decorator sat
one line above the conflict boundary, so resolving the conflict in favour of
the other branch's function body silently took its (absent) throttle too --
git had no reason to mention it, and no test covered it.

These three endpoints answer without a credential and do real work: a database
round trip, a manifest parse, and a stat per declared icon. That is the shape
worth limiting, and the shape worth a regression test, because the next merge
can drop a decorator exactly as quietly as this one did.
"""
import pytest


@pytest.fixture(autouse=True)
def _limiter_on():
    """conftest disables the limiter suite-wide; a throttle test needs it on."""
    import app as appmod
    appmod.limiter.reset()
    appmod.limiter.enabled = True
    yield
    appmod.limiter.enabled = False
    appmod.limiter.reset()


PUBLIC = ("/api/verification/self", "/api/build-identity", "/api/health")


@pytest.mark.parametrize("path", PUBLIC)
def test_public_endpoint_is_throttled(client, path):
    """61 calls in a minute must not all be served."""
    codes = [client.get(path).status_code for _ in range(61)]

    assert codes[0] == 200, f"{path} did not answer at all: {codes[0]}"
    assert 429 in codes, (
        f"{path} served all 61 requests without throttling — it is "
        f"unauthenticated and does real work per call"
    )
    # And the limit is a ceiling, not a trip-wire: ordinary use still works.
    assert codes.count(200) >= 30, f"{path} throttled far too early: {codes[:10]}"


@pytest.mark.parametrize("path", PUBLIC)
def test_the_throttle_is_declared_on_the_route_itself(path):
    """Belt and braces, and it names the endpoint when it fails.

    The behavioural test above proves a limit exists; this one proves it is
    declared where a reader looks for it, so a limit accidentally inherited
    from a global default would not satisfy it.
    """
    import inspect
    import app as appmod

    src = inspect.getsource(appmod)
    marker = f"@app.route('{path}'"
    assert marker in src, f"{path} is not a declared route any more"
    after = src.split(marker, 1)[1].split("\ndef ", 1)[0]
    assert "limiter.limit(" in after, (
        f"{path} has no @limiter.limit between its route decorator and its "
        f"handler — see this file's docstring for how that happens"
    )
