#!/usr/bin/env python3
"""Daily Mission subsystem — fetch today's 5 exercises and complete them.

Deliberately run with the user already a team member (see
build_team_scenario) even in standalone mode: completing a mission while
on a team is the realistic path, and it's what campfire.py, moments.py,
and rickie.py depend on when verify_all.py chains these modules together.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verification._client import run_module_standalone
from verification._fixtures import build_team_scenario, complete_daily_mission


def run(api, results, scenario):
    token = scenario.users["a"]["token"]
    complete_daily_mission(api, results, token)

    status, daily = api.request("GET", "/api/daily", token=token)
    ok = results.check("mission.reflects_completed_state", status == 200, f"status={status}")
    if ok:
        completed = sum(1 for ex in daily.get("exercises", []) if ex.get("completed"))
        results.check("mission.all_five_marked_completed", completed == 5, f"completed={completed}")

    status, me = api.request("GET", "/api/me", token=token)
    ok = results.check("mission.stats_readable", status == 200, f"status={status}")
    if ok:
        results.check("mission.streak_at_least_one", (me.get("current_streak") or 0) >= 1, f"streak={me.get('current_streak')}")
        results.check("mission.total_missions_at_least_one", (me.get("total_missions") or 0) >= 1, f"total={me.get('total_missions')}")

    return scenario


if __name__ == "__main__":
    run_module_standalone("Mission subsystem verification", build_team_scenario, run)
