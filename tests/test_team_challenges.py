"""Challenges: one person nudging another to actually move.

The rules that matter here are mostly about what a challenge is NOT. It is not
free text, because a typed dare in a family app used by children is a safety
hole. It has no loser, no failure state, and nothing anywhere reports who did
not do it.
"""
import datetime

import pytest

from app import TeamChallenge, TeamChallengeCompletion, User, db
from conftest import auth_headers, register_and_login


@pytest.fixture()
def team_of_two(client):
    parent = register_and_login(client, "ch_parent")
    team = client.post("/api/teams", json={"name": "Challenge Family"},
                       headers=auth_headers(parent)).get_json()["team"]
    kid = register_and_login(client, "ch_kid")
    client.post(f"/api/teams/{team['id']}/join", json={"code": team["invite_code"]},
                headers=auth_headers(kid))
    outsider = register_and_login(client, "ch_outsider")
    return {"parent": parent, "kid": kid, "outsider": outsider, "team_id": team["id"]}


def _uid(username):
    return db.session.execute(db.select(User).where(User.username == username)).scalar_one().id


def start(client, token, team_id, preset="squats_20", target=None):
    body = {"preset_key": preset}
    if target is not None:
        body["target_user_id"] = target
    return client.post(f"/api/teams/{team_id}/challenges", json=body,
                       headers=auth_headers(token))


# ── Only presets, ever ─────────────────────────────────────────────────────

def test_a_challenge_must_come_from_the_preset_list(client, team_of_two):
    """No free text. This is the safety property, not a convenience."""
    resp = start(client, team_of_two["parent"], team_of_two["team_id"],
                 preset="do 500 burpees or you're out")

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "unknown_challenge"


def test_the_preset_list_is_readable(client, team_of_two):
    presets = client.get("/api/challenge-presets",
                         headers=auth_headers(team_of_two["kid"])).get_json()

    assert len(presets) >= 6
    for p in presets:
        assert p["key"] and p["title"] and p["emoji"]


def test_every_preset_can_actually_be_started(client, team_of_two):
    """A preset with a typo'd key would be offered and then rejected."""
    presets = client.get("/api/challenge-presets",
                         headers=auth_headers(team_of_two["parent"])).get_json()
    for p in presets:
        resp = start(client, team_of_two["parent"], team_of_two["team_id"], preset=p["key"])
        assert resp.status_code == 201, f"{p['key']} could not be started"


def test_presets_never_prescribe_something_unsafe_or_shaming():
    import app as appmod

    banned = ["until you", "as many as you can", "don't stop", "no excuses",
              "beat", "loser", "fastest", "weigh", "run a mile"]
    for p in appmod.CHALLENGE_PRESETS:
        text = (p["title"] + " " + p["blurb"]).lower()
        for phrase in banned:
            assert phrase not in text, f"{p['key']}: {phrase!r}"


# ── Who can do what ────────────────────────────────────────────────────────

def test_a_non_member_cannot_start_a_challenge(client, team_of_two):
    assert start(client, team_of_two["outsider"], team_of_two["team_id"]).status_code == 403


def test_a_challenge_cannot_be_aimed_at_someone_outside_the_team(client, team_of_two):
    resp = start(client, team_of_two["parent"], team_of_two["team_id"],
                 target=_uid("ch_outsider"))

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "not_a_team_member"


def test_a_non_member_cannot_complete_a_challenge(client, team_of_two):
    ch = start(client, team_of_two["parent"], team_of_two["team_id"]).get_json()["challenge"]

    resp = client.post(
        f"/api/teams/{team_of_two['team_id']}/challenges/{ch['public_id']}/complete",
        headers=auth_headers(team_of_two["outsider"]))

    assert resp.status_code == 403


def test_anyone_on_the_team_may_do_a_challenge_aimed_at_someone_else(client, team_of_two):
    """A challenge is an invitation, not an assignment."""
    ch = start(client, team_of_two["parent"], team_of_two["team_id"],
               target=_uid("ch_kid")).get_json()["challenge"]

    resp = client.post(
        f"/api/teams/{team_of_two['team_id']}/challenges/{ch['public_id']}/complete",
        headers=auth_headers(team_of_two["parent"]))

    assert resp.status_code == 200


# ── Completing it pays, through the same economy ───────────────────────────

def test_completing_a_challenge_awards_xp_and_acorns(client, team_of_two):
    ch = start(client, team_of_two["parent"], team_of_two["team_id"]).get_json()["challenge"]

    done = client.post(
        f"/api/teams/{team_of_two['team_id']}/challenges/{ch['public_id']}/complete",
        headers=auth_headers(team_of_two["kid"])).get_json()

    assert done["completed"] is True
    assert done["xp_awarded"] == 15
    assert done["acorns_awarded"] == 2


