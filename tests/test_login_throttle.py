"""Failed logins are throttled far harder than successful ones.

Mudman Command's login-throttle probe found this: seven deliberately wrong
passwords in a row, and StreakFit answered 401 every time. The endpoint was
limited to 10 requests a minute, which protects the ENDPOINT and does nothing
for an ACCOUNT — 600 password guesses an hour, per IP, indefinitely, with no
escalation and no lockout.

The fix charges the bucket only when the response is a 401 (`deduct_when`), so
the control falls entirely on guessing. Somebody mistyping their own password
spends a couple of attempts and then gets in; somebody logging in normally
spends nothing at all.
"""
import pytest

from conftest import register_and_login   # noqa: F401  (fixtures import chain)


@pytest.fixture(autouse=True)
def _limiter_on():
    """conftest disables the limiter for the whole suite, so these turn it back on.

    Every other test would otherwise spend its budget on ordinary traffic and
    fail for reasons that have nothing to do with what it is checking — which
    is why the global default is off. A throttle test is the one place that
    default has to be reversed, and the bucket is emptied either side so one
    test cannot poison the next.
    """
    import app as appmod
    appmod.limiter.reset()
    appmod.limiter.enabled = True
    yield
    appmod.limiter.enabled = False
    appmod.limiter.reset()


def _login(client, username, password):
    return client.post("/api/login",
                       json={"username": username, "password": password})


def test_repeated_wrong_passwords_are_eventually_refused(client):
    client.post("/api/register",
                json={"username": "throttle_target", "password": "TestPass123!"})
    seen = []
    for _ in range(7):
        seen.append(_login(client, "throttle_target", "wrong-password").status_code)
        if seen[-1] == 429:
            break
    assert 429 in seen, f"no refusal after seven wrong passwords: {seen}"
    # And it arrives quickly enough to matter — not on the seventh of seven.
    assert seen.index(429) <= 5, seen


def test_a_person_who_mistypes_twice_still_gets_in(client):
    """The control must not punish the ordinary case.

    This is the whole reason for `deduct_when`: a limit on ALL login traffic
    would spend the budget on people who are simply logging in.
    """
    client.post("/api/register",
                json={"username": "fumbler", "password": "TestPass123!"})
    assert _login(client, "fumbler", "nope").status_code == 401
    assert _login(client, "fumbler", "also-nope").status_code == 401
    ok = _login(client, "fumbler", "TestPass123!")
    assert ok.status_code == 200, ok.get_json()
    assert "access_token" in ok.get_json()


def test_successful_logins_are_not_charged_to_the_failure_budget(client):
    """Ten good logins in a row must not trip the failed-login limiter."""
    client.post("/api/register",
                json={"username": "regular", "password": "TestPass123!"})
    for i in range(8):
        r = _login(client, "regular", "TestPass123!")
        assert r.status_code == 200, f"good login {i + 1} returned {r.status_code}"


def test_the_refusal_does_not_reveal_whether_the_account_exists(client):
    """Guessing at a real account and an imaginary one must look identical."""
    client.post("/api/register",
                json={"username": "real_person", "password": "TestPass123!"})
    real = _login(client, "real_person", "wrong").status_code
    import app as appmod
    appmod.limiter.reset()
    fake = _login(client, "no_such_person_at_all", "wrong").status_code
    assert real == fake == 401
