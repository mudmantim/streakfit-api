"""Milestones a new person can actually reach, and that never go backwards.

The list used to run: first mission (day one), then 100 exercises — twenty days
away at five a day — then 100 Brain Boosts, 1000 XP, 100 acorns, level 10, 500
exercises. So after the first day there was nothing reachable for three weeks,
and the whole Milestones page was locked rows with numbers a new user could not
move. An independent walkthrough read it as a list of things they had failed
to do, which is the opposite of what a record of your own progress is for.

Two properties are worth holding to, and neither is obvious from the code:
that something is reachable early, and that nothing can ever un-unlock.
"""
import app as appmod
from conftest import auth_headers, register_and_login


def _milestones(client, token):
    r = client.get("/api/memory-book", headers=auth_headers(token))
    assert r.status_code == 200, r.get_json()
    return {m["key"]: m for m in r.get_json()["milestones"]}


def test_something_is_reachable_in_the_first_two_weeks():
    """At five moves a day, several milestones land inside a fortnight."""
    per_day = 5
    early = []
    for m in appmod._MILESTONE_DEFINITIONS:
        if m["metric"] == "exercises_completed":
            days = m["target"] / per_day
        elif m["metric"] in ("days_active", "best_streak", "missions_completed"):
            days = m["target"]
        else:
            continue
        if days <= 14:
            early.append((m["key"], days))
    assert len(early) >= 4, (
        f"only {early} land inside two weeks — a new user sees a page of locks"
    )
    # And the very first week is not empty either.
    assert any(d <= 7 for _k, d in early), early


def test_milestones_are_ordered_by_how_soon_they_arrive():
    """They are read top to bottom, so the order is part of the message."""
    order = [m["key"] for m in appmod._MILESTONE_DEFINITIONS]
    assert order[0] == "first_mission"
    # A cheap proxy for "gets harder": every exercise target ascends.
    ex = [m["target"] for m in appmod._MILESTONE_DEFINITIONS
          if m["metric"] == "exercises_completed"]
    assert ex == sorted(ex), ex


def test_streak_milestones_use_the_best_streak_not_the_current_one():
    """A milestone must not be taken away because somebody had a hard week.

    This is the design rule — never punish who showed up — expressed as a
    schema choice. Reading `current_streak` here would make an unlocked
    milestone re-lock itself the day after a gap.
    """
    streaky = [m for m in appmod._MILESTONE_DEFINITIONS
               if "streak" in m["key"]]
    assert streaky, "expected streak milestones"
    for m in streaky:
        assert m["metric"] == "best_streak", m


def test_every_milestone_is_computable(client):
    """No definition may name a metric the endpoint does not produce.

    A typo here does not fail loudly — it raises a KeyError inside the Memory
    Book for every user at once.
    """
    token = register_and_login(client, "milestone_reader", "TestPass123!")
    got = _milestones(client, token)
    assert set(got) == {m["key"] for m in appmod._MILESTONE_DEFINITIONS}
    for key, m in got.items():
        assert isinstance(m["progress"], int), key
        assert isinstance(m["target"], int), key
        assert m["progress"] <= m["target"], key


def test_a_brand_new_account_sees_progress_not_only_locks(client):
    """Day one: the first mission is the only thing anyone has done."""
    token = register_and_login(client, "milestone_fresh", "TestPass123!")
    got = _milestones(client, token)
    assert not got["first_mission"]["unlocked"]
    # The near ones must have small targets, so the page reads as a start
    # rather than as a wall.
    nearest = sorted(m["target"] for m in got.values())
    assert nearest[0] == 1 and nearest[1] <= 5, nearest
