#!/usr/bin/env python3
"""Teams subsystem — create, join, and the member roster the team panel
reads (Operation: No Dead Ends, R2.8)."""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verification._client import run_module_standalone
from verification._fixtures import build_team_scenario, fetch_user_id


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
        # The roster is keyed by user_id now, and only security.py resolved
        # ids before — and it runs after this module. Resolved here so teams
        # does not depend on another module having run first.
        for role in ("a", "b"):
            if users[role].get("id") is None:
                fetch_user_id(api, results, users, role,
                              check_name=f"teams.fetch_{role}_user_id")

        # Identify members by user_id, not by login.
        #
        # The roster stopped returning other people's login identifiers — it
        # sends `name`, a chosen display name or a stable "Member N". These
        # smoke accounts are called qa_smoke_* and carry a run tag, which is
        # exactly the machine-looking shape `_safe_display_name` refuses, so
        # matching on the login here would never succeed again.
        roster_ids = {m["user_id"] for m in detail["members"]}
        results.check(
            "teams.roster_lists_both_members",
            {users["a"]["id"], users["b"]["id"]} <= roster_ids,
            f"roster={roster_ids}",
        )
        # The guarantee itself, on a live server: no member-visible response
        # may contain another account's login.
        #
        # The first version of this check was VACUOUS and an adversarial
        # review said so. Smoke accounts are called `qa_smoke_*`, and a `qa_`
        # prefix was already refused by the old name resolver, so "the login
        # is absent" could never fail here no matter how broken the roster
        # was. It passed against an implementation that published ordinary
        # logins verbatim.
        #
        # So it asserts the POSITIVE shape as well: peers get a chosen display
        # name or "Member N", and nothing else. These accounts never set a
        # display name, so every label must be a Member ordinal — which fails
        # immediately if anything falls back to a username, whatever that
        # username happens to look like.
        body = json.dumps(detail)
        results.check(
            "teams.roster_carries_no_login_identifier",
            all(users[r]["username"] not in body for r in ("a", "b")),
            f"detail={body[:200]}",
        )
        names = [m.get("name") for m in detail["members"]]
        results.check(
            "teams.roster_labels_are_never_derived_from_a_login",
            all(re.fullmatch(r"Member \d+", n or "") for n in names),
            f"names={names} (these accounts set no display name, so every "
            f"label must be a Member ordinal)",
        )
        results.check(
            "teams.creator_flag_correct",
            any(m["user_id"] == users["a"]["id"] and m["is_creator"] for m in detail["members"]),
        )
        results.check(
            "teams.non_creator_flag_correct",
            any(m["user_id"] == users["b"]["id"] and not m["is_creator"] for m in detail["members"]),
        )

        # Witness fields: the roster's whole purpose is showing whether the
        # people you share a campfire with moved today. Shipped without them
        # for the life of the team feature.
        member = next((m for m in detail["members"] if m["user_id"] == users["a"]["id"]), {})
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
