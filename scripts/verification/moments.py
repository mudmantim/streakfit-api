#!/usr/bin/env python3
"""Team Moments subsystem — the durable history record (team_created,
member_joined, campfire_log_added, ...). Never records absence, never
ranks members -- see Team System Baseline Section 10."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verification._client import run_module_standalone
from verification._fixtures import build_team_scenario, complete_daily_mission


def run(api, results, scenario):
    team_id = scenario.team_id
    token = scenario.users["a"]["token"]

    status, moments = api.request("GET", f"/api/teams/{team_id}/moments", token=token)
    ok = results.check("moments.readable", status == 200, f"status={status}")
    if not ok:
        return scenario

    moment_types = [m["moment_type"] for m in moments]
    results.check("moments.team_created_present", "team_created" in moment_types)
    results.check("moments.member_joined_present", "member_joined" in moment_types)
    results.check("moments.campfire_log_added_present", "campfire_log_added" in moment_types)

    # Newest-first ordering.
    timestamps = [m["occurred_at"] for m in moments]
    results.check("moments.ordered_newest_first", timestamps == sorted(timestamps, reverse=True))

    # team_created names the creator, not a team-wide event.
    created_moment = next((m for m in moments if m["moment_type"] == "team_created"), None)
    results.check(
        "moments.team_created_names_creator",
        created_moment is not None and created_moment.get("subject_username") == scenario.users["a"]["username"],
        f"moment={created_moment}",
    )

    return scenario


def _build_scenario(api, results):
    scenario = build_team_scenario(api, results)
    complete_daily_mission(api, results, scenario.users["a"]["token"])
    return scenario


if __name__ == "__main__":
    run_module_standalone("Team Moments subsystem verification", _build_scenario, run)
