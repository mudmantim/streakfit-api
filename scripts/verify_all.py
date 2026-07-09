#!/usr/bin/env python3
"""StreakFit — Full Verification Suite.

Runs every subsystem module in scripts/verification/ against one shared
scenario, in dependency order. This is the automated half of the
StreakFit Verification Standard (see CLAUDE.md) -- every feature merged
into main needs a module in scripts/verification/ included here, not a
one-off script.

One engine, many entry points: `run_suite(api)` takes any object
exposing the same `.request(method, path, token, body)` shape as
`verification._client.ApiClient` -- so the identical check logic runs
either over real HTTP (this file's CLI, local dev, future CI) or
in-process via `verification._client.WsgiClient` (StreakFit Control's
"Run Verification" button, which cannot afford to open a second real
connection against a single-worker deployment). See _client.py for why.

Standard library only -- no pip install needed. Only ever creates
throwaway qa_smoke_* accounts and one disposable "Smoke Test <run tag>"
team; never touches an existing user, team, or the database directly.
Safe to run against production at any time.

Usage:
    python scripts/verify_all.py
    python scripts/verify_all.py https://streakfit.pro
    python scripts/verify_all.py --base-url https://streakfit.pro
    SMOKE_BASE_URL=http://localhost:5000 python scripts/verify_all.py

Exit code 0 = every check passed. 1 = at least one check failed.
2 = a setup step (e.g. registration) failed and the run was aborted,
since nothing downstream could work without it.

To iterate on just one subsystem while developing, run its module
directly instead, e.g.:
    python scripts/verification/chat.py https://streakfit.pro
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from verification._client import ApiClient, Results, resolve_base_url
from verification._fixtures import Scenario, new_run_tag, register_and_login_users, create_and_join_team
from verification import auth, teams, mission, campfire, moments, chat, rickie, security, admin, VERIFICATION_SUITE_VERSION

# Order matters: security.py mutates membership state (removes a member,
# leaves a member) and must run last among the team-scenario modules --
# nothing after it can assume scenario.users["b"]/["outsider"] are still
# on the team. admin.py runs last overall since it's independent of the
# shared team scenario entirely (just checks route reachability/auth).
MODULES = [
    ("Auth", auth),
    ("Teams", teams),
    ("Mission", mission),
    ("Campfire", campfire),
    ("Moments", moments),
    ("Chat", chat),
    ("Rickie", rickie),
    ("Security", security),
    ("Admin", admin),
]


def run_suite(api, on_module_start=None):
    """Runs every module in MODULES against one fresh shared scenario
    built on `api`. `on_module_start(label)`, if given, fires before each
    module runs -- StreakFit Control uses this to report live progress
    while a run is in flight. Returns a Results instance; never raises
    for check failures (only Results.fatal() on unrecoverable setup)."""
    results = Results()
    run_tag = new_run_tag()

    scenario = Scenario(api, run_tag)
    scenario.users = register_and_login_users(api, results, run_tag)
    create_and_join_team(api, results, scenario, creator_role="a", joiner_roles=("b",))

    for label, module in MODULES:
        if on_module_start:
            on_module_start(label)
        module.run(api, results, scenario)

    return results


def main():
    parser = argparse.ArgumentParser(description="StreakFit full verification suite")
    parser.add_argument("base_url_positional", nargs="?", default=None, metavar="BASE_URL")
    parser.add_argument("--base-url", default=None)
    args = parser.parse_args()

    api = ApiClient(
        resolve_base_url(args.base_url_positional, args.base_url, os.environ.get("SMOKE_BASE_URL"))
    )

    print("StreakFit — Full Verification Suite")
    print(f"Target:         {api.base_url}")
    print(f"Suite version:  {VERIFICATION_SUITE_VERSION}")

    results = run_suite(api, on_module_start=lambda label: print(f"\n[{label}]"))

    results.print_table(title="StreakFit Full Verification Suite — Summary")
    sys.exit(0 if results.all_passed() else 1)


if __name__ == "__main__":
    main()
