"""A teammate is never told another account's login identifier.

Registration accepts any 2-80 character string and people register with email
addresses — this codebase already knows that, which is why
`_safe_display_name` exists to stop Rickie reading a login aloud. Four team
serializers were doing in writing, permanently, exactly what Rickie was
stopped from doing: the roster, the team history, the chat, and the challenge
cards all sent `User.username` verbatim to every other member.

Reproduced before the fix: a member registered as "olivia.hill@example.com"
appeared verbatim on the roster of every team they joined and in that team's
history, and setting a display name did not change it.

The strategy here is deliberately not "assert the name is right". It is
**scan the whole response for the login string**, at every member-visible
endpoint, because the failure mode is a field somebody forgot rather than a
field somebody got wrong.
"""
import json

from conftest import auth_headers, register_and_login

EMAIL_LOGIN = "olivia.hill@example.com"
MACHINE_LOGIN = "qa_user_1789836556_2"
# The one that matters most, and the one this file originally lacked.
#
# The first version of these tests used only the two shapes above — an email
# address and a machine handle. Both are refused by `_safe_display_name`, so
# every assertion passed against an implementation that still published
# ORDINARY logins verbatim:
#
#     ROSTER:  [{"name": "timhill"}, {"name": "oliviahill"}]
#     HISTORY: ['oliviahill joined the team']
#
# An independent adversarial review found it in minutes. A test assembled
# from the same assumptions as the code it checks is not a test, and this
# project has now learned that twice — see docs/reports for the first.
ORDINARY_LOGIN = "oliviahill"
ANOTHER_ORDINARY_LOGIN = "timhill"


def _t(client, name):
    return register_and_login(client, name, "TestPass123!")


def _team(client, token, name="The Hills"):
    r = client.post("/api/teams", json={"name": name}, headers=auth_headers(token))
    assert r.status_code == 201, r.get_json()
    t = r.get_json()["team"]
    return t["id"], t["invite_code"]


def _join_ok(client, tid, code, token):
    r = client.post(f"/api/teams/{tid}/join", json={"code": code},
                    headers=auth_headers(token))
    assert r.status_code == 200, r.get_json()


def _everything_a_member_can_read(client, tid, token):
    """Every member-visible surface, as one big blob of text to search."""
    blobs = {}
    for label, path in (
        ("team",       f"/api/teams/{tid}"),
        ("teams_list", "/api/teams"),
        ("moments",    f"/api/teams/{tid}/moments"),
        ("messages",   f"/api/teams/{tid}/messages"),
        ("campfire",   f"/api/teams/{tid}/campfire"),
        ("challenges", f"/api/teams/{tid}/challenges"),
    ):
        r = client.get(path, headers=auth_headers(token))
        if r.status_code == 200:
            blobs[label] = json.dumps(r.get_json())
    return blobs


def _assert_no_login_anywhere(blobs, login):
    leaked = [label for label, body in blobs.items() if login in body]
    assert not leaked, (
        f"{login!r} leaked into: {', '.join(leaked)}\n"
        + "\n".join(f"  {k}: {v[:300]}" for k, v in blobs.items() if k in leaked))


# ── The reproduction, as a test ─────────────────────────────────────────────

def test_an_email_login_never_reaches_another_member(client):
    kid = _t(client, EMAIL_LOGIN)
    adult = _t(client, "some_adult")
    tid, code = _team(client, adult)
    assert client.post(f"/api/teams/{tid}/join", json={"code": code},
                       headers=auth_headers(kid)).status_code == 200

    # The child does things that touch every surface.
    client.post(f"/api/teams/{tid}/messages", json={"body": "hello"},
                headers=auth_headers(kid))

    _assert_no_login_anywhere(
        _everything_a_member_can_read(client, tid, adult), EMAIL_LOGIN)


def test_a_machine_shaped_login_never_reaches_another_member(client):
    """The other shape `_safe_display_name` refuses: a generated QA handle."""
    bot = _t(client, MACHINE_LOGIN)
    adult = _t(client, "another_adult")
    tid, code = _team(client, adult)
    client.post(f"/api/teams/{tid}/join", json={"code": code},
                headers=auth_headers(bot))
    client.post(f"/api/teams/{tid}/messages", json={"body": "hi"},
                headers=auth_headers(bot))

    _assert_no_login_anywhere(
        _everything_a_member_can_read(client, tid, adult), MACHINE_LOGIN)


