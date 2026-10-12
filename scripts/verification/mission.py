#!/usr/bin/env python3
"""Daily Mission subsystem — fetch today's 5 exercises and complete them.

Since Step 1 (D50/D59/D68) the day's five are stored and every write names
its mission day; the checks marked "Step 1" verify that contract end to end,
against whatever server this runs on, without controlling its clock.

Deliberately run with the user already a team member (see
build_team_scenario) even in standalone mode: completing a mission while
on a team is the realistic path, and it's what campfire.py, moments.py,
and rickie.py depend on when verify_all.py chains these modules together.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verification._client import run_module_standalone
from verification._fixtures import build_team_scenario, complete_daily_mission


def _keys(daily):
    return [ex.get("key") for ex in (daily or {}).get("exercises", [])]


def _count(api, token):
    status, daily = api.request("GET", "/api/daily", token=token)
    return (daily or {}).get("completed_count") if status == 200 else None


def run(api, results, scenario):
    token = scenario.users["a"]["token"]

    # Step 1: the day's five are stored -- two reads give the same five, the
    # same revision and the same day, and the server says how long it has left.
    status, first = api.request("GET", "/api/daily", token=token)
    status2, second = api.request("GET", "/api/daily", token=token)
    results.check(
        "mission.repeated_reads_give_the_same_five",
        status == 200 and status2 == 200 and len(_keys(first)) == 5
        and _keys(first) == _keys(second)
        and first.get("revision") == second.get("revision")
        and first.get("mission_date") == second.get("mission_date"),
        f"first={_keys(first)} second={_keys(second)}",
    )
    left = (first or {}).get("day_ends_in_s")
    results.check(
        "mission.day_ends_in_s_is_reasonable",
        isinstance(left, int) and 0 < left <= 86400,
        f"day_ends_in_s={left!r}",
    )
    mission_date = (first or {}).get("mission_date")
    day = {"mission_date": mission_date}
    key = (_keys(first) or ["?"])[0]

    # Step 1: a write that does not name its day comes from a page older than
    # the contract; it is refused with a prompt to refresh and writes nothing.
    status, body = api.request("POST", f"/api/daily/{key}/complete", token=token)
    results.check(
        "mission.undated_write_asks_for_refresh",
        status == 400 and (body or {}).get("error") == "mission_date_required"
        and (body or {}).get("refresh_required") is True,
        f"status={status} body={body!r}",
    )
    # Step 1: a write for a day that has ended is refused, never filed today.
    try:
        yesterday = (datetime.date.fromisoformat(mission_date) - datetime.timedelta(days=1)).isoformat()
    except (TypeError, ValueError):
        yesterday = "1970-01-01"
    status, body = api.request("POST", f"/api/daily/{key}/complete", token=token,
                               body={"mission_date": yesterday})
    results.check(
        "mission.stale_day_is_refused",
        status == 409 and (body or {}).get("error") == "day_changed"
        and (body or {}).get("today") == mission_date,
        f"status={status} body={body!r}",
    )
    results.check("mission.refused_writes_count_nothing", _count(api, token) == 0,
                  f"completed_count={_count(api, token)!r}")

    # An effort or skill change from a page showing a day that has ended is
    # refused and reshapes nothing: a page left open overnight must not
    # rebuild today's five, which the person has not seen.
    status, body = api.request("PUT", "/api/daily/effort", token=token,
                               body={"level": "easy", "mission_date": yesterday})
    status2, body2 = api.request("PATCH", "/api/me", token=token,
                                 body={"skill_level": "advanced", "mission_date": yesterday})
    _, after = api.request("GET", "/api/daily", token=token)
    results.check(
        "mission.stale_effort_and_skill_are_refused",
        status == 409 and (body or {}).get("error") == "day_changed"
        and status2 == 409 and (body2 or {}).get("error") == "day_changed"
        and _keys(after) == _keys(first)
        and (after or {}).get("revision") == first.get("revision"),
        f"effort={status}/{(body or {}).get('error')} skill={status2}/{(body2 or {}).get('error')} "
        f"five_unchanged={_keys(after) == _keys(first)}",
    )

    # Step 1: a retried completion (lost response, double tap) is saved once.
    status, once = api.request("POST", f"/api/daily/{key}/complete", token=token, body=day)
    status2, again = api.request("POST", f"/api/daily/{key}/complete", token=token, body=day)
    results.check(
        "mission.retried_completion_is_idempotent",
        status == 200 and status2 == 200 and (again or {}).get("already_recorded") is True
        and (again or {}).get("xp_awarded") == 0
        and (once or {}).get("completed_count") == (again or {}).get("completed_count") == 1,
        f"first={status}/{(once or {}).get('completed_count')} retry={status2} body={again!r}",
    )

    # A completion the server refuses must say so and count for nothing. The
    # app's "Not saved" message is built on exactly this contract: a non-200
    # means nothing was recorded, so the button can honestly be offered again.
    status, body = api.request("POST", "/api/daily/not_a_real_exercise/complete", token=token,
                               body=day)
    results.check(
        "mission.refused_completion_is_reported",
        status == 400 and bool((body or {}).get("error")),
        f"status={status} body={body!r}",
    )
    status, daily = api.request("GET", "/api/daily", token=token)
    results.check(
        "mission.refused_completion_counts_nothing",
        status == 200 and daily.get("completed_count") == 1,
        f"status={status} completed_count={(daily or {}).get('completed_count')!r}",
    )

    finish = {}
    complete_daily_mission(api, results, token, capture=finish)

    # Finishing must pay, and must announce the milestone it just crossed --
    # both were silent in the app for their whole existence.
    final = finish.get("final") or {}
    results.check(
        "mission.completion_pays_for_finishing",
        (final.get("xp_awarded") or 0) >= 40,
        f"xp_awarded={final.get('xp_awarded')!r}",
    )
    results.check(
        "mission.first_mission_milestone_announced",
        any(m.get("key") == "first_mission" for m in (final.get("milestones_unlocked") or [])),
        f"milestones_unlocked={final.get('milestones_unlocked')!r}",
    )

    status, daily = api.request("GET", "/api/daily", token=token)
    ok = results.check("mission.reflects_completed_state", status == 200, f"status={status}")
    if ok:
        completed = sum(1 for ex in daily.get("exercises", []) if ex.get("completed"))
        results.check("mission.all_five_marked_completed", completed == 5, f"completed={completed}")
        # Step 1: the bonus is for the five stored slots, paid once.
        bonus = [e for e in (final.get("progress_events") or []) if e.get("event_type") == "mission_complete"]
        last = _keys(daily)[-1] if _keys(daily) else "?"
        status, replay = api.request("POST", f"/api/daily/{last}/complete", token=token, body=day)
        replay_bonus = [e for e in ((replay or {}).get("progress_events") or [])
                        if e.get("event_type") == "mission_complete"]
        results.check(
            "mission.five_slots_pay_the_bonus_once",
            daily.get("completed_count") == 5 and len(bonus) == 1 and status == 200
            and not replay_bonus and (replay or {}).get("completed_count") == 5,
            f"count={daily.get('completed_count')} bonus_events={len(bonus)} replay={status}/{replay!r}",
        )

    status, me = api.request("GET", "/api/me", token=token)
    ok = results.check("mission.stats_readable", status == 200, f"status={status}")
    if ok:
        results.check("mission.streak_at_least_one", (me.get("current_streak") or 0) >= 1, f"streak={me.get('current_streak')}")
        results.check("mission.total_missions_at_least_one", (me.get("total_missions") or 0) >= 1, f"total={me.get('total_missions')}")

    return scenario


if __name__ == "__main__":
    run_module_standalone("Mission subsystem verification", build_team_scenario, run)
