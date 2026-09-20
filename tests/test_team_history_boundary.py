"""Joining a team does not hand you everything said before you arrived.

Authorisation was "are you a member", full stop. So a new member immediately
received every prior chat message, every prior moment, and could fetch every
prior photograph. A child added to an existing team inherited all of it.

`TeamMembership.joined_at` already existed and was read nowhere — one match in
the whole codebase, the column definition. These tests are what make it mean
something.

The adversarial cases matter more than the happy ones: the boundary has to
hold on the path that serves the BYTES, not merely on the list that renders
the thread, or the image is one direct request away.
"""
import io
import json

from conftest import auth_headers, register_and_login


def _t(client, name):
    return register_and_login(client, name, "TestPass123!")


def _team(client, token, name="The Hills"):
    r = client.post("/api/teams", json={"name": name}, headers=auth_headers(token))
    assert r.status_code == 201, r.get_json()
    t = r.get_json()["team"]
    return t["id"], t["invite_code"]


def _join(client, tid, code, token):
    r = client.post(f"/api/teams/{tid}/join", json={"code": code},
                    headers=auth_headers(token))
    assert r.status_code == 200, r.get_json()


def _jpeg():
    """A real-enough JPEG the upload path accepts.

    Reuses the builder in tests/test_photo_privacy.py rather than hand-rolling
    one — my first attempt was a 1x1 image and the server correctly refused it
    as "too small", which would have left this file's most important test
    permanently skipped.
    """
    from test_photo_privacy import _jpeg as build
    return build(width=64, height=48)


def _upload_photo(client, tid, token, caption="ours"):
    return client.post(
        f"/api/teams/{tid}/photos",
        data={"photo": (io.BytesIO(_jpeg()), "p.jpg"), "caption": caption},
        headers=auth_headers(token), content_type="multipart/form-data")


# ── The boundary ────────────────────────────────────────────────────────────

def test_a_newcomer_cannot_read_messages_from_before_they_joined(client):
    a = _t(client, "hb_first")
    tid, code = _team(client, a)
    client.post(f"/api/teams/{tid}/messages", json={"body": "said before"},
                headers=auth_headers(a))

    b = _t(client, "hb_newcomer")
    _join(client, tid, code, b)
    client.post(f"/api/teams/{tid}/messages", json={"body": "said after"},
                headers=auth_headers(b))

    seen = json.dumps(client.get(f"/api/teams/{tid}/messages",
                                 headers=auth_headers(b)).get_json())
    assert "said before" not in seen, seen[:400]
    assert "said after" in seen, "the newcomer lost their own message too"


def test_the_original_member_keeps_everything(client):
    """The fix must not take history from people who were always there."""
    a = _t(client, "hb_keeps")
    tid, code = _team(client, a)
    client.post(f"/api/teams/{tid}/messages", json={"body": "said before"},
                headers=auth_headers(a))
    b = _t(client, "hb_late")
    _join(client, tid, code, b)

    seen = json.dumps(client.get(f"/api/teams/{tid}/messages",
                                 headers=auth_headers(a)).get_json())
    assert "said before" in seen, "an original member lost their own history"


def test_a_newcomer_cannot_read_moments_from_before_they_joined(client):
    a = _t(client, "hb_mom_a")
    tid, code = _team(client, a)
    b = _t(client, "hb_mom_b")
    _join(client, tid, code, b)
    c = _t(client, "hb_mom_c")
    _join(client, tid, code, c)

    moments = json.dumps(client.get(f"/api/teams/{tid}/moments",
                                    headers=auth_headers(c)).get_json())
    # c must not learn that b joined, nor that a created the team.
    assert "hb_mom_b" not in moments, moments[:400]
    assert "created the team" not in moments, moments[:400]


# ── Adversarial: the bytes, not the list ───────────────────────────────────