def test_setting_a_display_name_does_not_reveal_the_login(client):
    """The exact thing that did not work before.

    Setting a display name left the roster showing the email, because the
    roster never consulted the display name at all.
    """
    kid = _t(client, EMAIL_LOGIN)
    adult = _t(client, "third_adult")
    tid, code = _team(client, adult)
    client.post(f"/api/teams/{tid}/join", json={"code": code},
                headers=auth_headers(kid))

    client.patch("/api/me", json={"display_name": "Liv"}, headers=auth_headers(kid))
    blobs = _everything_a_member_can_read(client, tid, adult)
    _assert_no_login_anywhere(blobs, EMAIL_LOGIN)
    assert "Liv" in blobs["team"], "the chosen display name should be what peers see"


def test_changing_a_display_name_does_not_leak_the_previous_one_or_the_login(client):
    kid = _t(client, EMAIL_LOGIN)
    adult = _t(client, "fourth_adult")
    tid, code = _team(client, adult)
    client.post(f"/api/teams/{tid}/join", json={"code": code},
                headers=auth_headers(kid))

    client.patch("/api/me", json={"display_name": "Liv"}, headers=auth_headers(kid))
    client.post(f"/api/teams/{tid}/messages", json={"body": "first"},
                headers=auth_headers(kid))
    client.patch("/api/me", json={"display_name": "Olly"}, headers=auth_headers(kid))

    blobs = _everything_a_member_can_read(client, tid, adult)
    _assert_no_login_anywhere(blobs, EMAIL_LOGIN)
    # Clearing it must fall back to a safe label, never to the login.
    client.patch("/api/me", json={"display_name": ""}, headers=auth_headers(kid))
    _assert_no_login_anywhere(
        _everything_a_member_can_read(client, tid, adult), EMAIL_LOGIN)


# ── Team history is permanent, so it is the worst place to leak ─────────────

def test_team_history_carries_no_login(client):
    kid = _t(client, EMAIL_LOGIN)
    adult = _t(client, "fifth_adult")
    tid, code = _team(client, adult)
    client.post(f"/api/teams/{tid}/join", json={"code": code},
                headers=auth_headers(kid))
    client.post(f"/api/teams/{tid}/leave", headers=auth_headers(kid))

    moments = client.get(f"/api/teams/{tid}/moments",
                         headers=auth_headers(adult)).get_json()
    body = json.dumps(moments)
    assert EMAIL_LOGIN not in body, body[:400]


def test_a_challenge_message_body_carries_no_login(client):
    """This one is stored, not serialized.

    The challenge announcement was written into the chat row as
    f"{sender.username} started a challenge: ...", so fixing the serializers
    would not have helped — by the time anybody read it the login was already
    part of the durable text.
    """
    kid = _t(client, EMAIL_LOGIN)
    adult = _t(client, "sixth_adult")
    tid, code = _team(client, adult)
    client.post(f"/api/teams/{tid}/join", json={"code": code},
                headers=auth_headers(kid))

    r = client.post(f"/api/teams/{tid}/challenges",
                    json={"preset_key": "squats_20"}, headers=auth_headers(kid))
    assert r.status_code in (200, 201), r.get_json()

    msgs = client.get(f"/api/teams/{tid}/messages", headers=auth_headers(adult))
    body = json.dumps(msgs.get_json())
    # Case-insensitive on purpose: with no safe name the sentence starts
    # "Started a challenge" and names nobody at all, which is the branch this
    # test most wants to exercise.
    assert "tarted a challenge" in body, \
        "the announcement did not arrive, so this test proves nothing"
    assert EMAIL_LOGIN not in body


# ── You still get your OWN login where you legitimately need it ────────────

def test_your_own_login_is_still_returned_to_you(client):
    """The fix must not break the places that legitimately need it —
    /api/me powers the settings help text about what Rickie calls you."""
    me = _t(client, EMAIL_LOGIN)
    assert client.get("/api/me", headers=auth_headers(me)).get_json()["username"] \
        == EMAIL_LOGIN
    assert EMAIL_LOGIN in json.dumps(
        client.get("/api/me/data", headers=auth_headers(me)).get_json())


