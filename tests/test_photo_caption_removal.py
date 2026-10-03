"""A removed photo takes its caption with it, everywhere the caption was copied.

Upload writes the caption to three places: the photo row, the body of the
photo's chat message, and the photo_shared team-history moment. Removing the
photo, or deleting the sender's account, cleared only the first (and photo
removal not even that), so the caption kept coming back from GET /messages
and GET /moments and stayed in the database (E2E R1 D4, reproduced twice by
the auditor and twice by the parent).

The thread keeps its "Photo removed" shape and the history keeps the fact
that a photo was shared. Only the person's words go.
"""
import json

from conftest import auth_headers
from app import db, TeamMessage, TeamMoment, TeamPhoto
from test_team_photos import family, upload  # noqa: F401  (fixture)

MARK = 'CAPTION-MARKER-private words'


def _everything_a_teammate_can_read(client, token, team_id):
    msgs = client.get(f'/api/teams/{team_id}/messages', headers=auth_headers(token)).get_json()
    moments = client.get(f'/api/teams/{team_id}/moments', headers=auth_headers(token)).get_json()
    return json.dumps(msgs) + json.dumps(moments)


def _caption_rows():
    db.session.rollback()
    return {
        'team_photo': TeamPhoto.query.filter(TeamPhoto.caption.contains(MARK)).count(),
        'team_message': TeamMessage.query.filter(TeamMessage.body.contains(MARK)).count(),
        'team_moment': TeamMoment.query.filter(TeamMoment.moment_metadata.contains(MARK)).count(),
    }


def test_deleting_the_account_removes_the_caption_everywhere(client, family):  # noqa: F811
    assert upload(client, family['kid'], family['team_id'], caption=MARK).status_code == 201
    assert MARK in _everything_a_teammate_can_read(client, family['parent'], family['team_id'])

    r = client.delete('/api/me', json={'password': 'WalkTest123!'},
                      headers=auth_headers(family['kid']))
    assert r.status_code == 200

    assert MARK not in _everything_a_teammate_can_read(client, family['parent'], family['team_id'])
    assert _caption_rows() == {'team_photo': 0, 'team_message': 0, 'team_moment': 0}


def test_removing_the_photo_removes_the_caption_everywhere(client, family):  # noqa: F811
    photo = upload(client, family['kid'], family['team_id'], caption=MARK).get_json()['photo']
    assert client.delete(photo['url'], headers=auth_headers(family['kid'])).status_code == 200

    assert MARK not in _everything_a_teammate_can_read(client, family['parent'], family['team_id'])
    assert _caption_rows() == {'team_photo': 0, 'team_message': 0, 'team_moment': 0}


def test_removing_one_photo_leaves_the_senders_other_captions_alone(client, family):  # noqa: F811
    keep = upload(client, family['kid'], family['team_id'], caption='KEEP-THIS-ONE').get_json()['photo']
    gone = upload(client, family['kid'], family['team_id'], caption=MARK).get_json()['photo']
    client.delete(gone['url'], headers=auth_headers(family['kid']))

    seen = _everything_a_teammate_can_read(client, family['parent'], family['team_id'])
    assert 'KEEP-THIS-ONE' in seen and MARK not in seen
    assert keep['caption'] == 'KEEP-THIS-ONE'


def test_history_still_records_that_a_photo_was_shared(client, family):  # noqa: F811
    photo = upload(client, family['kid'], family['team_id'], caption=MARK).get_json()['photo']
    client.delete(photo['url'], headers=auth_headers(family['kid']))
    moments = client.get(f"/api/teams/{family['team_id']}/moments",
                         headers=auth_headers(family['parent'])).get_json()
    assert [m['display_text'] for m in moments if m['moment_type'] == 'photo_shared'] \
        == ['photo_kid shared a photo']


def _plant_pre_fix_moment(team_id, sender_id, caption):
    """A photo_shared moment as uploads wrote it before D4: caption in metadata.
    Production already holds rows like this, so the scrub must reach them."""
    db.session.add(TeamMoment(team_id=team_id, moment_type='photo_shared',
                              subject_user_id=sender_id,
                              moment_metadata=json.dumps({"caption": caption[:60]})))
    db.session.commit()


def _kid_id(client, family):  # noqa: F811
    return client.get('/api/me', headers=auth_headers(family['kid'])).get_json()['id']


def test_pre_fix_moment_caption_is_scrubbed_when_the_photo_goes(client, family):  # noqa: F811
    photo = upload(client, family['kid'], family['team_id'], caption=MARK).get_json()['photo']
    _plant_pre_fix_moment(family['team_id'], _kid_id(client, family), MARK)
    assert _caption_rows()['team_moment'] >= 1

    client.delete(photo['url'], headers=auth_headers(family['kid']))
    assert _caption_rows()['team_moment'] == 0


def test_pre_fix_moment_caption_is_scrubbed_when_the_account_goes(client, family):  # noqa: F811
    upload(client, family['kid'], family['team_id'], caption=MARK)
    _plant_pre_fix_moment(family['team_id'], _kid_id(client, family), MARK)
    assert _caption_rows()['team_moment'] >= 1

    client.delete('/api/me', json={'password': 'WalkTest123!'}, headers=auth_headers(family['kid']))
    assert _caption_rows()['team_moment'] == 0


# ── Residue from removals made before the fix (RC-B1 D4 review) ────────────

def _leave_pre_fix_residue(client, family, caption):  # noqa: F811
    """A photo removed the way 1613dfd removed it: pixels gone, words kept."""
    from datetime import datetime
    photo = upload(client, family['kid'], family['team_id'], caption=caption).get_json()['photo']
    row = TeamPhoto.query.filter_by(public_id=photo['public_id']).one()
    row.deleted_at = datetime.utcnow()
    row.image_data = None
    _plant_pre_fix_moment(family['team_id'], row.sender_user_id, caption)
    db.session.commit()
    return photo


def test_the_scrub_command_is_a_dry_run_until_told_otherwise(client, family):  # noqa: F811
    _leave_pre_fix_residue(client, family, MARK)
    out = client.application.test_cli_runner().invoke(args=['photo-caption-scrub']).output
    assert 'would clear 1 photo captions, 1 message bodies' in out
    assert _caption_rows() == {'team_photo': 1, 'team_message': 1, 'team_moment': 1}


def test_the_scrub_command_clears_pre_fix_residue_and_leaves_live_photos(client, family):  # noqa: F811
    upload(client, family['kid'], family['team_id'], caption='LIVE-CAPTION')
    _leave_pre_fix_residue(client, family, MARK)
    out = client.application.test_cli_runner().invoke(args=['photo-caption-scrub', '--execute']).output
    assert out.startswith('cleared')
    assert _caption_rows() == {'team_photo': 0, 'team_message': 0, 'team_moment': 0}
    seen = _everything_a_teammate_can_read(client, family['parent'], family['team_id'])
    assert MARK not in seen and 'LIVE-CAPTION' in seen
    again = client.application.test_cli_runner().invoke(args=['photo-caption-scrub', '--execute']).output
    assert 'cleared 0 photo captions, 0 message bodies, 0 moment captions' in again


def test_deleting_an_already_removed_photo_again_clears_its_residue(client, family):  # noqa: F811
    photo = _leave_pre_fix_residue(client, family, MARK)
    assert client.delete(photo['url'], headers=auth_headers(family['kid'])).status_code == 200
    assert _caption_rows()['team_message'] == 0 and _caption_rows()['team_photo'] == 0
