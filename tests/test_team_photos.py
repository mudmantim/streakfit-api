"""Private team photo sharing: authorization, sanitization, quotas, deletion.

This feature puts children's photographs in a database, so the tests are
weighted towards what must NOT happen: a non-member reading bytes, location
data surviving an upload, a locked filter being used because the client said
so, or a delete that leaves the pixels served.
"""
import datetime
import io

import pytest

from app import Team, TeamPhoto, User, db
from conftest import auth_headers, register_and_login

# These tests check ATTRIBUTION — that the right person is named on a moment,
# a message or a challenge card. Peer-visible surfaces stopped sending login
# identifiers (tests/test_peer_identity_privacy.py), so they send a chosen
# display name or "Member N".
#
# So the fixtures choose a name, which is what a real family does, and every
# assertion below keeps its original meaning. Deliberately NOT done in
# conftest: setting a display name for every account everywhere would make
# the privacy tests vacuous, because those rely on accounts that have not
# chosen one.
_register_without_a_name = register_and_login


def register_and_login(client, username, password='WalkTest123!'):
    token = _register_without_a_name(client, username, password)
    client.patch('/api/me', json={'display_name': username},
                 headers=auth_headers(token))
    return token


# A real, minimal JPEG: SOI, a baseline SOF0 declaring 64x64, a tiny scan, EOI.
# Built by hand so the tests need no image library and no binary fixture file.
# 64x64 rather than 8x8 on purpose -- PHOTO_MIN_DIMENSION rejects anything
# smaller than 32, and a fixture that trips a real guard tests the guard, not
# the feature.
_SOF0 = bytes([0xFF, 0xC0, 0x00, 0x11, 0x08, 0x00, 0x40, 0x00, 0x40, 0x03,
               0x01, 0x11, 0x00, 0x02, 0x11, 0x01, 0x03, 0x11, 0x01])
_SOS = bytes([0xFF, 0xDA, 0x00, 0x0C, 0x03, 0x01, 0x00, 0x02, 0x11, 0x03,
              0x11, 0x00, 0x3F, 0x00])
VALID_JPEG = b"\xff\xd8" + _SOF0 + _SOS + b"\x9a\x4b\x11\x22" + b"\xff\xd9"


def jpeg_with_metadata():
    """A JPEG carrying EXIF (GPS + device) and a free-text comment, the way a
    phone would hand one over."""
    exif = b"Exif\x00\x00MM\x00*GPSLatitude 51.5074 GPSLongitude -0.1278 iPhone 15 Pro"
    app1 = b"\xff\xe1" + (len(exif) + 2).to_bytes(2, "big") + exif
    note = b"home address in here"
    com = b"\xff\xfe" + (len(note) + 2).to_bytes(2, "big") + note
    return VALID_JPEG[:2] + app1 + com + VALID_JPEG[2:]


def upload(client, token, team_id, data=VALID_JPEG, caption=None, filter_key=None,
           filename="photo.jpg"):
    form = {"photo": (io.BytesIO(data), filename)}
    if caption is not None:
        form["caption"] = caption
    if filter_key is not None:
        form["filter_key"] = filter_key
    return client.post(f"/api/teams/{team_id}/photos", data=form,
                       content_type="multipart/form-data", headers=auth_headers(token))


@pytest.fixture()
def family(client):
    """A parent who owns a team, a kid in it, and an outsider who is not."""
    parent = register_and_login(client, "photo_parent")
    team = client.post("/api/teams", json={"name": "Photo Family"},
                       headers=auth_headers(parent)).get_json()["team"]
    kid = register_and_login(client, "photo_kid")
    client.post(f"/api/teams/{team['id']}/join", json={"code": team["invite_code"]},
                headers=auth_headers(kid))
    outsider = register_and_login(client, "photo_outsider")
    return {"parent": parent, "kid": kid, "outsider": outsider, "team_id": team["id"]}


# ── Who can see a photo ────────────────────────────────────────────────────

def test_a_teammate_can_view_the_photo(client, family):
    resp = upload(client, family["kid"], family["team_id"], caption="hi")
    assert resp.status_code == 201
    photo = resp.get_json()["photo"]

    got = client.get(photo["url"], headers=auth_headers(family["parent"]))

    assert got.status_code == 200
    assert got.headers["Content-Type"] == "image/jpeg"
    assert got.data[:2] == b"\xff\xd8"


def test_a_non_member_cannot_view_the_photo(client, family):
    photo = upload(client, family["kid"], family["team_id"]).get_json()["photo"]

    assert client.get(photo["url"], headers=auth_headers(family["outsider"])).status_code == 403


