#!/usr/bin/env python3
"""Moderation subsystem — blocking, reporting, and the operator queue.

Drives the same routes a person and an operator use, against a running app.
What it cannot do is exercise the moderation ACTIONS end to end: those need
the admin secret, and this suite is designed to be safe to run against
production without one. So the operator side is verified here as a boundary
-- every admin route refuses without the secret -- and the actions themselves
are covered by tests/test_moderation.py, which has a test-only secret.

Deliberately additive: it creates a block it then removes, and files reports
in the disposable smoke team. It never touches an existing user or team.
"""
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

    # Ids for A and B; the block routes take a user id.
    fetch_user_id(api, results, users, "a", "moderation.fetch_a_id")
    fetch_user_id(api, results, users, "b", "moderation.fetch_b_id")
    a_tok, b_tok = users["a"]["token"], users["b"]["token"]
    b_id, a_id = users["b"]["id"], users["a"]["id"]
    if not b_id or not a_id:
        results.check("moderation.ids_available", False, "could not resolve user ids")
        return scenario

    # --- blocking ---
    status, _ = api.request("GET", "/api/blocks", token=a_tok)
    results.check("moderation.block_list_readable", status == 200, f"status={status}")

    status, _ = api.request("PUT", f"/api/blocks/{b_id}", token=a_tok)
    results.check("moderation.block_created", status == 204, f"status={status}")

    status, blocks = api.request("GET", "/api/blocks", token=a_tok)
    ids = [b.get("user_id") for b in (blocks or [])]
    results.check("moderation.block_appears_in_list", b_id in ids, f"blocks={ids}")

    # The block list must not carry a login -- same rule as the roster.
    logins = {u["username"] for u in users.values()}
    blob = json.dumps(blocks)
    results.check(
        "moderation.block_list_carries_no_login_identifier",
        not any(login in blob for login in logins),
        f"blocks={blob[:200]}",
    )

    # Blocking yourself is refused; blocking a nonexistent id is indistinguishable
    # from blocking a real one, so the route cannot be used to enumerate accounts.
    status, _ = api.request("PUT", f"/api/blocks/{a_id}", token=a_tok)
    results.check("moderation.self_block_refused", status == 400, f"status={status}")
    status_missing, _ = api.request("PUT", "/api/blocks/99999999", token=a_tok)
    results.check(
        "moderation.block_route_is_not_an_enumeration_oracle",
        status_missing == 204,
        f"missing-id status={status_missing} (must match the 204 a real id gives)",
    )

    # A blocked member's messages stop appearing for the blocker.
    body = f"moderation probe {scenario.run_tag}"
    api.request("POST", f"/api/teams/{team_id}/messages", token=b_tok, body={"body": body})
    status, msgs = api.request("GET", f"/api/teams/{team_id}/messages", token=a_tok)
    bodies = [m.get("body") for m in (msgs or [])]
    results.check(
        "moderation.block_hides_the_thread",
        status == 200 and body not in bodies,
        f"status={status} found={body in bodies}",
    )

    # Unblock puts it back -- reversible, which is what makes it safe to offer.
    status, _ = api.request("DELETE", f"/api/blocks/{b_id}", token=a_tok)
    results.check("moderation.unblock_accepted", status == 204, f"status={status}")
    status, msgs = api.request("GET", f"/api/teams/{team_id}/messages", token=a_tok)
    bodies = [m.get("body") for m in (msgs or [])]
    results.check("moderation.unblock_restores_the_thread", body in bodies,
                  f"found={body in bodies}")

    # --- reporting ---
    status, rep = api.request("POST", "/api/reports", token=a_tok, body={
        "category": "harassment", "subject_type": "user",
        "reported_user_id": b_id, "team_id": team_id,
        "note": f"smoke report {scenario.run_tag}"})
    results.check("moderation.report_user_accepted",
                  status == 201 and bool((rep or {}).get("report_id")),
                  f"status={status}")
    # The receipt tells the reporter nothing about the other person.
    results.check(
        "moderation.report_receipt_carries_no_login_identifier",
        not any(login in json.dumps(rep) for login in logins),
        f"receipt={json.dumps(rep)[:200]}",
    )

    status, err = api.request("POST", "/api/reports", token=a_tok, body={
        "category": "not-a-real-category", "subject_type": "user",
        "reported_user_id": b_id, "team_id": team_id})
    results.check("moderation.unknown_category_refused", status == 400, f"status={status}")
    results.check(
        "moderation.categories_include_child_safety",
        "child_safety" in ((err or {}).get("categories") or []),
        f"categories={(err or {}).get('categories')}",
    )

    # A message can be reported by its own id, which means the serializer has
    # to be handing one out -- before this milestone it sent none at all.
    status, msgs = api.request("GET", f"/api/teams/{team_id}/messages", token=a_tok)
    reportable = [m for m in (msgs or []) if m.get("message_id")]
    results.check("moderation.messages_carry_a_reportable_id",
                  bool(reportable), f"of {len(msgs or [])} messages")
    if reportable:
        status, _ = api.request("POST", "/api/reports", token=a_tok, body={
            "category": "inappropriate_content", "subject_type": "message",
            "subject_ref": reportable[-1]["message_id"], "team_id": team_id})
        results.check("moderation.report_message_accepted", status == 201, f"status={status}")

    # Reporting must not become a way to reach a team you are not in.
    status, _ = api.request("POST", "/api/reports", token=users["c"]["token"], body={
        "category": "harassment", "subject_type": "user",
        "reported_user_id": b_id, "team_id": team_id})
    results.check("moderation.outsider_cannot_report_into_a_team",
                  status == 403, f"status={status}")

    # --- operator boundary ---
    for name, path in (
        ("queue", "/api/admin/reports"),
        ("detail", "/api/admin/reports/" + "0" * 32),
    ):
        status, _ = api.request("GET", path)
        results.check(f"moderation.admin_{name}_blocked_without_secret",
                      status == 403, f"status={status}")
        status, _ = api.request("GET", path, token=a_tok)
        results.check(f"moderation.admin_{name}_blocked_for_ordinary_user",
                      status == 403, f"status={status}")

    status, _ = api.request("POST", "/api/admin/reports/" + "0" * 32 + "/action",
                            token=a_tok, body={"action": "dismiss"})
    results.check("moderation.admin_action_blocked_for_ordinary_user",
                  status == 403, f"status={status}")

    # The operations milestone added four more operator routes. Evidence and
    # appeal disposition are the two most sensitive surfaces in the product --
    # one decrypts a preserved photo, the other overturns a decision -- so the
    # boundary is checked on each of them by name rather than assumed to be
    # inherited from the routes above.
    ops_routes = (
        ("photo_evidence", "GET", "/api/admin/reports/" + "0" * 32 + "/photo-evidence"),
        ("legal_hold", "POST", "/api/admin/reports/" + "0" * 32 + "/legal-hold"),
        ("appeal_queue", "GET", "/api/admin/appeals"),
        ("appeal_decide", "POST", "/api/admin/appeals/" + "0" * 32 + "/decide"),
    )
    for name, method, path in ops_routes:
        status, _ = api.request(method, path, body={} if method == "POST" else None)
        results.check(f"moderation.admin_{name}_blocked_without_secret",
                      status == 403, f"status={status}")
        status, _ = api.request(method, path, token=a_tok,
                                body={} if method == "POST" else None)
        results.check(f"moderation.admin_{name}_blocked_for_ordinary_user",
                      status == 403, f"status={status}")

    # --- decisions and appeals, from the ordinary side -----------------------
    #
    # These smoke accounts have no moderation history, which is the point: the
    # routes have to answer an empty, honest 200 for the overwhelming majority
    # of people rather than 404 or leak somebody else's decisions into the list.
    status, decisions = api.request("GET", "/api/moderation/decisions", token=a_tok)
    results.check("moderation.decisions_readable",
                  status == 200 and isinstance(decisions, list), f"status={status}")
    results.check("moderation.clean_account_has_no_decisions",
                  decisions == [], f"decisions={json.dumps(decisions)[:200]}")

    status, appeals = api.request("GET", "/api/appeals", token=a_tok)
    results.check("moderation.appeals_readable",
                  status == 200 and isinstance(appeals, list), f"status={status}")
    results.check("moderation.clean_account_has_no_appeals",
                  appeals == [], f"appeals={json.dumps(appeals)[:200]}")

    status, _ = api.request("GET", "/api/moderation/decisions")
    results.check("moderation.decisions_require_a_login", status == 401, f"status={status}")
    status, _ = api.request("GET", "/api/appeals")
    results.check("moderation.appeals_require_a_login", status == 401, f"status={status}")

    # A decision id you do not own is answered exactly like one that does not
    # exist. A 403 here would confirm that a given id is a real decision
    # against a real person -- the enumeration oracle the block route avoids.
    status, _ = api.request("POST", "/api/appeals", token=a_tok,
                            body={"decision_id": 1})
    results.check("moderation.appeal_on_a_foreign_decision_is_not_an_oracle",
                  status == 404, f"status={status} (must be 404, never 403)")
    status, _ = api.request("POST", "/api/appeals", token=a_tok,
                            body={"decision_id": 987654321})
    results.check("moderation.appeal_on_a_missing_decision_is_404",
                  status == 404, f"status={status}")
    status, _ = api.request("POST", "/api/appeals", token=a_tok, body={})
    results.check("moderation.appeal_without_a_decision_refused",
                  status == 400, f"status={status}")

    # --- the appeals UI is reachable ----------------------------------------
    #
    # This exists because of R2: a whole team layer shipped working and
    # unreachable. The appeal form was built the same way -- a settings row
    # that nothing rendered and a handler nothing called. The markup has to be
    # served, and it has to start hidden, because a permanent "appeals" entry
    # in a movement app reads as an accusation.
    status, page = api.request("GET", "/", text=True)
    html = page if isinstance(page, str) else ""
    results.check("moderation.appeals_row_is_served",
                  'id="settings-row-moderation"' in html,
                  "settings-row-moderation not found in the served page")
    row = re.search(r'<div[^>]*id="settings-row-moderation"[^>]*>', html)
    results.check("moderation.appeals_row_starts_hidden",
                  bool(row) and "hidden" in row.group(0),
                  f"row={row.group(0) if row else 'absent'}")
    results.check("moderation.appeals_row_has_a_handler",
                  "openModerationDecisions()" in html,
                  "the row renders but nothing opens the panel")
    # The panel has to exist in the markup before the first click: the button
    # advertises it with aria-controls, and a control pointing at an id that
    # is not in the document is a broken promise to a screen reader.
    results.check("moderation.appeals_panel_container_is_served",
                  'id="moderation-panel"' in html,
                  "aria-controls points at an element that does not exist")

    return scenario


if __name__ == "__main__":
    run_module_standalone(
        run, build_team_scenario,
        "Moderation — blocking, reporting, and the operator boundary")
