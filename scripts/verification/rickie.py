#!/usr/bin/env python3
"""Rickie's team reactions (R2.6) — fixed-template messages posted into
team chat for a small, deliberately rare set of triggers: a member
joining, and a team's first-ever campfire log. No AI generation, no
reaction on every ordinary log.

Coach (Rickie's 1:1 chat) is a separate subsystem with its own future
verification module -- see scripts/verification/README.md.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verification._client import run_module_standalone
from verification._fixtures import build_team_scenario, complete_daily_mission

# Must match RICKIE_TEAM_MESSAGES in app.py exactly -- these are picked
# randomly from a small fixed pool per trigger, so checks match against
# the whole pool, not one arbitrary phrase.
WELCOME_TEMPLATES = {"Welcome to the campfire.", "Glad you're here."}
FIRST_LOG_TEMPLATES = {"First log added. The fire is starting."}
STAGE_REACHED_TEMPLATES = {"The campfire grew brighter.", "You built this together."}


def run(api, results, scenario):
    team_id = scenario.team_id
    token = scenario.users["a"]["token"]

    status, messages = api.request("GET", f"/api/teams/{team_id}/messages", token=token)
    ok = results.check("rickie.chat_readable", status == 200, f"status={status}")
    if not ok:
        return scenario

    rickie_messages = [m for m in messages if m.get("sender_type") == "rickie"]
    rickie_bodies = [m["body"] for m in rickie_messages]

    results.check(
        "rickie.welcome_message_on_join",
        any(b in WELCOME_TEMPLATES for b in rickie_bodies),
        f"rickie said: {rickie_bodies}",
    )
    results.check(
        "rickie.first_log_message",
        any(b in FIRST_LOG_TEMPLATES for b in rickie_bodies),
        f"rickie said: {rickie_bodies}",
    )
    results.check(
        "rickie.sender_user_id_is_null",
        all(m.get("sender_username") is None for m in rickie_messages),
        f"messages: {rickie_messages}",
    )

    return scenario


def _build_scenario(api, results):
    scenario = build_team_scenario(api, results)
    complete_daily_mission(api, results, scenario.users["a"]["token"])
    return scenario


if __name__ == "__main__":
    run_module_standalone("Rickie team-reactions subsystem verification", _build_scenario, run)
