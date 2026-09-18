#!/usr/bin/env python3
"""Teams subsystem — create, join, and the member roster the team panel
reads (Operation: No Dead Ends, R2.8)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verification._client import run_module_standalone
from verification._fixtures import build_team_scenario


def run(api, results, scenario):
    users = scenario.users
    team_id = scenario.team_id

    status, detail = api.request("GET", f"/api/teams/{team_id}", token=users["a"]["token"])
    ok = results.check(
        "teams.detail_readable_by_creator",
        status == 200 and detail.get("member_count") == 2,
        f"status={status} member_count={detail.get('member_count')}",
    )
    if ok:
        roster_usernames = {m["username"] for m in detail["members"]}
        results.check(
            "teams.roster_lists_both_by_name",
            {users["a"]["username"], users["b"]["username"]} <= roster_usernames,
            f"roster={roster_usernames}",
        )
        results.check(
            "teams.creator_flag_correct",
            any(m["username"] == users["a"]["username"] and m["is_creator"] for m in detail["members"]),
        )
        results.check(
            "teams.non_creator_flag_correct",
            any(m["username"] == users["b"]["username"] and not m["is_creator"] for m in detail["members"]),
        )

        # Witness fields: the roster's whole purpose is showing whether the
        # people you share a campfire with moved today. Shipped without them
        # for the life of the team feature.
        member = next((m for m in detail["members"] if m["username"] == users["a"]["username"]), {})
        results.check(
            "teams.roster_carries_today_status",
            "completed_today" in member and "completed_today_count" in member,
            f"member keys={sorted(member)}",
        )
        results.check(
            "teams.roster_carries_streak",
            isinstance(member.get("current_streak"), int),
            f"current_streak={member.get('current_streak')!r}",
        )
        # No leaderboard: the roster must not arrive pre-sorted by who is ahead.
        results.check(
            "teams.roster_not_ranked_by_streak",
            [m["is_creator"] for m in detail["members"]][0] is True,
            "creator should lead the roster, not the highest streak",
        )

        campfire = detail.get("campfire", {})
        results.check(
            "teams.campfire_reports_next_stage",
            campfire.get("next_stage") == "Small Flame" and campfire.get("next_stage_at") == 100,
            f"campfire={campfire}",
        )

    # Listed in A's teams list (not just the detail route).
    status, teams_list = api.request("GET", "/api/teams", token=users["a"]["token"])
    ok = results.check("teams.list_readable", status == 200, f"status={status}")
    if ok:
        results.check("teams.appears_in_creator_list", any(t["id"] == team_id for t in teams_list))
        row = next((t for t in teams_list if t["id"] == team_id), {})
        results.check(
            "teams.list_carries_moved_today",
            isinstance(row.get("moved_today"), int),
            f"moved_today={row.get('moved_today')!r}",
        )

    # Joining with a garbage code fails cleanly.
    status, _ = api.request(
        "POST", f"/api/teams/{team_id}/join", token=users["c"]["token"], body={"code": "WRONG1"}
    )
    results.check("teams.bad_code_rejected", status == 403, f"status={status}")

    # Already-joined member can't join twice.
    status, _ = api.request(
        "POST", f"/api/teams/{team_id}/join",
        token=users["b"]["token"], body={"code": scenario.invite_code},
    )
    results.check("teams.duplicate_join_rejected", status == 400, f"status={status}")

    return scenario


if __name__ == "__main__":
    run_module_standalone("Teams subsystem verification", build_team_scenario, run)
