"""An individual's life in StreakFit does not belong to any team.

StreakFit is individual-first: teams are optional. That is a promise about data
as much as about tone — exercise history, levels, acorns, unlocked filters,
personal settings and private Ask Rickie conversations are the person's, and
joining or leaving a group must not touch any of them.

`leave_team` currently deletes one membership row and nothing else, which is
right. This file is here so that stays true: a future "tidy up when somebody
leaves" is exactly the kind of well-meant change that would quietly delete a
person's history along with their membership.
"""
import datetime as dt

from app import (CoachNote, CoachTurn, DailyCompletion, Team, TeamMembership,
                 User, UserFilterUnlock, db)
from conftest import auth_headers, register_and_login


def _create_team(client, token, name):
    return client.post("/api/teams", json={"name": name},
                       headers=auth_headers(token))


def _join(client, token, team_id, code):
    return client.post(f"/api/teams/{team_id}/join", json={"code": code},
                       headers=auth_headers(token))


def _seed_personal_life(username):
    """Everything that is the PERSON's, not the team's."""
    user = db.session.execute(
        db.select(User).where(User.username == username)).scalar_one()
    user.xp_total = 420
    user.acorns_total = 60
    user.acorns_spent = 15
    user.skill_level = "intermediate"
    user.display_name = "Olivia"
    db.session.add(DailyCompletion(user_id=user.id, date=dt.date.today(),
                                   exercise_key="bodyweight_squat"))
    db.session.add(UserFilterUnlock(user_id=user.id, filter_key="sweat_mode",
                                    acorns_spent=15))
    db.session.add(CoachTurn(user_id=user.id, role="user", content="something private"))
    db.session.add(CoachNote(user_id=user.id, activities='["walking"]'))
    db.session.commit()
    return user.id


def test_leaving_a_team_takes_nothing_personal_with_it(client, app):
    owner = register_and_login(client, "boundary_owner", "TestPass123!")
    member = register_and_login(client, "boundary_member", "TestPass123!")
    team = _create_team(client, owner, "The Hills").get_json()["team"]
    assert _join(client, member, team["id"], team["invite_code"]).status_code in (200, 201)

    uid = _seed_personal_life("boundary_member")

    resp = client.post(f"/api/teams/{team['id']}/leave",
                         headers=auth_headers(member))
    assert resp.status_code == 200, resp.get_json()

    user = db.session.get(User, uid)
    assert user is not None, "leaving a team deleted the account"
    assert user.xp_total == 420, "XP was lost on leaving a team"
    assert user.acorns_total == 60, "lifetime acorns were lost on leaving a team"
    assert user.acorns_spent == 15, "spend history was lost on leaving a team"
    assert user.skill_level == "intermediate", "a personal setting was reset"
    assert user.display_name == "Olivia", "the display name was cleared"
    assert DailyCompletion.query.filter_by(user_id=uid).count() == 1, \
        "exercise history was deleted with the membership"
    assert UserFilterUnlock.query.filter_by(user_id=uid).count() == 1, \
        "a filter bought with acorns was revoked on leaving"
    assert CoachTurn.query.filter_by(user_id=uid).count() == 1, \
        "a private Ask Rickie conversation was deleted with the membership"
    assert CoachNote.query.filter_by(user_id=uid).count() == 1, \
        "Coach Notes were deleted with the membership"
    assert TeamMembership.query.filter_by(user_id=uid).count() == 0, \
        "the membership itself should be gone"


def test_a_person_can_belong_to_several_teams_at_once(client, app):
    owner = register_and_login(client, "multi_owner", "TestPass123!")
    joiner = register_and_login(client, "multi_joiner", "TestPass123!")

    ids = []
    for i in range(5):
        t = _create_team(client, owner, f"Group {i}").get_json()["team"]
        assert _join(client, joiner, t["id"], t["invite_code"]).status_code in (200, 201)
        ids.append(t["id"])

    uid = db.session.execute(
        db.select(User.id).where(User.username == "multi_joiner")).scalar_one()
    assert TeamMembership.query.filter_by(user_id=uid).count() == 5

    listed = client.get("/api/teams", headers=auth_headers(joiner)).get_json()
    got = listed if isinstance(listed, list) else listed.get("teams", [])
    assert len(got) == 5, f"a member of five teams was shown {len(got)}"


def test_leaving_one_team_does_not_touch_the_others(client, app):
    owner = register_and_login(client, "many_owner", "TestPass123!")
    joiner = register_and_login(client, "many_joiner", "TestPass123!")
    ids = []
    for i in range(3):
        t = _create_team(client, owner, f"Crew {i}").get_json()["team"]
        _join(client, joiner, t["id"], t["invite_code"])
        ids.append(t["id"])

    client.post(f"/api/teams/{ids[1]}/leave", headers=auth_headers(joiner))

    uid = db.session.execute(
        db.select(User.id).where(User.username == "many_joiner")).scalar_one()
    remaining = {m.team_id for m in TeamMembership.query.filter_by(user_id=uid)}
    assert remaining == {ids[0], ids[2]}, f"left one team, ended up in {remaining}"
    for tid in ids:
        assert db.session.get(Team, tid) is not None, \
            "leaving a team deleted the team for everybody else"


def test_a_user_in_no_team_still_has_everything_that_is_theirs(client, app):
    """The default state of the product, asserted rather than assumed."""
    token = register_and_login(client, "never_joins", "TestPass123!")
    uid = _seed_personal_life("never_joins")
    assert TeamMembership.query.filter_by(user_id=uid).count() == 0

    me = client.get("/api/me", headers=auth_headers(token)).get_json()
    assert me["xp_total"] == 420
    assert me.get("acorns_total") == 60, f"acorn fields were {sorted(me)}"
    assert me["skill_level"] == "intermediate"
    assert me["display_name"] == "Olivia"

    daily = client.get("/api/daily", headers=auth_headers(token))
    assert daily.status_code == 200, "a teamless user cannot load their mission"
