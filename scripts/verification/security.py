#!/usr/bin/env python3
"""Security subsystem — invite rotation, remove member, leave team, and
unauthorized access. Destructive by nature (removes/leaves real
membership rows), so this module should run LAST against any shared
scenario -- nothing else needs scenario.users["b"] or ["c"] to still be
members afterward. See Operation: No Dead Ends (R2.8) and Team System
Baseline Section 4/11 for why remove-member and rotate-invite are the
creator's only two safety powers.

Moderation proper (blocking, reporting, operator review) now exists and is
covered by verification/moderation.py -- which runs BEFORE this module,
because this one ends with role "b" no longer in the team."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verification._client import run_module_standalone
from verification._fixtures import build_team_scenario, fetch_user_id


def check_team_ownership(api, results, scenario, team_id, code):
    """Owner decision 2026-11-13 and owner decision #1 (2026-11-14/15), checked
    WITHOUT ever leaving a team ownerless. Runs after "b" has left, so the
    smoke team holds only "a"; "c" joins. A sole owner's leave needs an
    explicit confirmation; with "c" in the team "a" must choose; handing the
    team to "c" (age not confirmed) is refused, by both routes; "c" then
    leaves as a plain member.

    Ends with the smoke team still OWNED by "a" (round-6 review R6-1). This
    suite runs against production after every deploy, and an ownerless team
    there closes the M1 rollback: the migration's downgrade refuses while any
    team is ownerless (docs/operations/deploy-ownerless-teams-m1.md). Leaving
    with "nobody", or "a" leaving the team its own Moderation reports keep,
    would make it ownerless. The ownerless path itself is verified against a
    test database instead: check_ownerless_team below, run by
    tests/test_round7_review_fixes.py, never by verify_all."""
    users = scenario.users
    if not code or not fetch_user_id(api, results, users, "a", check_name="security.fetch_owner_id") \
            or not fetch_user_id(api, results, users, "c", check_name="security.fetch_heir_id"):
        return
    status, body = api.request("POST", f"/api/teams/{team_id}/leave", token=users["a"]["token"])
    results.check("security.sole_owner_leave_needs_confirmation",
                  status == 409 and body.get("error") == "team_will_be_deleted",
                  f"status={status} error={body.get('error')!r}")

    status, _ = api.request("POST", f"/api/teams/{team_id}/join", token=users["c"]["token"],
                            body={"code": code})
    if not results.check("security.heir_joins", status == 200, f"status={status}"):
        return
    status, body = api.request("POST", f"/api/teams/{team_id}/leave", token=users["a"]["token"])
    results.check("security.owner_must_choose_before_leaving",
                  status == 409 and body.get("error") == "owner_must_choose",
                  f"status={status} error={body.get('error')!r}")
    status, _ = api.request("POST", f"/api/teams/{team_id}/owner", token=users["c"]["token"],
                            body={"new_owner_user_id": users["a"]["id"]})
    results.check("security.non_owner_cannot_hand_over", status == 403, f"status={status}")
    # Owner decision #1 (2026-11-14/15): only a verified adult can be handed a
    # team, and nothing records an age yet -- so the handover is refused...
    status, body = api.request("POST", f"/api/teams/{team_id}/owner", token=users["a"]["token"],
                               body={"new_owner_user_id": users["c"]["id"]})
    results.check("security.handover_to_unconfirmed_age_refused",
                  status == 400 and body.get("error") == "new_owner_not_eligible",
                  f"status={status} error={body.get('error')!r}")
    # ...and so is leaving with "c" picked as the next owner. "a" stays.
    status, body = api.request("POST", f"/api/teams/{team_id}/leave", token=users["a"]["token"],
                               body={"new_owner_user_id": users["c"]["id"]})
    results.check("security.leave_handing_to_unconfirmed_age_refused",
                  status == 400 and body.get("error") == "new_owner_not_eligible",
                  f"status={status} error={body.get('error')!r}")
    status, _ = api.request("POST", f"/api/teams/{team_id}/leave", token=users["c"]["token"])
    results.check("security.heir_leaves_plainly", status == 200, f"status={status}")
    status, team = api.request("GET", f"/api/teams/{team_id}", token=users["a"]["token"])
    results.check("security.smoke_team_left_owned",
                  status == 200 and team.get("ownerless") is False
                  and team.get("is_creator") is True,
                  f"status={status} ownerless={team.get('ownerless')!r}")


def check_ownerless_team(api, results, scenario, team_id, code):
    """The ownerless, read-only path (owner decision #1, P3/P4/P4-safety),
    live over the API. NOT part of verify_all: it makes a team ownerless,
    which in production would close the M1 rollback (see
    check_team_ownership). Run against a test database by
    tests/test_round7_review_fixes.py. Needs "a" as the owner and "c" as a
    member of `team_id`, and "outsider" outside it; ends with the team
    deleted by its last member (no report keeps it in that test)."""
    users = scenario.users
    status, _ = api.request("POST", f"/api/teams/{team_id}/leave", token=users["a"]["token"],
                            body={"no_owner": True})
    results.check("security.owner_leaves_with_no_owner", status == 200, f"status={status}")
    status, team = api.request("GET", f"/api/teams/{team_id}", token=users["c"]["token"])
    results.check("security.ownerless_team_hides_its_code",
                  status == 200 and team.get("ownerless") is True
                  and team.get("invite_code") is None, f"status={status}")
    status, body = api.request("POST", f"/api/teams/{team_id}/messages", token=users["c"]["token"],
                               body={"body": "still here?"})
    results.check("security.ownerless_team_is_read_only",
                  status == 403 and body.get("error") == "team_read_only", f"status={status}")
    status, _ = api.request("POST", f"/api/teams/{team_id}/join", token=users["outsider"]["token"],
                            body={"code": code})
    results.check("security.ownerless_team_takes_nobody_new", status in (403, 404),
                  f"status={status}")
    status, _ = api.request("POST", f"/api/teams/{team_id}/leave", token=users["c"]["token"],
                            body={"delete_team": True})
    results.check("security.last_member_leaves_and_deletes", status == 200, f"status={status}")
    status, _ = api.request("GET", f"/api/teams/{team_id}", token=users["c"]["token"])
    results.check("security.deleted_team_unreachable", status in (403, 404), f"status={status}")


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

    check_team_ownership(api, results, scenario, team_id, new_code)

    status, _ = api.request("GET", "/api/daily")  # no Authorization header at all
    results.check("security.no_token_blocked", status == 401, f"status={status}")

    # A token whose signature does not verify (what every stored token becomes
    # after a JWT_SECRET_KEY rotation) must be a 401, the status the client
    # signs out on; it was a 422 until 2026-10. Built from a real token by
    # replacing its signature, so no token-shaped literal lives in the repo.
    head_and_claims = users["a"]["token"].rsplit(".", 1)[0]
    status, body = api.request("GET", "/api/daily", token=head_and_claims + "." + "x" * 43)
    results.check("security.bad_signature_token_is_401",
                  status == 401 and (body or {}).get("error") == "Invalid token", f"status={status}")

    return scenario


def _build_scenario(api, results):
    return build_team_scenario(api, results, joiner_roles=("b",))


if __name__ == "__main__":
    run_module_standalone("Security subsystem verification", _build_scenario, run)