def test_a_member_without_a_safe_name_still_gets_a_stable_label(client):
    """Two members with no safe name must not both render as nothing."""
    a = _t(client, "a.person@example.com")
    b = _t(client, "b.person@example.com")
    tid, code = _team(client, a)
    client.post(f"/api/teams/{tid}/join", json={"code": code}, headers=auth_headers(b))

    members = client.get(f"/api/teams/{tid}",
                         headers=auth_headers(a)).get_json()["members"]
    names = [m["name"] for m in members]
    assert all(n for n in names), names
    assert len(set(names)) == len(names), f"members are indistinguishable: {names}"
    assert not any("@" in n for n in names), names


def test_a_legacy_announcement_row_is_sanitised_on_read(client):
    """History, without a migration.

    Announcement rows written before this change have a login baked into
    `body`. Chat messages do not expire, so those logins would sit in old
    threads indefinitely and no amount of careful serialization elsewhere
    would reach them.

    Rather than rewrite stored history — destructive, and an owner's call —
    the sentence is derived at read time for exactly the rows the system
    wrote. This test forges a row in the OLD format to prove an existing
    database is fixed by reading it, not by migrating it.
    """
    from app import TeamChallenge, TeamMessage, db

    kid = _t(client, EMAIL_LOGIN)
    adult = _t(client, "legacy_adult")
    tid, code = _team(client, adult)
    client.post(f"/api/teams/{tid}/join", json={"code": code},
                headers=auth_headers(kid))

    me = client.get("/api/me", headers=auth_headers(kid)).get_json()
    ch = TeamChallenge(public_id="legacyxyz", team_id=tid,
                       created_by_user_id=me["id"], preset_key="squats_20")
    db.session.add(ch)
    db.session.flush()
    db.session.add(TeamMessage(
        team_id=tid, sender_type="user", sender_user_id=me["id"],
        # Exactly what the old code wrote.
        body=f"{EMAIL_LOGIN} started a challenge: 20 squats",
        challenge_id=ch.id))
    db.session.commit()

    body = json.dumps(client.get(f"/api/teams/{tid}/messages",
                                 headers=auth_headers(adult)).get_json())
    assert EMAIL_LOGIN not in body, f"legacy row still leaks: {body[:400]}"
    assert "tarted a challenge: 20 squats" in body, \
        "the announcement vanished instead of being sanitised"


def test_a_persons_own_typed_message_is_never_rewritten(client):
    """The other half: derivation must touch only system-written rows.

    A member who types "Dave started a challenge: washing up" has said that,
    and the app must not edit their words. Those rows have no challenge_id.
    """
    adult = _t(client, "typer_adult")
    other = _t(client, "typer_other")
    tid, code = _team(client, adult)
    client.post(f"/api/teams/{tid}/join", json={"code": code},
                headers=auth_headers(other))

    typed = "Dave started a challenge: washing up"
    client.post(f"/api/teams/{tid}/messages", json={"body": typed},
                headers=auth_headers(adult))
    body = json.dumps(client.get(f"/api/teams/{tid}/messages",
                                 headers=auth_headers(other)).get_json())
    assert typed in body, "a member's own words were rewritten"


# ── The case the first version of this file missed ─────────────────────────

def test_an_ORDINARY_login_never_reaches_another_member(client):
    """No `@`, no digits, no machine prefix, under twenty characters — the
    shape a real person actually registers with, and the shape every earlier
    test in this file failed to cover."""
    kid = _t(client, ORDINARY_LOGIN)
    adult = _t(client, ANOTHER_ORDINARY_LOGIN)
    tid, code = _team(client, adult)
    _join_ok(client, tid, code, kid)
    client.post(f"/api/teams/{tid}/messages", json={"body": "hello"},
                headers=auth_headers(kid))

    blobs = _everything_a_member_can_read(client, tid, adult)
    _assert_no_login_anywhere(blobs, ORDINARY_LOGIN)
    # And the viewer's own login must not be echoed back at them by a peer
    # surface either — it is still a credential.
    _assert_no_login_anywhere(blobs, ANOTHER_ORDINARY_LOGIN)


