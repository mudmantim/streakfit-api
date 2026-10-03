"""The verify_all deletion check must delete an account that really has rows.

auth.delete_account_with_dependent_rows exists to exercise DELETE /api/me on
an account with dependent rows (the PostgreSQL 500 it guards against only
appeared for such accounts). Its leaver used to block an outsider it shared
no team with; after RC-B1 D3 that block writes nothing, and the check kept
passing on a 204 while deleting an account with no rows at all (found by the
independent D3 review). It now blocks a teammate and requires the row to be
listed. These tests prove the check notices when no row was written.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "scripts"))

from verification import auth  # noqa: E402
from verification._client import Results, WsgiClient  # noqa: E402
from verification._fixtures import (Scenario, create_and_join_team, new_run_tag,  # noqa: E402
                                    register_and_login_users)


def _run(flask_app):
    api = WsgiClient(flask_app)
    results = Results()
    scenario = Scenario(api, new_run_tag())
    scenario.users = register_and_login_users(api, results, scenario.run_tag)
    create_and_join_team(api, results, scenario, creator_role="a", joiner_roles=("b",))
    auth.check_account_deletion(api, results, scenario)
    return {name: ok for name, ok, _ in results.rows}


def test_the_leaver_really_has_a_block_before_it_is_deleted(app):
    rows = _run(app)
    assert rows["auth.delete_leaver_joins_the_team"]
    assert rows["auth.delete_leaver_has_a_block"]
    assert rows["auth.delete_account_with_dependent_rows"]


def test_the_check_fails_when_the_block_writes_nothing(app, monkeypatch):
    import app as appmod
    monkeypatch.setattr(appmod, "_share_a_team", lambda *_: False)
    rows = _run(app)
    assert rows["auth.delete_leaver_has_a_block"] is False
