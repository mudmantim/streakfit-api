"""An abandoned team is not a team, and its invite code is not a key.

An independent walkthrough took a family through the whole product — created a
team, shared photographs of a child, chatted — and then had everybody leave.
The invite code still resolved. A stranger holding it could join the empty
team, download the photographs in full, read the entire chat, and read the
team's history with everyone's usernames attached, while the two people who
had actually been in the family got 403 on those same photographs.

The composer promises "Only your team can open this." With nobody left in the
team, "your team" quietly became "whoever still has six characters in an old
text message" — and because Rotate Code requires membership, nobody could ever
revoke it.

These tests pin both halves: an empty team refuses entry, and a team that
still has somebody in it carries on working normally.
"""
from conftest import auth_headers, register_and_login


def _team(client, token, name="The Hills"):
    r = client.post("/api/teams", json={"name": name}, headers=auth_headers(token))
    assert r.status_code == 201, r.get_json()
    t = r.get_json()["team"]
    return t["id"], t["invite_code"]


def _t(client, name):
    return register_and_login(client, name, "TestPass123!")


def _abandon(client, tid, token):
    """Leave a team as its only member, leaving it EMPTY rather than deleted.

    Since the owner decision of 2026-11-13 a creator who is the only member
    deletes the team by leaving. An empty team can still exist: one abandoned
    before that rule, or one kept because a report about it is pending or
    under legal hold. The second is the live path, so it is the one used
    here; these tests are about what an empty team refuses.
    """
    import uuid
    from app import db, Report
    db.session.add(Report(public_id=uuid.uuid4().hex, team_id=tid, category="harassment",
                          subject_type="user", status="pending"))
    db.session.commit()
    r = client.post(f"/api/teams/{tid}/leave", json={"delete_team": True},
                    headers=auth_headers(token))
    assert r.status_code == 200, r.get_json()


# ── The hole ────────────────────────────────────────────────────────────────

def test_an_abandoned_teams_code_no_longer_resolves(client):
    mum = _t(client, "aban_mum")
    tid, code = _team(client, mum)
    _abandon(client, tid, mum)

    stranger = _t(client, "aban_stranger")
    r = client.get(f"/api/teams/lookup/{code}", headers=auth_headers(stranger))
    assert r.status_code == 404
    # The SAME message as an unknown code. A distinct error would tell a prober
    # the code was once real, which is the discovery this product rules out.
    assert r.get_json()["error"] == "Invalid invite code"


def test_a_stranger_cannot_join_an_abandoned_team(client):
    mum = _t(client, "aban_mum2")
    tid, code = _team(client, mum)
    _abandon(client, tid, mum)

    stranger = _t(client, "aban_stranger2")
    r = client.post(f"/api/teams/{tid}/join", json={"code": code},
                    headers=auth_headers(stranger))
    assert r.status_code == 403


def test_join_is_guarded_even_when_lookup_is_skipped(client):
    """The load-bearing one.

    /join takes a team_id in the PATH, so a caller who already knows the id
    never has to ask lookup anything. Guarding only the preview would leave the
    door open and the doorbell disconnected.
    """
    mum = _t(client, "aban_mum3")
    tid, code = _team(client, mum)
    _abandon(client, tid, mum)

    stranger = _t(client, "aban_stranger3")
    # Never calls /lookup at all.
    r = client.post(f"/api/teams/{tid}/join", json={"code": code},
                    headers=auth_headers(stranger))
    assert r.status_code == 403


def test_a_stranger_who_cannot_join_cannot_read_anything(client):
    mum = _t(client, "aban_mum4")
    tid, code = _team(client, mum)
    client.post(f"/api/teams/{tid}/messages", json={"body": "We did it!"},
                headers=auth_headers(mum))
    _abandon(client, tid, mum)

    stranger = _t(client, "aban_stranger4")
    for path in (f"/api/teams/{tid}",
                 f"/api/teams/{tid}/messages",
                 f"/api/teams/{tid}/moments",
                 f"/api/teams/{tid}/campfire"):
        r = client.get(path, headers=auth_headers(stranger))
        assert r.status_code in (403, 404), f"{path} returned {r.status_code}"


# ── And the ordinary case still works ───────────────────────────────────────

def test_a_team_with_one_member_left_still_works(client):
    """The fix must close an EMPTY team, not a shrinking one.

    A family of two where one person leaves is the common case, and the one
    remaining must still be able to invite somebody back.
    """
    mum = _t(client, "live_mum")
    kid = _t(client, "live_kid")
    tid, code = _team(client, mum)
    assert client.post(f"/api/teams/{tid}/join", json={"code": code},
                       headers=auth_headers(kid)).status_code == 200

    client.post(f"/api/teams/{tid}/leave", headers=auth_headers(kid))

    # Mum is still there, so the code still works.
    friend = _t(client, "live_friend")
    assert client.get(f"/api/teams/lookup/{code}",
                      headers=auth_headers(friend)).status_code == 200
    assert client.post(f"/api/teams/{tid}/join", json={"code": code},
                       headers=auth_headers(friend)).status_code == 200


def test_the_creator_leaving_does_not_close_a_team_others_are_in(client):
    """Since 2026-11-13 the creator hands the team on as they leave (owner
    decision); the team stays open for the people still in it."""
    mum = _t(client, "creator_mum")
    kid = _t(client, "creator_kid")
    tid, code = _team(client, mum)
    client.post(f"/api/teams/{tid}/join", json={"code": code},
                headers=auth_headers(kid))
    kid_id = client.get("/api/me", headers=auth_headers(kid)).get_json()["id"]
    r = client.post(f"/api/teams/{tid}/leave", json={"new_owner_user_id": kid_id},
                    headers=auth_headers(mum))
    assert r.status_code == 200, r.get_json()

    friend = _t(client, "creator_friend")
    assert client.get(f"/api/teams/lookup/{code}",
                      headers=auth_headers(friend)).status_code == 200