def test_the_roster_labels_everyone_who_has_not_chosen_a_name(client):
    a = _t(client, ANOTHER_ORDINARY_LOGIN)
    b = _t(client, ORDINARY_LOGIN)
    tid, code = _team(client, a)
    _join_ok(client, tid, code, b)

    names = [m["name"] for m in client.get(
        f"/api/teams/{tid}", headers=auth_headers(a)).get_json()["members"]]
    assert names == ["Member 1", "Member 2"], names


def test_a_chosen_display_name_is_what_peers_see(client):
    a = _t(client, ANOTHER_ORDINARY_LOGIN)
    b = _t(client, ORDINARY_LOGIN)
    tid, code = _team(client, a)
    _join_ok(client, tid, code, b)
    client.patch("/api/me", json={"display_name": "Liv"}, headers=auth_headers(b))

    names = [m["name"] for m in client.get(
        f"/api/teams/{tid}", headers=auth_headers(a)).get_json()["members"]]
    assert "Liv" in names, names
    assert ORDINARY_LOGIN not in names, names


def test_team_history_never_names_an_ordinary_login(client):
    a = _t(client, ANOTHER_ORDINARY_LOGIN)
    b = _t(client, ORDINARY_LOGIN)
    tid, code = _team(client, a)
    _join_ok(client, tid, code, b)

    body = json.dumps(client.get(f"/api/teams/{tid}/moments",
                                 headers=auth_headers(a)).get_json())
    assert ORDINARY_LOGIN not in body, body[:300]
    assert ANOTHER_ORDINARY_LOGIN not in body, body[:300]


# ── The four peer surfaces must agree about who somebody is ──────────────────

def test_every_peer_surface_gives_a_member_the_same_label(client):
    """The roster, the history and the chat must not each invent a label.

    The roster numbers its members inline (it is sorting them anyway); the
    other surfaces call `_team_peer_labels`. Two code paths producing the same
    answer is a claim, so it is asserted here rather than assumed: a member who
    reads "Member 2" on the roster and something else in the thread has been
    told two different things about the same person.

    Mixed on purpose -- one member chooses a display name and two do not, which
    is the case where an ordinal and a chosen name have to coexist.
    """
    a = _t(client, 'timhill')            # creator, no display name
    b = _t(client, 'oliviahill')         # joiner, chooses a name
    c = _t(client, 'sarahjones')         # joiner, no display name
    tid, code = _team(client, a)
    _join_ok(client, tid, code, b)
    _join_ok(client, tid, code, c)
    client.patch('/api/me', json={'display_name': 'Olivia'},
                 headers=auth_headers(b))

    roster = client.get(f"/api/teams/{tid}", headers=auth_headers(a)).get_json()['members']
    by_id = {m['user_id']: m['name'] for m in roster}

    # Chat: every sender, echo and list alike.
    for token in (a, b, c):
        echo = client.post(f"/api/teams/{tid}/messages", json={'body': 'hi'},
                           headers=auth_headers(token)).get_json()
        assert echo['sender_username'] == by_id[echo['sender_user_id']], \
            f"chat echo disagrees with the roster: {echo}"

    listed = client.get(f"/api/teams/{tid}/messages",
                        headers=auth_headers(a)).get_json()
    for m in listed:
        if m.get('sender_user_id'):
            assert m['sender_username'] == by_id[m['sender_user_id']], \
                f"chat list disagrees with the roster: {m}"

    # History: same names, same people.
    moments = client.get(f"/api/teams/{tid}/moments",
                         headers=auth_headers(a)).get_json()
    for m in moments:
        if m.get('subject_username') and m.get('moment_type') != 'campfire_stage_reached':
            assert m['subject_username'] in by_id.values(), \
                f"history used a label no other surface knows: {m}"

    # And the mix actually happened, or this test proved nothing.
    assert 'Olivia' in by_id.values(), by_id
    assert any(v.startswith('Member ') for v in by_id.values()), by_id
    assert len(set(by_id.values())) == 3, by_id
