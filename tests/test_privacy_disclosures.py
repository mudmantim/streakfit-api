"""What StreakFit says about keeping data matches what it does (proposal Gaps 3-5).

* The data export said `"everything_else": "kept while the account exists"`,
  which was false for team messages: they have no time limit and outlive the
  account (Gap 3; the window itself is an OWNER DECISION, so the export now
  says there is none rather than promising one).
* Team history -- moments, challenges, "a photo was shared" -- outlives a
  deleted account without the person's name, and nothing said so (Gap 5a).
* There was no privacy page at all (Gap 4). /privacy is a DRAFT for the
  owner's review, and its day counts come from the constants the code
  enforces. Owner decision #5 (2026-11-14): it is NOT published -- 404 and no
  links -- until PRIVACY_PAGE_PUBLISHED is turned on.
* Owner decisions #2-#4 (2026-11-14): messages and history have no time limit,
  said plainly; photos are "scheduled for erasure", kept only by a legal hold.
"""
import pathlib
import re

import pytest

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


def test_the_export_says_messages_have_no_time_limit_explicitly(client):
    """Owner decision #2 (2026-11-14): kept, and said plainly. Wording only."""
    assert _export(client, 'disc_msgs')['team_messages'] == (
        "kept in the team with no time limit: they are not deleted after any "
        "set time. They stay after you delete your account, without your name, "
        "and are deleted if the team itself is deleted")


def test_the_export_says_history_left_by_a_deleted_account_is_kept(client):
    """Owner decision #3 (2026-11-14): orphaned moments are disclosed, not
    deleted. Wording only."""
    assert _export(client, 'disc_hist')['team_history'] == (
        "team history (who joined or left, challenges, that a photo was shared) "
        "is kept in the team with no time limit: it is not deleted after any set "
        "time. If you delete your account it stays, without your name")


def test_the_export_discloses_team_history_and_photo_expiry(client):
    ret = _export(client, 'disc_user2')
    assert 'stays' in ret['team_history'] and 'without your name' in ret['team_history']
    assert ret['team_photo_days'] == A.PHOTO_RETENTION_DAYS
    assert f'{A.PHOTO_RETENTION_DAYS} days' in ret['team_photos']
    assert 'deleted with your account' in ret['teams_you_created']


def test_the_export_does_not_promise_photo_erasure_unconditionally(client):
    """Owner decision #4 (2026-11-14): no "erased at 30 days" while the
    production sweeper is unverified; legal holds keep a photo, a pending
    report does not."""
    photos = _export(client, 'disc_photo')['team_photos']
    assert photos == (
        f"a photo you share with a team is shown for {A.PHOTO_RETENTION_DAYS} "
        "days. After that, its picture and caption are scheduled for erasure. A "
        "legal hold on the photo, its message, the person who sent it or the "
        "team keeps it until the hold is lifted. A report that is still being "
        "reviewed does not keep it. You can delete it sooner.")
    assert 'are erased' not in photos and 'then its picture' not in photos


# ── The delete-account screen ──────────────────────────────────────────────

def test_the_delete_screen_discloses_what_stays_and_what_happens_to_teams():
    js = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
    body = js[js.index('function openDeleteAccount()'):js.index('// ── StreakFit photo filters')]
    assert 'team history' in body and 'without your name' in body
    assert "'them, with no time limit.'" in body
    assert 'A team only you are in is deleted too' in body
    # Round 6 (owner decision #1): every handover is refused until an age can
    # be confirmed, so the screen points at Leave Team (which offers "nobody"),
    # and (D-R5-1) does not promise immediate deletion of a kept team.
    assert 'Leave Team first' in body and 'leave it without an owner' in body
    assert "it's kept only until that ends" in body
    assert 'Hand over the team' not in body


# ── The privacy page: a draft, NOT published (owner decision #5) ──────────

@pytest.fixture()
def published(app):
    """The owner has published the page (STREAKFIT_PRIVACY_PAGE_PUBLISHED=1)."""
    app.config['PRIVACY_PAGE_PUBLISHED'] = True
    try:
        yield
    finally:
        app.config['PRIVACY_PAGE_PUBLISHED'] = False


def test_the_privacy_flag_is_off_unless_the_environment_says_exactly_1():
    assert A.app.config['PRIVACY_PAGE_PUBLISHED'] is False
    src = (ROOT / 'app.py').read_text(encoding='utf-8')
    assert ("app.config['PRIVACY_PAGE_PUBLISHED'] = (\n"
            "    os.environ.get('STREAKFIT_PRIVACY_PAGE_PUBLISHED') == '1')") in src


UNPUBLISHED_PATHS = ['/privacy', '/privacy/', '/privacy?x=1', '/privacy.html',
                     '/Privacy', '/PRIVACY', '/pages/privacy.html', '/privacy/index.html',
                     '/static/privacy.html', '/static/../pages/privacy.html',
                     '/static/%2e%2e/pages/privacy.html', '/pages/', '/sitemap.xml',
                     '/robots.txt', '/static/pages/privacy.html']


