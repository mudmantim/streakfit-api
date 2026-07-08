#!/usr/bin/env python3
"""Security subsystem — invite rotation, remove member, leave team, and
unauthorized access. Destructive by nature (removes/leaves real
membership rows), so this module should run LAST against any shared
scenario -- nothing else needs scenario.users["b"] or ["c"] to still be
members afterward. See Operation: No Dead Ends (R2.8) and Team System
Baseline Section 4/11 for why remove-member and rotate-invite are the
creator's only two safety powers, and why nothing else exists (no
moderation, no message deletion)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verification._client import run_module_standalone
from verification._fixtures import build_team_scenario, fetch_user_id


def run(api, results, scenario):
    team_id = scenario.team_id
    users = scenario.users
    old_code = scenario.invite_code

    # ── invite rotation ──────────────────────────────────────────────
    status, rotate_resp = api.request(
        "POST", f"/api/teams/{team_id}/rotate-invite", token=users["a"]["token"]
    )
    ok = results.check(
        "security.rotate_invite_as_creator",
        status == 200 and "invite_code" in rotate_resp,
        f"status={status}",
    )
    new_code = rotate_resp.get("invite_code")

    if ok:
        status, _ = api.request(
            "POST", f"/api/teams/{team_id}/join", token=users["outsider"]["token"], body={"code": old_code}
        )
        results.check("security.old_invite_code_rejected", status == 403, f"status={status}")

        status, _ = api.request(
            "POST", f"/api/teams/{team_id}/join", token=users["outsider"]["token"], body={"code": new_code}
        )
        results.check("security.new_invite_code_accepted", status == 200, f"status={status}")
        # "outsider" is now a real member -- reuse it as the one removed below.

    status, _ = api.request(
        "POST", f"/api/teams/{team_id}/rotate-invite", token=users["b"]["token"]
    )
    results.check("security.non_creator_cannot_rotate", status == 403, f"status={status}")

    # ── remove member: creator removes the just-joined "outsider" ──────
    if fetch_user_id(api, results, users, "outsider", check_name="security.fetch_outsider_user_id"):
        target_id = users["outsider"]["id"]

        status, _ = api.request(
            "DELETE", f"/api/teams/{team_id}/members/{target_id}", token=users["b"]["token"]
        )
        results.check("security.non_creator_cannot_remove", status == 403, f"status={status}")

        status, _ = api.request(
            "DELETE", f"/api/teams/{team_id}/members/{target_id}", token=users["a"]["token"]
        )
        results.check("security.creator_removes_member", status == 200, f"status={status}")

        status, _ = api.request("GET", f"/api/teams/{team_id}/messages", token=users["outsider"]["token"])
        results.check("security.removed_member_loses_chat", status == 403, f"status={status}")

        status, _ = api.request("GET", f"/api/teams/{team_id}/moments", token=users["outsider"]["token"])
        results.check("security.removed_member_loses_moments", status == 403, f"status={status}")

        status, _ = api.request("GET", f"/api/teams/{team_id}", token=users["outsider"]["token"])
        results.check("security.removed_member_loses_team_detail", status == 403, f"status={status}")

    # Creator can't remove themselves -- Leave Team is that action.
    if fetch_user_id(api, results, users, "a", check_name="security.fetch_creator_user_id"):
        status, _ = api.request(
            "DELETE", f"/api/teams/{team_id}/members/{users['a']['id']}", token=users["a"]["token"]
        )
        results.check("security.creator_cannot_remove_self", status == 400, f"status={status}")

    # ── leave team: b leaves voluntarily ────────────────────────────────
    status, _ = api.request("POST", f"/api/teams/{team_id}/leave", token=users["b"]["token"])
    results.check("security.member_leaves", status == 200, f"status={status}")

    status, teams_b = api.request("GET", "/api/teams", token=users["b"]["token"])
    ok = results.check("security.leaver_team_list_readable", status == 200, f"status={status}")
    if ok:
        results.check("security.team_gone_from_leaver_list", all(t["id"] != team_id for t in teams_b))

    # ── unauthorized access: "c" never joined this team ─────────────────
    status, _ = api.request("GET", f"/api/teams/{team_id}/messages", token=users["c"]["token"])
    results.check("security.non_member_chat_read_blocked", status == 403, f"status={status}")

    status, _ = api.request(
        "POST", f"/api/teams/{team_id}/messages", token=users["c"]["token"], body={"body": "nope"}
    )
    results.check("security.non_member_chat_post_blocked", status == 403, f"status={status}")

    status, _ = api.request("GET", f"/api/teams/{team_id}/moments", token=users["c"]["token"])
    results.check("security.non_member_moments_blocked", status == 403, f"status={status}")

    status, _ = api.request("GET", f"/api/teams/{team_id}", token=users["c"]["token"])
    results.check("security.non_member_team_detail_blocked", status == 403, f"status={status}")

    status, _ = api.request("GET", "/api/daily")  # no Authorization header at all
    results.check("security.no_token_blocked", status == 401, f"status={status}")

    return scenario


def _build_scenario(api, results):
    return build_team_scenario(api, results, joiner_roles=("b",))


if __name__ == "__main__":
    run_module_standalone("Security subsystem verification", _build_scenario, run)