def test_an_unauthenticated_request_cannot_view_the_photo(client, family):
    photo = upload(client, family["kid"], family["team_id"]).get_json()["photo"]

    assert client.get(photo["url"]).status_code == 401


def test_a_non_member_cannot_upload_into_the_team(client, family):
    assert upload(client, family["outsider"], family["team_id"]).status_code == 403


def test_a_photo_id_from_another_team_is_a_plain_404(client, family):
    """Not 403: an id must not be able to confirm a photo exists elsewhere."""
    other_owner = register_and_login(client, "other_owner")
    other = client.post("/api/teams", json={"name": "Other"},
                        headers=auth_headers(other_owner)).get_json()["team"]
    theirs = upload(client, other_owner, other["id"]).get_json()["photo"]

    cross = client.get(f"/api/teams/{family['team_id']}/photos/{theirs['public_id']}",
                       headers=auth_headers(family["parent"]))

    assert cross.status_code == 404


def test_photo_responses_are_never_cached_by_a_shared_cache(client, family):
    photo = upload(client, family["kid"], family["team_id"]).get_json()["photo"]

    got = client.get(photo["url"], headers=auth_headers(family["parent"]))

    assert "private" in got.headers["Cache-Control"]
    assert "no-store" in got.headers["Cache-Control"]
    assert got.headers["X-Content-Type-Options"] == "nosniff"


def test_the_public_id_is_random_not_a_sequence(client, family):
    """A sequential id would let a member enumerate how many photos exist and
    probe for neighbours. These must be unguessable and unrelated to each other."""
    first = upload(client, family["kid"], family["team_id"]).get_json()["photo"]["public_id"]
    second = upload(client, family["kid"], family["team_id"]).get_json()["photo"]["public_id"]

    for pid in (first, second):
        assert len(pid) == 32
        assert all(c in "0123456789abcdef" for c in pid)
    assert first != second
    # Consecutive uploads must not be one character apart, the way a counter
    # rendered as hex would be.
    differing = sum(1 for a, b in zip(first, second) if a != b)
    assert differing > 8, f"ids look sequential: {first} vs {second}"


# ── What survives an upload ────────────────────────────────────────────────

def test_location_and_device_metadata_never_reach_storage(client, family):
    tainted = jpeg_with_metadata()
    assert b"GPSLatitude" in tainted and b"iPhone" in tainted

    photo = upload(client, family["kid"], family["team_id"], data=tainted).get_json()["photo"]
    served = client.get(photo["url"], headers=auth_headers(family["parent"])).data
    stored = db.session.execute(
        db.select(TeamPhoto).where(TeamPhoto.public_id == photo["public_id"])
    ).scalar_one().image_data

    for blob in (served, stored):
        assert b"GPSLatitude" not in blob
        assert b"GPSLongitude" not in blob
        assert b"iPhone" not in blob
        assert b"home address in here" not in blob
        assert b"\xff\xe1" not in blob   # no APP1 segment at all


