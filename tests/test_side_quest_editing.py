"""Side Quests can be corrected and removed — and only by the person who made them.

Side Quests are the one thing in StreakFit a person types themselves, and they
were write-once: the API had POST, GET and check-in and nothing else. A typo in
a habit you look at daily was permanent, and a habit you had stopped doing sat
in the list forever with "Not started" beside it. That is a standing reminder of
a thing you gave up, in an app whose first design rule is that it never punishes
anybody for showing up.

The boundary tests are the load-bearing ones. These routes take an id from the
URL, so "scoped to the caller" has to be true in the WHERE clause rather than in
a check somebody can forget to write.
"""
import pytest

from conftest import auth_headers, register_and_login


def _make_quest(client, token, title="Read ten pages"):
    r = client.post("/api/challenges", json={"title": title},
                    headers=auth_headers(token))
    assert r.status_code == 201
    return r.get_json()["challenge_id"]


# ── Rename ──────────────────────────────────────────────────────────────────

def test_a_side_quest_can_be_renamed(client):
    token = _t(client, "quest_rename")
    qid = _make_quest(client, token, "Raed ten pages")

    r = client.patch(f"/api/challenges/{qid}", json={"title": "Read ten pages"},
                     headers=auth_headers(token))
    assert r.status_code == 200
    assert r.get_json()["title"] == "Read ten pages"

    listed = client.get("/api/challenges", headers=auth_headers(token)).get_json()
    assert [c["title"] for c in listed] == ["Read ten pages"]


def test_renaming_keeps_the_streak(client):
    """The name is a label. Renaming must not cost somebody their history."""
    token = _t(client, "quest_keep")
    qid = _make_quest(client, token)
    client.post(f"/api/challenges/{qid}/checkin", headers=auth_headers(token))

    before = client.get(f"/api/challenges/{qid}", headers=auth_headers(token)).get_json()
    assert before["current_streak"] == 1

    client.patch(f"/api/challenges/{qid}", json={"title": "Read twenty pages"},
                 headers=auth_headers(token))
    after = client.get(f"/api/challenges/{qid}", headers=auth_headers(token)).get_json()
    assert after["current_streak"] == 1
    assert after["longest_streak"] == 1
    assert after["last_check_in"] == before["last_check_in"]


@pytest.mark.parametrize("bad", ["", "   ", "x" * 101])
def test_rename_refuses_an_unusable_name(client, bad):
    token = _t(client, "quest_bad")
    qid = _make_quest(client, token)
    r = client.patch(f"/api/challenges/{qid}", json={"title": bad},
                     headers=auth_headers(token))
    assert r.status_code == 400
    # Written for a person, like every other message they can see.
    assert "title" not in r.get_json()["error"]


# ── Remove ──────────────────────────────────────────────────────────────────

def test_a_side_quest_can_be_removed(client):
    token = _t(client, "quest_del")
    qid = _make_quest(client, token)

    r = client.delete(f"/api/challenges/{qid}", headers=auth_headers(token))
    assert r.status_code == 200
    assert client.get("/api/challenges", headers=auth_headers(token)).get_json() == []
    assert client.get(f"/api/challenges/{qid}",
                      headers=auth_headers(token)).status_code == 404


def test_removing_a_side_quest_leaves_the_mission_streak_alone(client):
    """The promise the UI makes out loud, held to by a test.

    The Remove button says "Your mission streak, XP and levels are not
    affected." Nothing about the mission lives on this row, but that is a fact
    about today's schema, not a guarantee — so it is asserted.
    """
    token = _t(client, "quest_safe")
    me_before = client.get("/api/me", headers=auth_headers(token)).get_json()
    qid = _make_quest(client, token)
    client.post(f"/api/challenges/{qid}/checkin", headers=auth_headers(token))
    client.delete(f"/api/challenges/{qid}", headers=auth_headers(token))

    me_after = client.get("/api/me", headers=auth_headers(token)).get_json()
    for key in ("current_streak", "best_streak", "xp_total",
                "acorns_total", "level", "total_missions"):
        assert me_after[key] == me_before[key], key


# ── The boundary ────────────────────────────────────────────────────────────

def test_one_person_cannot_rename_anothers_side_quest(client):
    mine = _t(client, "quest_owner")
    theirs = _t(client, "quest_other")
    qid = _make_quest(client, mine, "Mine")

    r = client.patch(f"/api/challenges/{qid}", json={"title": "Theirs now"},
                     headers=auth_headers(theirs))
    assert r.status_code == 404
    assert client.get(f"/api/challenges/{qid}",
                      headers=auth_headers(mine)).get_json()["title"] == "Mine"


def test_one_person_cannot_delete_anothers_side_quest(client):
    mine = _t(client, "quest_owner2")
    theirs = _t(client, "quest_other2")
    qid = _make_quest(client, mine, "Mine")

    r = client.delete(f"/api/challenges/{qid}", headers=auth_headers(theirs))
    assert r.status_code == 404
    assert client.get(f"/api/challenges/{qid}",
                      headers=auth_headers(mine)).status_code == 200


def test_both_routes_require_a_token(client):
    mine = _t(client, "quest_anon")
    qid = _make_quest(client, mine)
    assert client.patch(f"/api/challenges/{qid}", json={"title": "x"}).status_code == 401
    assert client.delete(f"/api/challenges/{qid}").status_code == 401


def _t(client, name):
    return register_and_login(client, name, "TestPass123!")
