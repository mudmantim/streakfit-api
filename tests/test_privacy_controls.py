"""Leaving, seeing what is held, and not being findable.

StreakFit asks for a username and a password and nothing else — no email, no
real name, no age, no location. These tests hold that line, and cover the two
controls a family actually needs: being able to see everything the product
knows about them, and being able to take it all back out again.
"""

from app import TeamPhoto, User, UserFilterUnlock, db
from conftest import auth_headers, register_and_login
from test_team_photos import upload

PASSWORD = "WalkTest123!"


# ── A family can leave, and take the photographs with them ─────────────────

def test_a_user_can_delete_their_own_account(client):
    token = register_and_login(client, "leaver")

    resp = client.delete("/api/me", json={"password": PASSWORD}, headers=auth_headers(token))

    assert resp.status_code == 200
    assert resp.get_json()["deleted"] is True
    assert db.session.execute(
        db.select(User).where(User.username == "leaver")).scalar_one_or_none() is None


def test_deleting_an_account_requires_the_password_again(client):
    """A token left on a shared family tablet must not be able to destroy an
    account. This is the one action with no undo."""
    token = register_and_login(client, "shared_tablet")

    resp = client.delete("/api/me", json={"password": "not-the-password"},
                         headers=auth_headers(token))

    assert resp.status_code == 403
    assert db.session.execute(
        db.select(User).where(User.username == "shared_tablet")).scalar_one() is not None


def test_deleting_an_account_needs_a_token_at_all(client):
    assert client.delete("/api/me", json={"password": PASSWORD}).status_code == 401


def test_deleting_an_account_takes_the_photos_with_it(client):
    """A photograph of a person IS their personal data. The thread keeps its
    shape; the pixels and the name on them do not survive."""
    parent = register_and_login(client, "dp_parent")
    team = client.post("/api/teams", json={"name": "DP Family"},
                       headers=auth_headers(parent)).get_json()["team"]
    kid = register_and_login(client, "dp_kid")
    client.post(f"/api/teams/{team['id']}/join", json={"code": team["invite_code"]},
                headers=auth_headers(kid))
    photo = upload(client, kid, team["id"], caption="a child").get_json()["photo"]

    assert client.delete("/api/me", json={"password": PASSWORD},
                         headers=auth_headers(kid)).status_code == 200
    db.session.rollback()

    row = db.session.execute(
        db.select(TeamPhoto).where(TeamPhoto.public_id == photo["public_id"])).scalar_one()
    assert row.image_data is None
    assert row.byte_size == 0
    assert row.caption is None
    assert row.sender_user_id is None       # no dangling link to a deleted person
    assert row.deleted_at is not None
    # And it genuinely stops being served to the people who could see it.
    assert client.get(photo["url"], headers=auth_headers(parent)).status_code == 404


def test_deleting_an_account_removes_purchased_filter_unlocks(client):
    token = register_and_login(client, "unlock_leaver")
    user = db.session.execute(
        db.select(User).where(User.username == "unlock_leaver")).scalar_one()
    user.acorns_total = 100
    db.session.commit()
    user_id = user.id
    client.post("/api/photo-filters/golden_hour/unlock", headers=auth_headers(token))

    client.delete("/api/me", json={"password": PASSWORD}, headers=auth_headers(token))
    db.session.rollback()

    assert db.session.execute(
        db.select(UserFilterUnlock).where(UserFilterUnlock.user_id == user_id)
    ).scalars().all() == []


def test_the_deletion_report_counts_photos(client):
    """A person should be told what leaving actually removes."""
    parent = register_and_login(client, "rc_parent")
    team = client.post("/api/teams", json={"name": "RC"},
                       headers=auth_headers(parent)).get_json()["team"]
    kid = register_and_login(client, "rc_kid")
    client.post(f"/api/teams/{team['id']}/join", json={"code": team["invite_code"]},
                headers=auth_headers(kid))
    upload(client, kid, team["id"])

    report = client.delete("/api/me", json={"password": PASSWORD},
                           headers=auth_headers(kid)).get_json()

    assert report["counts"]["team_photo_shared"] == 1


