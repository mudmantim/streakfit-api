"""What StreakFit says about keeping data matches what it does (proposal Gaps 3-5).

* The data export said `"everything_else": "kept while the account exists"`,
  which was false for team messages: they have no time limit and outlive the
  account (Gap 3; the window itself is an OWNER DECISION, so the export now
  says there is none rather than promising one).
* Team history -- moments, challenges, "a photo was shared" -- outlives a
  deleted account without the person's name, and nothing said so (Gap 5a).
* There was no privacy page at all (Gap 4). /privacy is a DRAFT for the
  owner's review, signed out, linked from sign-up and Settings, and its day
  counts come from the constants the code enforces.
"""
import pathlib
import re

import app as A
from conftest import auth_headers, register_and_login

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _export(client, name='disc_user'):
    tok = register_and_login(client, name)
    r = client.get('/api/me/data', headers=auth_headers(tok))
    assert r.status_code == 200
    return r.get_json()['retention']


# ── The export ─────────────────────────────────────────────────────────────

def test_the_export_no_longer_says_messages_are_kept_only_while_the_account_exists(client):
    ret = _export(client)
    assert 'team_messages' in ret
    assert 'no time limit' in ret['team_messages']
    assert 'after you delete your account' in ret['team_messages']
    assert 'without your name' in ret['team_messages']
    # The catch-all no longer covers shared team content by omission.
    assert ret['everything_else'] != 'kept while the account exists'


def test_the_export_discloses_team_history_and_photo_expiry(client):
    ret = _export(client, 'disc_user2')
    assert 'stays in the team' in ret['team_history'] and 'without your name' in ret['team_history']
    assert ret['team_photo_days'] == A.PHOTO_RETENTION_DAYS
    assert f'{A.PHOTO_RETENTION_DAYS} days' in ret['team_photos']
    assert 'erased' in ret['team_photos']
    assert 'deleted with your account' in ret['teams_you_created']


# ── The delete-account screen ──────────────────────────────────────────────

def test_the_delete_screen_discloses_what_stays_and_what_happens_to_teams():
    js = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
    body = js[js.index('function openDeleteAccount()'):js.index('// ── StreakFit photo filters')]
    assert 'team history' in body and 'without your name' in body
    assert 'A team only you are in is deleted too' in body
    assert 'Hand over the team' in body


# ── The privacy page ───────────────────────────────────────────────────────

def test_the_privacy_page_is_served_signed_out_and_marked_draft(client):
    r = client.get('/privacy')
    assert r.status_code == 200
    assert r.headers['Content-Type'].startswith('text/html')
    html = r.get_data(as_text=True)
    assert 'id="privacy-draft-banner"' in html and 'DRAFT' in html
    assert 'not yet StreakFit' in html
    # Every placeholder was filled; nothing like $photo_days leaks through.
    assert not re.search(r'\$[a-z_]+', html)


def test_the_privacy_page_day_counts_match_the_constants(client):
    html = client.get('/privacy').get_data(as_text=True)
    assert f'<span id="privacy-photo-days">{A.PHOTO_RETENTION_DAYS}</span>' in html
    assert f'<span id="privacy-coach-days">{A._COACH_TURN_MAX_AGE_DAYS}</span>' in html
    assert f'{A.EVIDENCE_RETENTION_DAYS_AFTER_CLOSURE} days after the report is closed' in html
    assert f'{A.PHOTO_EVIDENCE_MAX_AGE_DAYS} days after capture' in html


def test_the_privacy_page_discloses_what_outlives_an_account(client):
    html = client.get('/privacy').get_data(as_text=True)
    assert 'without your name' in html
    assert 'Team messages:</strong> kept in the team with no time limit' in html
    assert 'A team that only you are in is deleted with your account' in html


def test_the_privacy_page_invents_no_legal_facts(client):
    """Open decisions are marked as the owner's, never filled in."""
    html = client.get('/privacy').get_data(as_text=True)
    for marker in ('Owner to confirm: operator name and a privacy contact',
                   'Owner decision pending: the age policy',
                   'Owner to confirm: a message retention period'):
        assert marker in html
    assert 'COPPA' not in html and 'GDPR' not in html


def test_the_privacy_page_is_linked_from_sign_up_and_settings():
    index = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
    assert 'id="auth-privacy-link" href="/privacy"' in index
    assert 'id="link-privacy"' in index and 'href="/privacy"' in index


def test_the_service_worker_never_serves_a_cached_policy():
    sw = (ROOT / 'static' / 'sw.js').read_text(encoding='utf-8')
    assert "url.pathname === '/privacy'" in sw
    assert "const CACHE = 'streakfit-v1018';" in sw