@pytest.mark.parametrize("label,data", [
    ("png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64),
    ("svg", b'<svg xmlns="http://www.w3.org/2000/svg" onload="x()"/>'),
    ("html_with_jpeg_magic", b"\xff\xd8<script>alert(1)</script>"),
    ("zip", b"PK\x03\x04" + b"\x00" * 64),
    ("empty", b""),
])
def test_only_real_jpegs_are_accepted(client, family, label, data):
    assert upload(client, family["kid"], family["team_id"], data=data).status_code == 400


def test_a_missing_file_is_rejected(client, family):
    resp = client.post(f"/api/teams/{family['team_id']}/photos", data={"caption": "no file"},
                       content_type="multipart/form-data",
                       headers=auth_headers(family["kid"]))
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "photo_required"


def test_an_overlong_caption_is_rejected(client, family):
    resp = upload(client, family["kid"], family["team_id"], caption="x" * 200)
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "caption_too_long"


# ── Filters are the server's decision, not the client's ────────────────────

def test_a_locked_filter_cannot_be_used_just_because_the_client_asked(client, family):
    resp = upload(client, family["kid"], family["team_id"], filter_key="rickie_crew")

    assert resp.status_code == 403
    assert resp.get_json()["error"] == "filter_not_unlocked"


def test_an_unknown_filter_is_rejected(client, family):
    assert upload(client, family["kid"], family["team_id"],
                  filter_key="definitely_not_real").status_code == 403


def test_free_filters_work_for_a_brand_new_user(client, family):
    assert upload(client, family["kid"], family["team_id"],
                  filter_key="rickie_peek").status_code == 201


def test_the_catalog_reports_what_is_locked_and_why(client, family):
    body = client.get("/api/photo-filters", headers=auth_headers(family["kid"])).get_json()
    by_key = {f["key"]: f for f in body["filters"]}

    assert by_key["rickie_peek"]["unlocked"] is True
    assert by_key["rickie_crew"]["unlocked"] is False
    assert by_key["rickie_crew"]["requirement"] == "Reach level 5"
    assert by_key["golden_hour"]["cost"] == 20
    assert body["acorns_available"] == 0


def test_every_filter_declares_a_valid_unlock_rule():
    """A filter added with a typo'd unlock type would silently never unlock."""
    import app as appmod

    valid = {"free", "missions", "streak", "level", "milestone", "acorns"}
    milestone_keys = {m["key"] for m in appmod._MILESTONE_DEFINITIONS}
    for spec in appmod.PHOTO_FILTERS:
        rule = spec["unlock"]
        assert rule["type"] in valid, f"{spec['key']} has unlock type {rule['type']}"
        assert spec["name"] and spec["blurb"]
        if rule["type"] == "acorns":
            assert rule["cost"] > 0
        if rule["type"] == "milestone":
            assert rule["key"] in milestone_keys, f"{spec['key']} points at a missing milestone"
        if rule["type"] in {"missions", "streak", "level"}:
            assert rule["value"] > 0


def test_filter_render_specs_only_use_known_primitives():
    """The client implements a fixed set of primitives; a filter using
    something else would silently render as nothing."""
    import app as appmod

    known = {"tint", "overlays", "frame", "ribbon", "confetti",
             "vignette", "burst", "stat"}
    for spec in appmod.PHOTO_FILTERS:
        assert set(spec["render"]) <= known, f"{spec['key']}: {set(spec['render']) - known}"
        for overlay in spec["render"].get("overlays", []):
            assert overlay["src"].startswith("/static/")


# ── Acorns: a sink, not a slot machine ─────────────────────────────────────

def _give_acorns(username, amount):
    user = db.session.execute(db.select(User).where(User.username == username)).scalar_one()
    user.acorns_total = amount
    db.session.commit()
    return user


def test_buying_a_filter_spends_earned_acorns(client, family):
    _give_acorns("photo_kid", 50)

    resp = client.post("/api/photo-filters/golden_hour/unlock",
                       headers=auth_headers(family["kid"]))

    assert resp.status_code == 200
    assert resp.get_json()["acorns_available"] == 30
    user = db.session.execute(db.select(User).where(User.username == "photo_kid")).scalar_one()
    # Lifetime earned must not move -- the acorns_100 milestone depends on it.
    assert user.acorns_total == 50
    assert user.acorns_spent == 20


def test_a_purchased_filter_becomes_usable(client, family):
    _give_acorns("photo_kid", 50)
    client.post("/api/photo-filters/golden_hour/unlock", headers=auth_headers(family["kid"]))

    assert upload(client, family["kid"], family["team_id"],
                  filter_key="golden_hour").status_code == 201


def test_a_filter_cannot_be_bought_twice(client, family):
    _give_acorns("photo_kid", 100)
    client.post("/api/photo-filters/golden_hour/unlock", headers=auth_headers(family["kid"]))

    second = client.post("/api/photo-filters/golden_hour/unlock",
                         headers=auth_headers(family["kid"]))

    assert second.status_code == 409
    user = db.session.execute(db.select(User).where(User.username == "photo_kid")).scalar_one()
    assert user.acorns_spent == 20   # charged once, not twice


def test_cannot_buy_without_enough_acorns(client, family):
    _give_acorns("photo_kid", 5)

    resp = client.post("/api/photo-filters/golden_hour/unlock",
                       headers=auth_headers(family["kid"]))

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "not_enough_acorns"
    user = db.session.execute(db.select(User).where(User.username == "photo_kid")).scalar_one()
    assert user.acorns_spent == 0


def test_an_earned_filter_cannot_be_bought(client, family):
    _give_acorns("photo_kid", 500)

    resp = client.post("/api/photo-filters/rickie_crew/unlock",
                       headers=auth_headers(family["kid"]))

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "not_purchasable"


def test_there_is_no_way_to_acquire_acorns_except_by_earning_them(client):
    """No purchase endpoint, no bundles, no randomised grants."""
    import app as appmod

    rules = [r.rule for r in appmod.app.url_map.iter_rules()]
    assert not [r for r in rules if "acorn" in r.lower()]
    for spec in appmod.PHOTO_FILTERS:
        # A fixed price for a named filter -- never a chance of getting one.
        assert "chance" not in spec["unlock"]
        assert "odds" not in spec["unlock"]
        assert "pool" not in spec["unlock"]


# ── Deletion and expiry ────────────────────────────────────────────────────

def test_the_sender_can_delete_their_own_photo(client, family):
    photo = upload(client, family["kid"], family["team_id"]).get_json()["photo"]

    assert client.delete(photo["url"], headers=auth_headers(family["kid"])).status_code == 200
    assert client.get(photo["url"], headers=auth_headers(family["parent"])).status_code == 404


def test_deleting_actually_drops_the_bytes(client, family):
    photo = upload(client, family["kid"], family["team_id"]).get_json()["photo"]
    client.delete(photo["url"], headers=auth_headers(family["kid"]))

    row = db.session.execute(
        db.select(TeamPhoto).where(TeamPhoto.public_id == photo["public_id"])
    ).scalar_one()

    assert row.deleted_at is not None
    assert row.image_data is None      # not merely flagged — genuinely gone
    assert row.byte_size == 0


def test_the_team_creator_can_remove_someone_elses_photo(client, family):
    """The existing creator safety exception, extended to a family's photos."""
    photo = upload(client, family["kid"], family["team_id"]).get_json()["photo"]

    assert client.delete(photo["url"], headers=auth_headers(family["parent"])).status_code == 200


def test_an_ordinary_member_cannot_delete_someone_elses_photo(client, family):
    third = register_and_login(client, "photo_sibling")
    team = db.session.get(Team, family["team_id"])
    db.session.add(__import__("app").TeamMembership(team_id=team.id,
                                                    user_id=_uid("photo_sibling")))
    db.session.commit()
    photo = upload(client, family["kid"], family["team_id"]).get_json()["photo"]

    # 404, not 403, and that is the point.
    #
    # An adversarial review used this route as an existence oracle: 403 said
    # "this photograph is real but not yours", 404 said "no such photograph".
    # The pair confirmed which pictures existed. The refusal is unchanged —
    # an ordinary member still cannot delete somebody else's photo — but the
    # response no longer distinguishes the two cases, so this asserts the
    # stronger property: it is byte-for-byte what you get for a photo that
    # never existed.
    refused = client.delete(photo["url"], headers=auth_headers(third))
    imaginary = client.delete(
        f"/api/teams/{family['team_id']}/photos/{'0' * 32}",
        headers=auth_headers(third))
    assert refused.status_code == 404
    assert (refused.status_code, refused.get_json()) == \
           (imaginary.status_code, imaginary.get_json())

    # And it really is still there for the person who may delete it.
    assert client.delete(photo["url"],
                         headers=auth_headers(family["kid"])).status_code == 200


def _uid(username):
    return db.session.execute(db.select(User).where(User.username == username)).scalar_one().id


def test_deleting_is_idempotent(client, family):
    photo = upload(client, family["kid"], family["team_id"]).get_json()["photo"]
    client.delete(photo["url"], headers=auth_headers(family["kid"]))

    assert client.delete(photo["url"], headers=auth_headers(family["kid"])).status_code == 200


def test_an_expired_photo_stops_being_served(client, family):
    photo = upload(client, family["kid"], family["team_id"]).get_json()["photo"]
    row = db.session.execute(
        db.select(TeamPhoto).where(TeamPhoto.public_id == photo["public_id"])
    ).scalar_one()
    row.expires_at = datetime.datetime.utcnow() - datetime.timedelta(seconds=1)
    db.session.commit()

    assert client.get(photo["url"], headers=auth_headers(family["parent"])).status_code == 410


def test_an_upload_sets_a_retention_deadline(client, family):
    import app as appmod

    photo = upload(client, family["kid"], family["team_id"]).get_json()["photo"]
    row = db.session.execute(
        db.select(TeamPhoto).where(TeamPhoto.public_id == photo["public_id"])
    ).scalar_one()

    expected = row.created_at + datetime.timedelta(days=appmod.PHOTO_RETENTION_DAYS)
    assert abs((row.expires_at - expected).total_seconds()) < 2


# ── The thread and the team's history ──────────────────────────────────────

def test_a_photo_appears_in_the_team_thread(client, family):
    upload(client, family["kid"], family["team_id"], caption="look at this")

    thread = client.get(f"/api/teams/{family['team_id']}/messages",
                        headers=auth_headers(family["parent"])).get_json()

    with_photo = [m for m in thread if m.get("photo")]
    assert len(with_photo) == 1
    assert with_photo[0]["photo"]["caption"] == "look at this"
    assert with_photo[0]["body"] == "look at this"


def test_the_thread_never_ships_image_bytes(client, family):
    """The list is metadata only; loading it must not drag blobs through."""
    upload(client, family["kid"], family["team_id"])

    thread = client.get(f"/api/teams/{family['team_id']}/messages",
                        headers=auth_headers(family["parent"])).get_json()

    assert "image_data" not in thread[0].get("photo", {})
    assert len(client.get(f"/api/teams/{family['team_id']}/messages",
                          headers=auth_headers(family["parent"])).data) < 4096


def test_a_removed_photo_is_marked_in_the_thread_not_erased_from_it(client, family):
    photo = upload(client, family["kid"], family["team_id"], caption="oops").get_json()["photo"]
    client.delete(photo["url"], headers=auth_headers(family["kid"]))

    thread = client.get(f"/api/teams/{family['team_id']}/messages",
                        headers=auth_headers(family["parent"])).get_json()

    entry = next(m for m in thread if m.get("photo"))
    assert entry["photo"]["removed"] is True
    assert entry["photo"]["available"] is False


def test_sharing_a_photo_is_recorded_in_team_history(client, family):
    upload(client, family["kid"], family["team_id"])

    # Rollback first: these tests share one session with the app, so a row that
    # was only STAGED and never committed would still be visible to the query
    # below and the test would pass while real HTTP clients saw nothing. That
    # exact bug shipped here once and only verify_all caught it — discarding
    # uncommitted work is what makes this assertion mean "committed".
    db.session.rollback()

    moments = client.get(f"/api/teams/{family['team_id']}/moments",
                         headers=auth_headers(family["parent"])).get_json()

    shared = [m for m in moments if m["moment_type"] == "photo_shared"]
    assert len(shared) == 1
    assert shared[0]["display_text"] == "photo_kid shared a photo"


def test_history_survives_the_photo_expiring(client, family):
    """The pixels have a budget; the team's story should not."""
    photo = upload(client, family["kid"], family["team_id"]).get_json()["photo"]
    client.delete(photo["url"], headers=auth_headers(family["kid"]))

    moments = client.get(f"/api/teams/{family['team_id']}/moments",
                         headers=auth_headers(family["parent"])).get_json()

    assert any(m["moment_type"] == "photo_shared" for m in moments)


# ── Storage is bounded ─────────────────────────────────────────────────────

def test_a_team_photo_album_has_a_quota(client, family):
    import app as appmod

    row = TeamPhoto(public_id="q" * 32, team_id=family["team_id"],
                    sender_user_id=_uid("photo_kid"), content_type="image/jpeg",
                    byte_size=appmod.PHOTO_TEAM_QUOTA_BYTES, image_data=b"x")
    db.session.add(row)
    db.session.commit()

    resp = upload(client, family["kid"], family["team_id"])

    assert resp.status_code == 507
    assert resp.get_json()["error"] == "team_photo_quota_reached"


def test_deleted_photos_stop_counting_against_the_quota(client, family):
    import app as appmod

    row = TeamPhoto(public_id="d" * 32, team_id=family["team_id"],
                    sender_user_id=_uid("photo_kid"), content_type="image/jpeg",
                    byte_size=appmod.PHOTO_TEAM_QUOTA_BYTES, image_data=b"x",
                    deleted_at=datetime.datetime.utcnow())
    db.session.add(row)
    db.session.commit()

    assert upload(client, family["kid"], family["team_id"]).status_code == 201


# ── Body size limits ───────────────────────────────────────────────────────

def test_the_json_api_keeps_its_original_256kb_ceiling(client, family):
    """Raising the global limit for photo upload must not raise it everywhere."""
    resp = client.post(f"/api/teams/{family['team_id']}/messages",
                       json={"body": "x" * 400_000}, headers=auth_headers(family["kid"]))

    assert resp.status_code == 413
    assert resp.get_json()["error"] == "payload_too_large"


def test_a_photo_larger_than_the_json_ceiling_is_still_accepted(client, family):
    padded = VALID_JPEG + b"\x00" * 400_000

    assert upload(client, family["kid"], family["team_id"], data=padded).status_code == 201
