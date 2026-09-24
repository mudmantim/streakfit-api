#!/usr/bin/env python3
"""Auth subsystem — registration, login, /api/me, and the two rejection
paths (duplicate username, wrong password) real users actually hit."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verification._client import run_module_standalone
from verification._fixtures import (SMOKE_PASSWORD, ROLES, Scenario, fetch_user_id,
                                    new_run_tag, register_and_login_users)


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

    check_account_deletion(api, results, scenario)
    return scenario


def check_account_deletion(api, results, scenario):
    """DELETE /api/me works for an account that has touched another table.

    It shipped returning 500 on PostgreSQL for anyone with a row the deletion
    service did not know about (daily_effort first; blocks, reports and team
    challenges too). A brand-new account has nothing, so it proved nothing:
    this one blocks somebody first, which writes a user_block row pointing at
    it, and only then deletes itself. Uses its own throwaway account, so it
    also leaves nothing behind."""
    users = scenario.users
    if not users.get("outsider", {}).get("id"):
        fetch_user_id(api, results, users, "outsider", "auth.delete_fetch_outsider_id")
    outsider_id = users.get("outsider", {}).get("id")
    leaver = f"qa_smoke_leaver_{scenario.run_tag}"
    status, _ = api.request("POST", "/api/register",
                            body={"username": leaver, "password": SMOKE_PASSWORD})
    if not results.check("auth.delete_register_leaver", status == 201, f"status={status}"):
        return
    status, data = api.request("POST", "/api/login",
                               body={"username": leaver, "password": SMOKE_PASSWORD})
    token = (data or {}).get("access_token")
    if not results.check("auth.delete_login_leaver", status == 200 and token, f"status={status}"):
        return
    if outsider_id:
        status, _ = api.request("PUT", f"/api/blocks/{outsider_id}", token=token)
        results.check("auth.delete_leaver_has_a_block", status == 204, f"status={status}")

    status, _ = api.request("DELETE", "/api/me", token=token, body={"password": "wrong"})
    results.check("auth.delete_needs_the_password", status == 403, f"status={status}")

    status, body = api.request("DELETE", "/api/me", token=token,
                               body={"password": SMOKE_PASSWORD})
    results.check("auth.delete_account_with_dependent_rows", status == 200,
                  f"status={status} body={str(body)[:160]}")

    status, _ = api.request("POST", "/api/login",
                            body={"username": leaver, "password": SMOKE_PASSWORD})
    results.check("auth.deleted_account_cannot_log_in", status == 401, f"status={status}")


def _build_scenario(api, results):
    run_tag = new_run_tag()
    scenario = Scenario(api, run_tag)
    scenario.users = register_and_login_users(api, results, run_tag)
    return scenario


if __name__ == "__main__":
    run_module_standalone("Auth subsystem verification", _build_scenario, run)