@pytest.mark.parametrize('path', UNPUBLISHED_PATHS)
def test_no_url_serves_the_draft_while_it_is_unpublished(client, path):
    unknown = client.get('/no-such-page-at-all')
    for method in ('GET', 'HEAD'):
        r = client.open(path, method=method)
        assert r.status_code == 404, (method, path, r.status_code)
        if method == 'GET':
            body = r.get_data(as_text=True)
            assert 'privacy-draft-banner' not in body and 'DRAFT' not in body
            # Indistinguishable from any unknown path.
            assert r.get_json() == unknown.get_json()


def test_no_page_links_to_the_privacy_page_while_it_is_unpublished(client):
    for path in ('/', '/static/index.html', '/admin', '/static/app.js',
                 '/static/admin.html', '/manifest.json', '/static/manifest.json',
                 '/sw.js'):
        r = client.get(path)
        if r.status_code != 200:
            continue
        body = r.get_data(as_text=True)
        assert 'href="/privacy"' not in body, path
        assert 'auth-privacy-link' not in body and 'id="link-privacy"' not in body, path
    root = client.get('/').get_data(as_text=True)
    assert 'id="settings-row-privacy"' not in root
    assert '<!--privacy-link:' not in root      # the markers are removed, not shown


def test_the_draft_is_kept_in_the_repo():
    html = (ROOT / 'pages' / 'privacy.html').read_text(encoding='utf-8')
    assert 'id="privacy-draft-banner"' in html and 'NOT PUBLISHED' in html


def test_the_privacy_page_is_served_signed_out_and_marked_draft(client, published):
    r = client.get('/privacy')
    assert r.status_code == 200
    assert r.headers['Content-Type'].startswith('text/html')
    html = r.get_data(as_text=True)
    assert 'id="privacy-draft-banner"' in html and 'DRAFT' in html
    assert 'not yet StreakFit' in html
    # Every placeholder was filled; nothing like $photo_days leaks through.
    assert not re.search(r'\$[a-z_]+', html)


def test_the_privacy_page_day_counts_match_the_constants(client, published):
    html = client.get('/privacy').get_data(as_text=True)
    assert f'<span id="privacy-photo-days">{A.PHOTO_RETENTION_DAYS}</span>' in html
    assert f'<span id="privacy-coach-days">{A._COACH_TURN_MAX_AGE_DAYS}</span>' in html
    assert f'{A.EVIDENCE_RETENTION_DAYS_AFTER_CLOSURE} days after the report is closed' in html
    assert f'{A.PHOTO_EVIDENCE_MAX_AGE_DAYS} days after capture' in html


def test_the_privacy_page_discloses_what_outlives_an_account(client, published):
    html = client.get('/privacy').get_data(as_text=True)
    assert 'without your name' in html
    assert ('Team messages:</strong> kept in the team with no time limit. They are '
            'not deleted after any set time. They are deleted if the team itself is '
            'deleted.') in html
    assert ('<strong>Team history</strong> (who joined or left, challenges, "a photo '
            'was shared"): kept in the team with no time limit. It is not deleted '
            'after any set time.') in html
    assert '<li>messages you posted, with no time limit;</li>' in html
    assert 'A team that only you are in is deleted with your account' in html


def test_the_privacy_page_photo_wording_is_conditional(client, published):
    html = client.get('/privacy').get_data(as_text=True)
    assert ('After that, the picture and its caption are scheduled for erasure. A '
            'legal hold on the photo, its message, the person who sent it or the team '
            'keeps the photo until the hold is lifted. A report that is still being '
            'reviewed does not keep it.') in html
    assert 'After that the picture and its caption are erased' not in html


def test_the_privacy_page_invents_no_legal_facts(client, published):
    """Open decisions are marked as the owner's, never filled in."""
    html = client.get('/privacy').get_data(as_text=True)
    for marker in ('Owner to confirm: operator name and a privacy contact',
                   'Owner decision pending: the age policy'):
        assert marker in html
    # Decided 2026-11-14 (#2): no time limit, so no "to confirm" marker left.
    assert 'Owner to confirm: a message retention period' not in html
    assert 'COPPA' not in html and 'GDPR' not in html


def test_once_published_the_page_is_linked_from_sign_up_and_settings(client, published):
    A._ASSET_VERSION_CACHE.pop('__rendered_index__:privacy=1', None)
    index = client.get('/').get_data(as_text=True)
    assert 'id="auth-privacy-link" href="/privacy"' in index
    assert 'id="link-privacy"' in index and 'href="/privacy"' in index
    assert '<!--privacy-link:' not in index


def test_the_service_worker_never_serves_a_cached_policy():
    sw = (ROOT / 'static' / 'sw.js').read_text(encoding='utf-8')
    assert "url.pathname === '/privacy'" in sw
    assert "const CACHE = 'streakfit-v1018';" in sw