def test_a_challenge_pays_less_than_finishing_the_daily_mission(client):
    """Movement with other people amplifies the core loop; it must not replace
    it. Finishing your own mission stays the biggest beat of the day."""
    import app as appmod

    finishing = appmod.MISSION_COMPLETE_XP + appmod.PERFECT_MISSION_XP
    assert appmod.CHALLENGE_COMPLETE_XP < finishing


def test_completing_the_same_challenge_twice_pays_once(client, team_of_two):
    ch = start(client, team_of_two["parent"], team_of_two["team_id"]).get_json()["challenge"]
    path = f"/api/teams/{team_of_two['team_id']}/challenges/{ch['public_id']}/complete"
    client.post(path, headers=auth_headers(team_of_two["kid"]))

    again = client.post(path, headers=auth_headers(team_of_two["kid"])).get_json()

    assert again["xp_awarded"] == 0
    assert again.get("already_completed") is True


def test_challenge_farming_stops_paying_but_never_stops_working(client, team_of_two):
    """Past the daily cap the challenge still completes and still celebrates.
    Refusing it would be telling someone they had moved too much."""
    import app as appmod

    results = []
    for _ in range(appmod.CHALLENGE_REWARDED_PER_DAY + 2):
        ch = start(client, team_of_two["parent"], team_of_two["team_id"],
                   preset="walk_10").get_json()["challenge"]
        results.append(client.post(
            f"/api/teams/{team_of_two['team_id']}/challenges/{ch['public_id']}/complete",
            headers=auth_headers(team_of_two["kid"])).get_json())

    assert all(r["completed"] for r in results), "a completion was refused"
    paid = [r for r in results if r["xp_awarded"] > 0]
    assert len(paid) == appmod.CHALLENGE_REWARDED_PER_DAY


def test_completing_suggests_the_victory_photo(client, team_of_two):
    """The natural next beat, and the bridge into the thing Olivia asked for."""
    ch = start(client, team_of_two["parent"], team_of_two["team_id"]).get_json()["challenge"]

    done = client.post(
        f"/api/teams/{team_of_two['team_id']}/challenges/{ch['public_id']}/complete",
        headers=auth_headers(team_of_two["kid"])).get_json()

    assert done["suggest_photo"] is True
    assert done["suggested_filter"] == "team_challenge"


# ── It lives in the thread, and in the team's history ──────────────────────

def test_a_challenge_appears_as_a_card_in_the_thread(client, team_of_two):
    start(client, team_of_two["parent"], team_of_two["team_id"], target=_uid("ch_kid"))

    thread = client.get(f"/api/teams/{team_of_two['team_id']}/messages",
                        headers=auth_headers(team_of_two["kid"])).get_json()

    cards = [m for m in thread if m.get("challenge")]
    assert len(cards) == 1
    card = cards[0]["challenge"]
    assert card["title"] == "20 squats"
    assert card["from_username"] == "ch_parent"
    assert card["to_username"] == "ch_kid"
    assert card["for_everyone"] is False


def test_the_card_shows_who_did_it_and_never_who_didnt(client, team_of_two):
    ch = start(client, team_of_two["parent"], team_of_two["team_id"]).get_json()["challenge"]
    client.post(f"/api/teams/{team_of_two['team_id']}/challenges/{ch['public_id']}/complete",
                headers=auth_headers(team_of_two["kid"]))

    thread = client.get(f"/api/teams/{team_of_two['team_id']}/messages",
                        headers=auth_headers(team_of_two["parent"])).get_json()
    card = next(m["challenge"] for m in thread if m.get("challenge"))

    assert card["completed_by"] == ["ch_kid"]
    assert card["completed_by_me"] is False      # the parent has not done it
    # There is deliberately no field naming anyone who has not done it.
    for absent in ("not_completed_by", "pending", "missed_by", "failed_by", "waiting_on"):
        assert absent not in card


def test_starting_and_completing_are_both_recorded_in_team_history(client, team_of_two):
    ch = start(client, team_of_two["parent"], team_of_two["team_id"]).get_json()["challenge"]
    client.post(f"/api/teams/{team_of_two['team_id']}/challenges/{ch['public_id']}/complete",
                headers=auth_headers(team_of_two["kid"]))
    db.session.rollback()   # only committed rows count

    moments = client.get(f"/api/teams/{team_of_two['team_id']}/moments",
                         headers=auth_headers(team_of_two["parent"])).get_json()
    kinds = {m["moment_type"] for m in moments}

    assert "challenge_started" in kinds
    assert "challenge_completed" in kinds


def test_rickie_says_something_when_a_challenge_is_completed(client, team_of_two):
    import app as appmod

    ch = start(client, team_of_two["parent"], team_of_two["team_id"]).get_json()["challenge"]
    client.post(f"/api/teams/{team_of_two['team_id']}/challenges/{ch['public_id']}/complete",
                headers=auth_headers(team_of_two["kid"]))
    db.session.rollback()

    thread = client.get(f"/api/teams/{team_of_two['team_id']}/messages",
                        headers=auth_headers(team_of_two["kid"])).get_json()
    rickie = [m for m in thread if m["sender_type"] == "rickie"]

    assert any(m["body"] in appmod.RICKIE_TEAM_MESSAGES["challenge_completed"] for m in rickie)


