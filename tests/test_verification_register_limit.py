"""The account-deletion verification check survives the registration limit.

The first production run of the deletion check (release audit 14.7, run tag
1790288371_l70ghx) failed `auth.delete_register_leaver` with 429: by then the
suite had already made five registrations (four accounts and the
duplicate-username check), and `/api/register` allows 5 per minute per client.
The check returned early, so production `DELETE /api/me` was never exercised.
Every earlier pass had the limiter switched off, which is why nobody saw it.

These tests run the real auth module, in-process, with the real limiter on.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "scripts"))

from verification import auth  # noqa: E402
from verification._client import Results, WsgiClient  # noqa: E402
from verification._fixtures import Scenario, new_run_tag, register_and_login_users  # noqa: E402


@pytest.fixture(autouse=True)
def _limiter_on():
    """Same reversal as test_login_throttle: conftest turns the limiter off."""
    import app as appmod
    appmod.limiter.reset()
    appmod.limiter.enabled = True
    yield
    appmod.limiter.enabled = False
    appmod.limiter.reset()


def _run_auth_module(flask_app):
    api = WsgiClient(flask_app)
    results = Results()
    scenario = Scenario(api, new_run_tag())
    scenario.users = register_and_login_users(api, results, scenario.run_tag)
    auth.run(api, results, scenario)
    return {name: (ok, detail) for name, ok, detail in results.rows}


def test_deletion_check_runs_after_the_suite_spent_the_register_window(app, monkeypatch):
    """Five registrations, then the leaver's: the sixth must still get through
    and the deletion itself must actually be exercised."""
    import app as appmod
    waits = []
    # Stands in for waiting out the window: what the real wait achieves.
    monkeypatch.setattr(auth, "_wait_out_register_window",
                        lambda: (waits.append(1), appmod.limiter.reset()))

    rows = _run_auth_module(app)

    assert rows["auth.delete_register_leaver"][0], rows["auth.delete_register_leaver"]
    assert rows["auth.delete_account_with_dependent_rows"][0], \
        rows["auth.delete_account_with_dependent_rows"]
    assert rows["auth.deleted_account_cannot_log_in"][0]
    assert waits == [1], "the check must wait only because it was refused, and once"


def test_a_429_that_does_not_clear_is_still_a_failure(app, monkeypatch):
    """Waiting is a retry, not a pass: if the limit is still in force the
    check fails, says 429, and does not claim the deletion was verified."""
    monkeypatch.setattr(auth, "_wait_out_register_window", lambda: None)

    rows = _run_auth_module(app)

    ok, detail = rows["auth.delete_register_leaver"]
    assert not ok and "429" in detail
    assert "auth.delete_account_with_dependent_rows" not in rows


def test_no_wait_without_a_429(app, monkeypatch):
    """Without a 429 (limiter off here) the check never waits."""
    import app as appmod
    appmod.limiter.enabled = False
    monkeypatch.setattr(auth, "_wait_out_register_window",
                        lambda: pytest.fail("waited without being refused"))

    rows = _run_auth_module(app)

    assert rows["auth.delete_account_with_dependent_rows"][0]
