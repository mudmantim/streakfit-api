#!/usr/bin/env python3
"""Auth subsystem — registration, login, /api/me, and the two rejection
paths (duplicate username, wrong password) real users actually hit."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verification._client import run_module_standalone
from verification._fixtures import (SMOKE_PASSWORD, ROLES, Scenario, create_and_join_team,
                                    fetch_user_id, new_run_tag, register_and_login_users)


def run(api, results, scenario):
    users = scenario.users

    for role in ROLES:
        status, me = api.request("GET", "/api/me", token=users[role]["token"])
        results.check(
            f"auth.me_matches_{role}",
            status == 200 and me.get("username") == users[role]["username"],
            f"status={status} username={me.get('username')!r}",
        )

    # Duplicate registration is rejected -- reuse role "a"'s username.
    status, _ = api.request(
        "POST", "/api/register",
        body={"username": users["a"]["username"], "password": SMOKE_PASSWORD},
    )
    results.check("auth.duplicate_username_rejected", status == 400, f"status={status}")

    # Wrong password is rejected.
    status, _ = api.request(
        "POST", "/api/login",
        body={"username": users["a"]["username"], "password": "definitely-wrong"},
    )
    results.check("auth.wrong_password_rejected", status == 401, f"status={status}")

    # A malformed body is the caller's mistake, answered as one -- never a 500.
    # (Registration is checked by pytest only: one more /api/register here
    # would spend the 5-per-minute budget the scenario's own sign-ups need.)
    status, _ = api.request(
        "POST", "/api/login",
        body={"username": users["a"]["username"], "password": 12345678},
    )
    results.check("auth.malformed_login_is_400", status == 400, f"status={status}")

    check_account_deletion(api, results, scenario)
    return scenario


def check_account_deletion(api, results, scenario):
    """DELETE /api/me works for an account that has touched another table.

    It shipped returning 500 on PostgreSQL for anyone with a row the deletion
    service did not know about (daily_effort first; blocks, reports and team
    challenges too). A brand-new account has nothing, so it proved nothing:
    this one joins the smoke team and blocks a teammate first, which writes a
    team_membership and a user_block row pointing at it, and only then deletes
    itself. Uses its own throwaway account, so it also leaves nothing behind.

    It blocks a TEAMMATE, and confirms the row through GET /api/blocks rather
    than trusting the 204: since RC-B1 D3 a block on someone you share no
    team with deliberately writes nothing, and this check went on passing
    while deleting an account with no dependent rows at all."""
    users = scenario.users
    if not users.get("b", {}).get("id"):
        fetch_user_id(api, results, users, "b", "auth.delete_fetch_b_id")
    b_id = users.get("b", {}).get("id")
    if not results.check("auth.delete_has_a_team_to_join",
                         bool(scenario.team_id and scenario.invite_code and b_id),
                         f"team={scenario.team_id} b={b_id}"):
        return
    leaver = f"qa_smoke_leaver_{scenario.run_tag}"
    status, _ = api.request("POST", "/api/register",
                            body={"username": leaver, "password": SMOKE_PASSWORD})
    if status == 429:
        # By now the suite has registered four accounts and tried a duplicate,
        # and /api/register allows 5 a minute per client. On production that
        # made this the sixth, and the deletion was never exercised (release
        # audit 14.7). Wait the window out once; a second 429 still fails.
        _wait_out_register_window()
        status, _ = api.request("POST", "/api/register",
                                body={"username": leaver, "password": SMOKE_PASSWORD})
    if not results.check("auth.delete_register_leaver", status == 201, f"status={status}"):
        return
    status, data = api.request("POST", "/api/login",
                               body={"username": leaver, "password": SMOKE_PASSWORD})
    token = (data or {}).get("access_token")
    if not results.check("auth.delete_login_leaver", status == 200 and token, f"status={status}"):
        return
    status, _ = api.request("POST", f"/api/teams/{scenario.team_id}/join", token=token,
                            body={"code": scenario.invite_code})
    if not results.check("auth.delete_leaver_joins_the_team", status == 200, f"status={status}"):
        return
    status, _ = api.request("PUT", f"/api/blocks/{b_id}", token=token)
    _, blocks = api.request("GET", "/api/blocks", token=token)
    listed = [x.get("user_id") for x in (blocks or [])] if isinstance(blocks, list) else []
    results.check("auth.delete_leaver_has_a_block", status == 204 and b_id in listed,
                  f"status={status} listed={listed}")

    token = check_password_change(api, results, leaver, token)
    if not token:
        return

    status, _ = api.request("DELETE", "/api/me", token=token, body={"password": "wrong"})
    results.check("auth.delete_needs_the_password", status == 403, f"status={status}")

    # Malformed, not wrong: 400, and nothing is deleted (the next step proves
    # the account is still there by deleting it).
    status, _ = api.request("DELETE", "/api/me", token=token, body={"password": ["x"]})
    results.check("auth.delete_malformed_is_400", status == 400, f"status={status}")

    status, body = api.request("DELETE", "/api/me", token=token,
                               body={"password": CHANGED_PASSWORD})
    results.check("auth.delete_account_with_dependent_rows", status == 200,
                  f"status={status} body={str(body)[:160]}")

    status, _ = api.request("POST", "/api/login",
                            body={"username": leaver, "password": CHANGED_PASSWORD})
    results.check("auth.deleted_account_cannot_log_in", status == 401, f"status={status}")


CHANGED_PASSWORD = SMOKE_PASSWORD + "-renewed"


def check_password_change(api, results, username, token):
    """PW-1, on the same throwaway account the deletion check then removes.

    A second sign-in stands for another device. A wrong current password is
    refused without signing anybody out; the change returns a new token for
    this device and signs out both earlier ones; the old password stops
    working. Returns the new token (or None if the change failed), which the
    deletion that follows uses with the new password -- so deletion is also
    proven to work after a change."""
    status, data = api.request("POST", "/api/login",
                               body={"username": username, "password": SMOKE_PASSWORD})
    other_device = (data or {}).get("access_token")
    if not results.check("auth.password_second_device_login", status == 200 and other_device,
                         f"status={status}"):
        return None

    status, _ = api.request("POST", "/api/me/password", token=token,
                            body={"current_password": "not-the-password",
                                  "new_password": CHANGED_PASSWORD})
    results.check("auth.password_wrong_current_is_403", status == 403, f"status={status}")

    # The shared policy: a password on the breached/common list is refused,
    # before any hashing, and changes nothing.
    status, data = api.request("POST", "/api/me/password", token=token,
                               body={"current_password": SMOKE_PASSWORD,
                                     "new_password": "passwordpassword"})
    results.check("auth.password_common_is_refused",
                  status == 400 and (data or {}).get("code") == "common",
                  f"status={status} code={(data or {}).get('code')!r}")

    status, data = api.request("POST", "/api/me/password", token=token,
                               body={"current_password": SMOKE_PASSWORD,
                                     "new_password": CHANGED_PASSWORD})
    new_token = (data or {}).get("access_token")
    if not results.check("auth.password_change_succeeds", status == 200 and new_token,
                         f"status={status}"):
        return None

    for label, old in (("this_device", token), ("other_device", other_device)):
        status, data = api.request("GET", "/api/me", token=old)
        results.check(f"auth.password_old_token_signed_out_{label}",
                      status == 401 and (data or {}).get("error") == "session_ended",
                      f"status={status} error={(data or {}).get('error')!r}")

    status, _ = api.request("GET", "/api/me", token=new_token)
    results.check("auth.password_new_token_works", status == 200, f"status={status}")

    status, _ = api.request("POST", "/api/login",
                            body={"username": username, "password": SMOKE_PASSWORD})
    results.check("auth.password_old_password_refused", status == 401, f"status={status}")
    return new_token


REGISTER_WINDOW_S = 61   # /api/register: "5 per minute", plus a second's margin


def _wait_out_register_window():
    print(f"        (registration limit reached; waiting {REGISTER_WINDOW_S}s for the window)")
    time.sleep(REGISTER_WINDOW_S)


def _build_scenario(api, results):
    run_tag = new_run_tag()
    scenario = Scenario(api, run_tag)
    scenario.users = register_and_login_users(api, results, run_tag)
    create_and_join_team(api, results, scenario, creator_role="a", joiner_roles=("b",))
    return scenario


if __name__ == "__main__":
    run_module_standalone("Auth subsystem verification", _build_scenario, run)
