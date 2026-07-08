#!/usr/bin/env python3
"""Team Chat subsystem (R2.5) — post/read between real members. No DMs,
no global chat, no edit/delete; a reaction emoji is just a short message,
not a separate mechanism."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verification._client import run_module_standalone
from verification._fixtures import build_team_scenario


def run(api, results, scenario):
    team_id = scenario.team_id
    users = scenario.users

    chat_body = f"smoke test message {scenario.run_tag}"
    status, posted = api.request(
        "POST", f"/api/teams/{team_id}/messages", token=users["a"]["token"], body={"body": chat_body}
    )
    ok = results.check(
        "chat.post_as_a",
        status == 201 and posted.get("body") == chat_body and posted.get("sender_type") == "user",
        f"status={status}",
    )

    if ok:
        status, messages_b = api.request("GET", f"/api/teams/{team_id}/messages", token=users["b"]["token"])
        ok = results.check("chat.read_as_b", status == 200, f"status={status}")
        if ok:
            results.check("chat.b_sees_a_message", any(m["body"] == chat_body for m in messages_b))

    # Empty and over-length bodies are rejected.
    status, _ = api.request("POST", f"/api/teams/{team_id}/messages", token=users["a"]["token"], body={"body": "   "})
    results.check("chat.empty_body_rejected", status == 400, f"status={status}")

    status, _ = api.request(
        "POST", f"/api/teams/{team_id}/messages", token=users["a"]["token"], body={"body": "x" * 241}
    )
    results.check("chat.over_length_body_rejected", status == 400, f"status={status}")

    # A reaction emoji is just a short message -- no separate mechanism.
    status, reaction = api.request(
        "POST", f"/api/teams/{team_id}/messages", token=users["b"]["token"], body={"body": "🔥"}
    )
    results.check(
        "chat.emoji_reaction_posts_as_normal_message",
        status == 201 and reaction.get("body") == "🔥" and reaction.get("sender_type") == "user",
        f"status={status}",
    )

    return scenario


if __name__ == "__main__":
    run_module_standalone("Team Chat subsystem verification", build_team_scenario, run)