def test_a_team_creator_is_told_why_they_cannot_delete_yet(client):
    """Blocked, not broken: tearing down a team other people are using is the
    one thing account deletion must never do on its own (ADR-0007)."""
    owner = register_and_login(client, "team_owner_leaving")
    client.post("/api/teams", json={"name": "Still In Use"}, headers=auth_headers(owner))

    resp = client.delete("/api/me", json={"password": PASSWORD}, headers=auth_headers(owner))

    assert resp.status_code == 409
    body = resp.get_json()
    assert body["error"] == "account_deletion_blocked"
    assert "team" in body["message"].lower()
    assert db.session.execute(
        db.select(User).where(User.username == "team_owner_leaving")).scalar_one() is not None


# ── Seeing what is held ────────────────────────────────────────────────────

def test_a_user_can_export_everything_held_about_them(client):
    token = register_and_login(client, "exporter")

    data = client.get("/api/me/data", headers=auth_headers(token)).get_json()

    assert data["account"]["username"] == "exporter"
    assert "exercise_completions" in data
    assert "coach_conversation" in data
    assert "photos_shared" in data


def test_the_export_states_plainly_what_is_not_collected(client):
    token = register_and_login(client, "exporter2")

    data = client.get("/api/me/data", headers=auth_headers(token)).get_json()

    joined = " ".join(data["not_collected"]).lower()
    for absent in ("email", "real name", "phone", "date of birth", "location"):
        assert absent in joined


def test_the_export_is_only_your_own(client):
    register_and_login(client, "someone_else")
    mine = register_and_login(client, "me_only")

    data = client.get("/api/me/data", headers=auth_headers(mine)).get_json()

    assert data["account"]["username"] == "me_only"
    assert client.get("/api/me/data").status_code == 401


def test_the_account_really_does_hold_no_contact_details(client):
    """If a column for an email address ever appears, this should be a
    deliberate decision rather than something that slipped in."""
    columns = {c.name for c in User.__table__.columns}
    for forbidden in ("email", "phone", "real_name", "full_name",
                      "date_of_birth", "birthdate", "address"):
        assert forbidden not in columns, f"User gained a {forbidden} column"


# ── Not being findable ─────────────────────────────────────────────────────

def test_invite_code_lookup_is_rate_limited(client, monkeypatch):
    """Unprotected this was measured at 321 probes/second, and a hit returns a
    family's team NAME — slow discovery of strangers, which the product rules
    out entirely."""
    import app as appmod

    appmod.limiter.enabled = True
    try:
        token = register_and_login(client, "prober")
        statuses = [
            client.get(f"/api/teams/lookup/ZZ{i:04d}", headers=auth_headers(token)).status_code
            for i in range(20)
        ]
    finally:
        appmod.limiter.enabled = False

    assert 429 in statuses, f"no rate limiting kicked in: {sorted(set(statuses))}"


def test_there_is_no_way_to_search_for_users_or_teams(client):
    """No discovery feed, no user search, ever."""
    import app as appmod

    rules = [r.rule.lower() for r in appmod.app.url_map.iter_rules()]
    for probe in ("search", "browse", "discover", "directory", "explore", "users"):
        matches = [r for r in rules if probe in r]
        assert not matches, f"a discovery-shaped route exists: {matches}"


def test_a_team_is_only_reachable_with_its_code(client):
    """Knowing a team id is not enough to see anything about it."""
    owner = register_and_login(client, "private_owner")
    team = client.post("/api/teams", json={"name": "Private Family"},
                       headers=auth_headers(owner)).get_json()["team"]
    stranger = register_and_login(client, "curious_stranger")

    detail = client.get(f"/api/teams/{team['id']}", headers=auth_headers(stranger))
    messages = client.get(f"/api/teams/{team['id']}/messages", headers=auth_headers(stranger))
    moments = client.get(f"/api/teams/{team['id']}/moments", headers=auth_headers(stranger))

    assert detail.status_code == 403
    assert messages.status_code == 403
    assert moments.status_code == 403
