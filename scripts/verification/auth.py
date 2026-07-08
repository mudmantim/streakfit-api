#!/usr/bin/env python3
"""Auth subsystem — registration, login, /api/me, and the two rejection
paths (duplicate username, wrong password) real users actually hit."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verification._client import run_module_standalone
from verification._fixtures import SMOKE_PASSWORD, ROLES, Scenario, new_run_tag, register_and_login_users


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

    return scenario


def _build_scenario(api, results):
    run_tag = new_run_tag()
    scenario = Scenario(api, run_tag)
    scenario.users = register_and_login_users(api, results, run_tag)
    return scenario


if __name__ == "__main__":
    run_module_standalone("Auth subsystem verification", _build_scenario, run)
