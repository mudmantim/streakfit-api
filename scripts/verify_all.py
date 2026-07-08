#!/usr/bin/env python3
"""StreakFit — Full Verification Suite.

Runs every subsystem module in scripts/verification/ against one shared
scenario, in dependency order, and prints a combined pass/fail summary.
This is the automated half of the StreakFit Verification Standard (see
CLAUDE.md) -- every feature merged into main needs a module in
scripts/verification/ included here, not a one-off script.

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
from verification import auth, teams, mission, campfire, moments, chat, rickie, security

# Order matters: security.py mutates membership state (removes a member,
# leaves a member) and must run last -- nothing after it can assume
# scenario.users["b"]/["outsider"] are still on the team.
MODULES = [
    ("Auth", auth),
    ("Teams", teams),
    ("Mission", mission),
    ("Campfire", campfire),
    ("Moments", moments),
    ("Chat", chat),
    ("Rickie", rickie),
    ("Security", security),
]


def main():
    parser = argparse.ArgumentParser(description="StreakFit full verification suite")
    parser.add_argument("base_url_positional", nargs="?", default=None, metavar="BASE_URL")
    parser.add_argument("--base-url", default=None)
    args = parser.parse_args()

    api = ApiClient(
        resolve_base_url(args.base_url_positional, args.base_url, os.environ.get("SMOKE_BASE_URL"))
    )
    results = Results()
    run_tag = new_run_tag()

    print("StreakFit — Full Verification Suite")
    print(f"Target:  {api.base_url}")
    print(f"Run tag: {run_tag}")

    scenario = Scenario(api, run_tag)
    scenario.users = register_and_login_users(api, results, run_tag)
    create_and_join_team(api, results, scenario, creator_role="a", joiner_roles=("b",))

    for label, module in MODULES:
        print(f"\n[{label}]")
        module.run(api, results, scenario)

    results.print_table(title="StreakFit Full Verification Suite — Summary")
    sys.exit(0 if results.all_passed() else 1)


if __name__ == "__main__":
    main()