def test_rickie_never_comments_on_who_has_not_done_a_challenge():
    import app as appmod

    for line in appmod.RICKIE_TEAM_MESSAGES["challenge_completed"]:
        low = line.lower()
        for phrase in ("still waiting", "hasn't", "has not", "anyone else",
                       "come on", "where are", "last one"):
            assert phrase not in low, f"{line!r} nudges the people who didn't"


# ── Expiry is quiet ────────────────────────────────────────────────────────

def test_an_expired_challenge_simply_closes(client, team_of_two):
    ch = start(client, team_of_two["parent"], team_of_two["team_id"]).get_json()["challenge"]
    row = db.session.execute(
        db.select(TeamChallenge).where(TeamChallenge.public_id == ch["public_id"])).scalar_one()
    row.expires_at = datetime.datetime.utcnow() - datetime.timedelta(seconds=1)
    db.session.commit()

    thread = client.get(f"/api/teams/{team_of_two['team_id']}/messages",
                        headers=auth_headers(team_of_two["kid"])).get_json()
    card = next(m["challenge"] for m in thread if m.get("challenge"))

    assert card["open"] is False
    assert card["completed_by"] == []


def test_a_challenge_gets_an_opaque_id_not_a_sequence(client, team_of_two):
    first = start(client, team_of_two["parent"], team_of_two["team_id"]).get_json()["challenge"]
    second = start(client, team_of_two["parent"], team_of_two["team_id"]).get_json()["challenge"]

    assert len(first["public_id"]) == 32
    assert first["public_id"] != second["public_id"]


def test_completions_are_one_per_person(client, team_of_two):
    ch = start(client, team_of_two["parent"], team_of_two["team_id"]).get_json()["challenge"]
    path = f"/api/teams/{team_of_two['team_id']}/challenges/{ch['public_id']}/complete"
    client.post(path, headers=auth_headers(team_of_two["kid"]))
    client.post(path, headers=auth_headers(team_of_two["kid"]))
    db.session.rollback()

    row = db.session.execute(
        db.select(TeamChallenge).where(TeamChallenge.public_id == ch["public_id"])).scalar_one()
    count = db.session.execute(
        db.select(db.func.count(TeamChallengeCompletion.id))
        .where(TeamChallengeCompletion.challenge_id == row.id)).scalar()

    assert count == 1


# ── A waiting challenge is visible without digging ─────────────────────────

def test_a_waiting_challenge_shows_on_the_teams_list(client, team_of_two):
    """The best honest reason to open the app tomorrow should not need three
    taps to discover."""
    start(client, team_of_two["parent"], team_of_two["team_id"])

    listing = client.get("/api/teams", headers=auth_headers(team_of_two["kid"])).get_json()
    row = next(r for r in listing if r["id"] == team_of_two["team_id"])

    assert row["open_challenges"] == 1


def test_a_challenge_you_have_done_stops_waiting_for_you(client, team_of_two):
    ch = start(client, team_of_two["parent"], team_of_two["team_id"]).get_json()["challenge"]
    client.post(f"/api/teams/{team_of_two['team_id']}/challenges/{ch['public_id']}/complete",
                headers=auth_headers(team_of_two["kid"]))

    listing = client.get("/api/teams", headers=auth_headers(team_of_two["kid"])).get_json()
    row = next(r for r in listing if r["id"] == team_of_two["team_id"])

    assert row["open_challenges"] == 0
    # ...but it is still waiting for the person who has not done it.
    theirs = client.get("/api/teams", headers=auth_headers(team_of_two["parent"])).get_json()
    assert next(r for r in theirs if r["id"] == team_of_two["team_id"])["open_challenges"] == 1


def test_an_expired_challenge_stops_waiting_for_everyone(client, team_of_two):
    ch = start(client, team_of_two["parent"], team_of_two["team_id"]).get_json()["challenge"]
    row = db.session.execute(
        db.select(TeamChallenge).where(TeamChallenge.public_id == ch["public_id"])).scalar_one()
    row.expires_at = datetime.datetime.utcnow() - datetime.timedelta(seconds=1)
    db.session.commit()

    listing = client.get("/api/teams", headers=auth_headers(team_of_two["kid"])).get_json()

    assert next(r for r in listing if r["id"] == team_of_two["team_id"])["open_challenges"] == 0


def test_the_teams_list_does_not_scale_queries_with_challenges(client, team_of_two):
    from test_team_witness import count_queries

    for _ in range(6):
        start(client, team_of_two["parent"], team_of_two["team_id"], preset="walk_10")

    with count_queries() as n:
        client.get("/api/teams", headers=auth_headers(team_of_two["kid"]))

    assert n[0] <= 10, f"query count {n[0]} grows with challenges"