def test_a_newcomer_cannot_fetch_an_older_photo_by_direct_request(client):
    """The one that matters.

    Filtering the thread is not a boundary if the image is one request away.
    This skips the list entirely and asks for the bytes.
    """
    a = _t(client, "hb_ph_a")
    tid, code = _team(client, a)
    up = _upload_photo(client, tid, a, caption="before")
    assert up.status_code in (200, 201), up.get_json()
    public_id = (up.get_json().get("public_id")
                 or up.get_json().get("photo", {}).get("public_id"))
    assert public_id, up.get_json()

    b = _t(client, "hb_ph_b")
    _join(client, tid, code, b)

    # The sender can still open it.
    assert client.get(f"/api/teams/{tid}/photos/{public_id}",
                      headers=auth_headers(a)).status_code == 200
    # The newcomer cannot, and gets the same 404 as a photo that never existed.
    r = client.get(f"/api/teams/{tid}/photos/{public_id}", headers=auth_headers(b))
    assert r.status_code == 404, r.status_code


def test_leaving_and_rejoining_does_not_reopen_the_old_window(client):
    """Documented behaviour, asserted so a later change is deliberate.

    Leaving deletes the membership row, so rejoining sets a NEW joined_at and
    the old window does not reopen. That is the safe default and it is also a
    real cost: somebody who leaves a family team and comes back loses sight of
    their own family's earlier messages. Changing it needs membership history,
    which does not exist — see the Stage 1 report.
    """
    a = _t(client, "hb_re_a")
    tid, code = _team(client, a)
    b = _t(client, "hb_re_b")
    _join(client, tid, code, b)
    client.post(f"/api/teams/{tid}/messages", json={"body": "while b was here"},
                headers=auth_headers(a))

    client.post(f"/api/teams/{tid}/leave", headers=auth_headers(b))
    client.post(f"/api/teams/{tid}/messages", json={"body": "while b was away"},
                headers=auth_headers(a))
    _join(client, tid, code, b)

    seen = json.dumps(client.get(f"/api/teams/{tid}/messages",
                                 headers=auth_headers(b)).get_json())
    assert "while b was away" not in seen, "content from an absence leaked in"
    assert "while b was here" not in seen, (
        "rejoining reopened the earlier window — if this is now intended, the "
        "owner decided it and this test should say so")


def test_a_second_team_does_not_widen_access_to_the_first(client):
    """Multiple memberships must not be read as one permission."""
    a = _t(client, "hb_two_a")
    t1, c1 = _team(client, a, "Team One")
    client.post(f"/api/teams/{t1}/messages", json={"body": "one secret"},
                headers=auth_headers(a))

    b = _t(client, "hb_two_b")
    t2, c2 = _team(client, b, "Team Two")
    _join(client, t2, c2, a)          # a joins b's team
    _join(client, t1, c1, b)          # b joins a's older team

    seen = json.dumps(client.get(f"/api/teams/{t1}/messages",
                                 headers=auth_headers(b)).get_json())
    assert "one secret" not in seen, seen[:300]


def test_a_removed_member_loses_access_entirely(client):
    a = _t(client, "hb_rm_a")
    tid, code = _team(client, a)
    b = _t(client, "hb_rm_b")
    _join(client, tid, code, b)
    me_b = client.get("/api/me", headers=auth_headers(b)).get_json()["id"]

    client.delete(f"/api/teams/{tid}/members/{me_b}", headers=auth_headers(a))
    for path in (f"/api/teams/{tid}/messages", f"/api/teams/{tid}/moments"):
        assert client.get(path, headers=auth_headers(b)).status_code == 403, path


def test_a_non_member_with_a_valid_invite_code_still_reads_nothing(client):
    """Holding the code is not membership."""
    a = _t(client, "hb_code_a")
    tid, code = _team(client, a)
    client.post(f"/api/teams/{tid}/messages", json={"body": "members only"},
                headers=auth_headers(a))

    outsider = _t(client, "hb_code_out")
    for path in (f"/api/teams/{tid}/messages", f"/api/teams/{tid}/moments"):
        assert client.get(path, headers=auth_headers(outsider)).status_code == 403
