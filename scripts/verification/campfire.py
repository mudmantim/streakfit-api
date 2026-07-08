#!/usr/bin/env python3
"""Campfire subsystem — cumulative team mission counter and its derived
stage (Kindling / Small Flame / Campfire / Bonfire / Beacon). Never
decrements, never resets -- see Team Campfire Baseline."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verification._client import run_module_standalone
from verification._fixtures import build_team_scenario, complete_daily_mission


def run(api, results, scenario):
    team_id = scenario.team_id
    token = scenario.users["a"]["token"]

    status, campfire = api.request("GET", f"/api/teams/{team_id}/campfire", token=token)
    ok = results.check("campfire.readable", status == 200, f"status={status}")
    if ok:
        results.check(
            "campfire.one_log_recorded",
            campfire.get("total_team_missions") == 1,
            f"total={campfire.get('total_team_missions')}",
        )
        results.check(
            "campfire.stage_is_kindling",
            campfire.get("stage") == "Kindling",
            f"stage={campfire.get('stage')}",
        )

    # Also reachable via the team-detail route (same numbers, different endpoint).
    status, detail = api.request("GET", f"/api/teams/{team_id}", token=token)
    ok = results.check("campfire.also_readable_via_team_detail", status == 200, f"status={status}")
    if ok:
        results.check(
            "campfire.team_detail_matches_campfire_route",
            detail.get("campfire", {}).get("total_team_missions") == campfire.get("total_team_missions"),
        )

    return scenario


def _build_scenario(api, results):
    scenario = build_team_scenario(api, results)
    complete_daily_mission(api, results, scenario.users["a"]["token"])
    return scenario


if __name__ == "__main__":
    run_module_standalone("Campfire subsystem verification", _build_scenario, run)
