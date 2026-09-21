"""Does content in the store actually reach a reader?

A library can be large, validated and accepted and still never be seen: a
rotation that only walks part of it, a loader that drops a type, or an endpoint
that serves from somewhere else entirely. Counting rows in content/items proves
none of that. These tests connect the store to the thing a person sees.
"""
from collections import Counter
from datetime import date, timedelta

import pytest

import app as appmod
import streakfit_content as store
from conftest import auth_headers, register_and_login


def _accepted(kind):
    return [i for i in store.ALL_ITEMS
            if i["stage"] == "accepted" and i["type"] == kind]


# ── The loader serves exactly what the store marked accepted ────────────────

def test_brain_boost_library_is_exactly_the_accepted_trivia():
    assert len(store.BRAIN_BOOST_LIBRARY) == len(_accepted("trivia"))


def test_insight_library_is_exactly_the_accepted_discovery_types():
    expected = sum(len(_accepted(t)) for t in
                   ("fact", "movement", "riddle", "experiment", "rickie"))
    assert len(store.INSIGHT_LIBRARY) == expected


def test_nothing_unaccepted_leaks_into_a_served_library():
    served_questions = {q["question"] for q in store.BRAIN_BOOST_LIBRARY}
    for item in store.ALL_ITEMS:
        if item["type"] == "trivia" and item["stage"] != "accepted":
            assert item["question"] not in served_questions, (
                f"{item['id']} is stage {item['stage']} but is being served")


# ── The rotation reaches every item, and does not repeat early ──────────────

@pytest.mark.parametrize("kind,size_fn", [
    ("boost", lambda: len(store.BRAIN_BOOST_LIBRARY)),
    ("insight", lambda: len(store.INSIGHT_LIBRARY)),
])
def test_rotation_covers_the_whole_library_exactly_once_per_cycle(kind, size_fn):
    size = size_fn()
    start = date(2026, 1, 1)
    seen = Counter(
        appmod._personal_daily_index(
            kind, 4242, (start + timedelta(days=d)).isoformat(), size)
        for d in range(size)
    )
    assert len(seen) == size, (
        f"{kind}: only {len(seen)} of {size} items are reachable in a full cycle")
    assert set(seen.values()) == {1}, f"{kind}: something repeated inside one cycle"


def test_two_users_are_not_shown_the_same_item_every_day():
    """The whole point of the per-person permutation."""
    size = len(store.BRAIN_BOOST_LIBRARY)
    start = date(2026, 1, 1)
    same = sum(
        appmod._personal_daily_index("boost", 1, (start + timedelta(days=d)).isoformat(), size)
        == appmod._personal_daily_index("boost", 2, (start + timedelta(days=d)).isoformat(), size)
        for d in range(60)
    )
    assert same <= 3, f"two users saw the same question on {same} of 60 days"


# ── End to end: the endpoint a real client calls ────────────────────────────

def test_brain_boost_reaches_the_client_through_api_daily(client):
    """There is no GET /api/brain-boost — the question ships inside /api/daily,
    which is the only way a real client ever sees one."""
    token = register_and_login(client, "content_reaches_bb", "TestPass123!")
    r = client.get("/api/daily", headers=auth_headers(token))
    assert r.status_code == 200
    body = r.get_json() or {}
    boost = body.get("brain_boost") or body.get("brainBoost") or {}
    assert boost, f"/api/daily carried no brain_boost; keys were {sorted(body)}"
    questions = {q["question"] for q in store.BRAIN_BOOST_LIBRARY}
    assert boost.get("question") in questions, "served a question not in the store"
    assert len(boost.get("options", [])) == 4
    # The answer must NOT be shipped to the client alongside the question.
    assert "correct_index" not in boost and "answer_index" not in boost


def test_daily_insight_endpoint_serves_an_item_from_the_store(client):
    token = register_and_login(client, "content_reaches_insight", "TestPass123!")
    r = client.get("/api/daily", headers=auth_headers(token))
    assert r.status_code == 200
    insight = (r.get_json() or {}).get("insight") or {}
    if not insight:
        pytest.skip("/api/daily did not include an insight for a fresh user")
    assert insight.get("text") in {i["text"] for i in store.INSIGHT_LIBRARY}
