"""Child-safety phase 0: `can()` on every capability route, behind a switch.

docs/child-safety/phase0-implementation.md, tests C1-C8.

The switch (STREAKFIT_AGE_POLICY) defaults to OFF and nothing here turns it
on outside a test. The "enforce" tests exist to prove that, once the owner
does turn it on, the checks cannot be stepped around: not by a deleted
account's token, not by HEAD instead of GET, not by paying, not by a consent
revoked a moment ago, and not by a route someone adds later without the guard.

No real child's information appears anywhere here. Every fixture is synthetic.
"""
import logging
import re

import pytest
from flask import g

import app as appmod
from app import Consent, GuardianLink, PermissionAudit, Team, TeamMembership, User, db
from conftest import auth_headers

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def policy(monkeypatch):
    def set_(value):
        if value is None:
            monkeypatch.delenv('STREAKFIT_AGE_POLICY', raising=False)
        else:
            monkeypatch.setenv('STREAKFIT_AGE_POLICY', value)
    return set_


def _person(username, band=None, plus=False):
    u = User(username=username, password_hash='x', age_band=band, is_plus=plus)
    db.session.add(u)
    db.session.commit()
    return u, auth_headers(appmod._issue_access_token(u))


def _team_with(*members):
    t = Team(name=f'Synthetic {members[0].id}', created_by_user_id=members[0].id)
    db.session.add(t)
    db.session.commit()
    for m in members:
        db.session.add(TeamMembership(team_id=t.id, user_id=m.id))
    db.session.commit()
    return t


def _consent(child, guardian, capability, revoked=False):
    link = GuardianLink(child_user_id=child.id, guardian_user_id=guardian.id,
                        method='synthetic-test')
    db.session.add(link)
    db.session.commit()
    c = Consent(child_user_id=child.id, guardian_link_id=link.id,
                capability=capability, method='synthetic-test')
    if revoked:
        c.revoked_at = appmod.datetime.utcnow()
    db.session.add(c)
    db.session.commit()
    return c


def _audits():
    return db.session.execute(db.select(PermissionAudit)).scalars().all()


# One request per guarded route. `team` is a team the caller belongs to.
def _calls(team):
    return {
        'create_team': ('post', '/api/teams', {'name': 'New synthetic team'}),
        'lookup_team_by_code': ('get', '/api/teams/lookup/NOPE2345', None),
        'join_team': ('post', f'/api/teams/{team.id}/join', {'code': 'nope'}),
        'get_team_messages': ('get', f'/api/teams/{team.id}/messages', None),
        'post_team_message': ('post', f'/api/teams/{team.id}/messages', {'body': 'hello'}),
        'upload_team_photo': ('post', f'/api/teams/{team.id}/photos', None),
        'get_team_photo': ('get', f'/api/teams/{team.id}/photos/no-such-photo', None),
        'coach': ('post', '/api/coach', {'message': 'hi'}),
        'create_team_challenge': ('post', f'/api/teams/{team.id}/challenges', {'preset_key': 'squats_20'}),
        'complete_team_challenge': ('post', f'/api/teams/{team.id}/challenges/no-such-challenge/complete', None),
    }


def _call(client, headers, spec):
    method, path, body = spec
    return getattr(client, method)(path, json=body, headers=headers)


GUARDED = {
    'create_team': 'teams', 'lookup_team_by_code': 'teams', 'join_team': 'teams',
    'get_team_messages': 'team_chat', 'post_team_message': 'team_chat',
    'upload_team_photo': 'team_photos', 'get_team_photo': 'team_photos',
    'coach': 'ask_rickie',
    'create_team_challenge': 'team_chat', 'complete_team_challenge': 'team_chat',
}


# ── C1/C2/C7: off, unset, or unrecognised -- today's behaviour, can() unused ─

@pytest.mark.parametrize('setting', [None, 'off', 'OFF', '', 'enfroce', 'on', '1'])
def test_with_the_policy_off_can_is_never_consulted_and_answers_are_unchanged(
        client, policy, monkeypatch, setting):
    unknown, h = _person('cs0_unknown_age')           # NULL band: a "child" to can()
    team = _team_with(unknown)

    policy(None)
    baseline = {name: _call(client, h, spec).status_code
                for name, spec in _calls(team).items() if name != 'create_team'}

    policy(setting)
    calls = []
    monkeypatch.setattr(appmod, 'can', lambda *a, **k: calls.append(a) or (False, 'x'))
    responses = {name: _call(client, h, spec)
                 for name, spec in _calls(team).items() if name != 'create_team'}
    now = {name: r.status_code for name, r in responses.items()}
    assert calls == []
    assert now == baseline          # join_team's 403 is a wrong invite code, as before
    assert not [n for n, r in responses.items()
                if 'capability_denied' in r.get_data(as_text=True)]
    assert _call(client, h, _calls(team)['create_team']).status_code == 201
    assert _audits() == []


def test_the_default_is_off(client, policy):
    policy(None)
    assert appmod._age_policy_mode() == 'off'


# ── C3: shadow -- decides, logs a denial, changes nothing ────────────────────

def test_shadow_logs_what_enforce_would_refuse_and_refuses_nothing(client, policy, caplog):
    unknown, h = _person('cs0_shadow')
    team = _team_with(unknown)
    policy('shadow')
    with caplog.at_level(logging.INFO):
        r = _call(client, h, _calls(team)['post_team_message'])
    assert r.status_code == 201
    lines = [rec.getMessage() for rec in caplog.records if 'capability_shadow_deny' in rec.getMessage()]
    assert lines == [f'event=capability_shadow_deny user_id={unknown.id} '
                     f'capability=team_chat reason=age_not_established']
    assert _audits() == []


def test_shadow_logs_nothing_for_an_adult(client, policy, caplog):
    adult, h = _person('cs0_shadow_adult', appmod.AGE_ADULT)
    team = _team_with(adult)
    policy('shadow')
    with caplog.at_level(logging.INFO):
        assert _call(client, h, _calls(team)['get_team_messages']).status_code == 200
    assert not [r for r in caplog.records if 'capability_' in r.getMessage()]


# ── C4: enforce -- every guarded route, every age shape ──────────────────────

@pytest.mark.parametrize('name', sorted(GUARDED))
@pytest.mark.parametrize('band,code', [
    (None, 'age_not_established'),
    (appmod.AGE_TEEN, 'teen_policy_not_set'),
    (appmod.AGE_CHILD, 'no_guardian_consent'),
])
def test_enforce_refuses_every_guarded_route_before_the_route_runs(
        client, policy, name, band, code):
    who, h = _person(f'cs0_enf_{name}_{band}', band)
    team = _team_with(who)
    policy('enforce')
    r = _call(client, h, _calls(team)[name])
    assert r.status_code == 403
    body = r.get_json()
    assert body['error'] == 'capability_denied'
    assert body['capability'] == GUARDED[name]
    expected = code
    if band == appmod.AGE_CHILD and GUARDED[name] == 'ask_rickie':
        expected = 'child_ai_assurance_missing'
    assert body['reason_code'] == expected
    assert body['message']
    audits = _audits()
    assert [(a.subject_user_id, a.capability, a.decision) for a in audits] == [
        (who.id, GUARDED[name], 'deny')]


def test_enforce_lets_an_adult_through_and_writes_no_audit_row(client, policy):
    adult, h = _person('cs0_adult', appmod.AGE_ADULT)
    team = _team_with(adult)
    policy('enforce')
    assert _call(client, h, _calls(team)['post_team_message']).status_code == 201
    assert _call(client, h, _calls(team)['get_team_messages']).status_code == 200
    assert _call(client, h, _calls(team)['get_team_photo']).status_code == 404   # the route ran
    assert _audits() == []


def test_a_consented_child_gets_exactly_the_consented_capability(client, policy):
    parent, _ = _person('cs0_parent', appmod.AGE_ADULT)
    kid, h = _person('cs0_kid', appmod.AGE_CHILD)
    team = _team_with(parent, kid)
    _consent(kid, parent, appmod.CAP_TEAM_CHAT)
    policy('enforce')
    assert _call(client, h, _calls(team)['get_team_messages']).status_code == 200
    assert _call(client, h, _calls(team)['get_team_photo']).status_code == 403
    assert _call(client, h, _calls(team)['coach']).status_code == 403


def test_consent_cannot_unlock_ask_rickie_without_the_written_assurance(client, policy):
    parent, _ = _person('cs0_parent_ai', appmod.AGE_ADULT)
    kid, h = _person('cs0_kid_ai', appmod.AGE_CHILD)
    _consent(kid, parent, appmod.CAP_ASK_RICKIE)
    policy('enforce')
    r = client.post('/api/coach', json={'message': 'hi'}, headers=h)
    assert r.status_code == 403
    assert r.get_json()['reason_code'] == 'child_ai_assurance_missing'


def test_revoking_consent_takes_effect_on_the_very_next_request(client, policy):
    parent, _ = _person('cs0_parent_rv', appmod.AGE_ADULT)
    kid, h = _person('cs0_kid_rv', appmod.AGE_CHILD)
    team = _team_with(parent, kid)
    consent = _consent(kid, parent, appmod.CAP_TEAM_CHAT)
    policy('enforce')
    assert _call(client, h, _calls(team)['get_team_messages']).status_code == 200
    consent.revoked_at = appmod.datetime.utcnow()
    db.session.commit()
    assert _call(client, h, _calls(team)['get_team_messages']).status_code == 403


def test_paying_never_grants_a_capability(client, policy):
    kid, h = _person('cs0_kid_plus', appmod.AGE_CHILD, plus=True)
    unknown, h2 = _person('cs0_unknown_plus', None, plus=True)
    team = _team_with(kid, unknown)
    policy('enforce')
    for headers in (h, h2):
        for name in ('get_team_messages', 'coach', 'create_team'):
            assert _call(client, headers, _calls(team)[name]).status_code == 403


# ── C5: Ask Rickie, denied, does nothing at all ──────────────────────────────

def test_a_denied_question_reaches_no_provider_stores_nothing_and_spends_no_allowance(
        client, policy, monkeypatch):
    unknown, h = _person('cs0_coach_denied')
    monkeypatch.setattr(appmod, '_anthropic_api_key', 'sk-test-not-real')
    reached = []
    monkeypatch.setattr(appmod._anthropic_lib, 'Anthropic',
                        lambda *a, **k: reached.append(1) or (_ for _ in ()).throw(AssertionError))
    loaded = []
    monkeypatch.setattr(appmod, '_load_coach_messages', lambda *a: loaded.append('history') or [])
    monkeypatch.setattr(appmod, '_load_coach_note_block', lambda *a: loaded.append('notes'))
    monkeypatch.setattr(appmod, '_build_rickie_context', lambda *a: loaded.append('context') or '')
    policy('enforce')
    with client:
        r = client.post('/api/coach', json={'message': 'my name is Sam and I am 9'}, headers=h)
        spent = appmod._coach_reached_provider(r)
        assert g.get('_coach_reached_provider') is None
    assert r.status_code == 403
    assert reached == [] and loaded == []
    assert spent is False
    assert db.session.execute(db.select(db.func.count(appmod.CoachTurn.id))).scalar() == 0
    assert db.session.execute(db.select(db.func.count(appmod.CoachNote.user_id))).scalar() == 0


# ── C6: a denied account can always leave, look, and clean up ────────────────

def test_a_denied_account_keeps_export_forgetting_leaving_and_deletion(client, policy):
    owner, _ = _person('cs0_owner', appmod.AGE_ADULT)
    unknown, h = _person('cs0_leaver')
    team = _team_with(owner, unknown)
    policy('enforce')
    assert client.get('/api/me/data', headers=h).status_code == 200
    assert client.delete('/api/coach/memory', headers=h).status_code in (200, 204)
    assert client.get('/api/teams', headers=h).status_code == 200
    assert client.post(f'/api/teams/{team.id}/leave', headers=h).status_code == 200
    unknown.password_hash = appmod.generate_password_hash('Synthetic-Pass-1')
    db.session.commit()
    assert client.delete('/api/me', json={'password': 'Synthetic-Pass-1'},
                         headers=h).status_code == 200


# ── C8: ways around it ───────────────────────────────────────────────────────

def test_a_deleted_accounts_token_is_not_waved_through_under_enforce(client, policy, monkeypatch):
    """With nobody to decide about, "enforce" must not fall through to the
    route: /api/coach would call the provider for a token whose account is
    gone. It answers 404, as the deletion contract does elsewhere."""
    ghost, h = _person('cs0_ghost', appmod.AGE_ADULT)
    db.session.delete(ghost)
    db.session.commit()
    monkeypatch.setattr(appmod, '_anthropic_api_key', 'sk-test-not-real')
    reached = []
    monkeypatch.setattr(appmod._anthropic_lib, 'Anthropic', lambda *a, **k: reached.append(1))
    policy('enforce')
    assert client.post('/api/coach', json={'message': 'hi'}, headers=h).status_code == 404
    assert reached == []


def test_head_is_guarded_like_get(client, policy):
    unknown, h = _person('cs0_head')
    team = _team_with(unknown)
    policy('enforce')
    assert client.head(f'/api/teams/{team.id}/messages', headers=h).status_code == 403
    assert client.head(f'/api/teams/{team.id}/photos/x', headers=h).status_code == 403


def test_the_guard_decides_on_the_token_never_on_the_body(client, policy):
    adult, _ = _person('cs0_body_adult', appmod.AGE_ADULT)
    unknown, h = _person('cs0_body_unknown')
    team = _team_with(adult, unknown)
    policy('enforce')
    r = client.post(f'/api/teams/{team.id}/messages', headers=h,
                    json={'body': 'hi', 'user_id': adult.id, 'sender_user_id': adult.id,
                          'age_band': 'adult'})
    assert r.status_code == 403


def test_without_a_token_the_answer_is_still_401(client, policy):
    policy('enforce')
    assert client.post('/api/coach', json={'message': 'hi'}).status_code == 401
    assert client.get('/api/teams/1/messages').status_code == 401


# ── C7: the self-check reports the switch ────────────────────────────────────

def _self_check_entry(client):
    body = client.get('/api/verification/self').get_json()
    return body, {c['id']: c for c in body['checks']}['child_safety.age_policy']


@pytest.mark.parametrize('setting,shown', [(None, 'off'), ('shadow', 'shadow'),
                                           ('enforce', 'enforce')])
def test_the_self_check_reports_a_known_setting(client, policy, setting, shown):
    policy(setting)
    _, entry = _self_check_entry(client)
    assert entry['status'] == 'PASS'
    assert entry['observed'].startswith(f'age policy {shown};')
    assert '10 routes guarded for ask_rickie, team_chat, team_photos, teams' in entry['observed']


def test_an_unrecognised_setting_fails_the_self_check_without_echoing_it(client, policy):
    policy('enforce-please-s3cr3t')
    body, entry = _self_check_entry(client)
    assert entry['status'] == 'FAIL'
    assert body['status'] == 'FAIL'
    assert 'UNRECOGNISED' in entry['observed']
    assert 's3cr3t' not in client.get('/api/verification/self').get_data(as_text=True)


# ── C8: the URL map -- a capability route cannot be added without the guard ──

# Routes that look like capability routes but deliberately are NOT guarded,
# each with the reason. Adding to this list is a reviewed decision.
EXEMPT = {
    'forget_coach_memory': "'Forget our conversations' must always be available",
    'list_teams': 'existing membership; leaving must stay possible',
    'get_team': 'existing membership: names, streaks, today status (no free text)',
    'leave_team': 'leaving must never be blocked',
    'remove_team_member': 'removing someone must never be blocked',
    'rotate_team_invite': 'an owner closing their team to newcomers',
    'get_team_campfire': 'existing membership; aggregate counts only',
    'get_team_moments': ('existing membership; metadata goes through _public_moment_metadata '
                         '(allow-list), so stored free text never leaves'),
    'delete_team_photo': 'removing a photo must never be blocked',
    'list_photo_filters': 'a catalogue; no other person involved',
    'unlock_photo_filter': 'spends acorns; no other person involved',
}

CAPABILITY_SHAPED = re.compile(r'/api/(coach|teams?)(/|$)|photo|message|chat|rickie', re.I)


def _unclassified(rules):
    """Capability-shaped (rule, view) pairs that neither carry the guard nor
    are exempt. Admin routes are not user capabilities."""
    missing = []
    for rule, view in rules:
        path = rule.rule
        if not CAPABILITY_SHAPED.search(path) or path.startswith('/api/admin') or path == '/admin':
            continue
        if getattr(view, '_streakfit_capability', None):
            continue
        if view.__name__ in EXEMPT:
            continue
        missing.append(f'{path} -> {view.__name__}')
    return missing


def _app_rules():
    return [(r, appmod.app.view_functions[r.endpoint]) for r in appmod.app.url_map.iter_rules()
            if r.endpoint != 'static']


def test_every_capability_shaped_route_is_guarded_or_explicitly_exempt():
    assert _unclassified(_app_rules()) == []


def test_the_guarded_set_is_exactly_the_reviewed_one():
    guarded = {view.__name__: view._streakfit_capability for _, view in _app_rules()
               if getattr(view, '_streakfit_capability', None)}
    assert guarded == GUARDED


def test_the_exempt_list_names_only_routes_that_exist():
    names = {view.__name__ for _, view in _app_rules()}
    assert set(EXEMPT) <= names, set(EXEMPT) - names


def test_a_new_unguarded_capability_route_would_fail_this_file():
    """The classifier, fed a synthetic route, catches it."""
    class Rule:
        rule = '/api/teams/<int:team_id>/voice-notes'

    def post_voice_note():
        pass
    assert _unclassified([(Rule, post_voice_note)]) == [
        '/api/teams/<int:team_id>/voice-notes -> post_voice_note']


def test_the_guard_refuses_an_unknown_capability_at_import_time():
    with pytest.raises(ValueError):
        appmod.requires_capability('teleport')


# ── Review A1: moments must not carry stored free text, whatever the row holds ─

def _moment(team, moment_type, subject, metadata):
    m = appmod.TeamMoment(team_id=team.id, moment_type=moment_type,
                          subject_user_id=subject.id if subject else None,
                          moment_metadata=metadata if isinstance(metadata, str) or metadata is None
                          else appmod.json.dumps(metadata))
    db.session.add(m)
    db.session.commit()
    return m


def test_the_original_exploit_a_legacy_caption_no_longer_reaches_a_refused_member(client, policy):
    """The review's reproduction: a photo_shared moment as upload wrote it
    before R1 D4 carries the caption. Under enforce, the member refused chat
    and photos got 403 on messages but read the caption through moments."""
    adult, ha = _person('cs0_a1_adult', appmod.AGE_ADULT)
    unknown, hk = _person('cs0_a1_unknown')
    team = _team_with(adult, unknown)
    _moment(team, 'photo_shared', adult, {"caption": "pick me up at 5, gate B"})
    policy('enforce')
    assert client.get(f'/api/teams/{team.id}/messages', headers=hk).status_code == 403
    r = client.get(f'/api/teams/{team.id}/moments', headers=hk)
    assert r.status_code == 200
    assert 'gate B' not in r.get_data(as_text=True)
    shared = [m for m in r.get_json() if m['moment_type'] == 'photo_shared']
    assert shared and shared[0]['metadata'] is None
    assert shared[0]['display_text'] == f"{shared[0]['subject_username']} shared a photo"
    # Not a policy effect: with the switch off the caption is gone too.
    policy(None)
    assert 'gate B' not in client.get(f'/api/teams/{team.id}/moments', headers=ha).get_data(as_text=True)


@pytest.mark.parametrize('moment_type,stored,expected', [
    ('challenge_started', {'title': '20 squats'}, {'title': '20 squats'}),
    ('challenge_completed', {'title': '20 squats'}, {'title': '20 squats'}),
    ('challenge_started', {'title': 'call me on 555-0100'}, None),          # not a preset
    ('challenge_started', {'title': '20 squats', 'note': 'hi'}, {'title': '20 squats'}),
    ('campfire_stage_reached', {'stage': 'Bonfire', 'total_team_missions': 750},
     {'stage': 'Bonfire', 'total_team_missions': 750}),
    ('campfire_stage_reached', {'stage': 'my secret', 'total_team_missions': '750'}, None),
    ('campfire_log_added', {'total_team_missions': 3}, {'total_team_missions': 3}),
    ('campfire_log_added', {'total_team_missions': True}, None),
    ('photo_shared', {'caption': 'anything'}, None),
    ('member_joined', {'anything': 'at all'}, None),
    ('photo_shared', 'not json {', None),
    ('photo_shared', '["a list"]', None),
    ('some_future_type', {'text': 'free'}, None),
])
def test_moment_metadata_is_an_allow_list_of_server_written_values(moment_type, stored, expected):
    raw = stored if isinstance(stored, str) else appmod.json.dumps(stored)
    assert appmod._public_moment_metadata(moment_type, raw) == expected


def test_every_moment_the_server_writes_today_survives_the_filter(client):
    """The allow-list must not blank out legitimate moments: drive the real
    writers and read the feed back."""
    adult, h = _person('cs0_a1_writer', appmod.AGE_ADULT)
    team = _team_with(adult)
    r = client.post(f'/api/teams/{team.id}/challenges', headers=h, json={'preset_key': 'squats_20'})
    assert r.status_code == 201
    moments = client.get(f'/api/teams/{team.id}/moments', headers=h).get_json()
    started = [m for m in moments if m['moment_type'] == 'challenge_started']
    assert started[0]['metadata'] == {'title': '20 squats'}
    assert started[0]['display_text'].endswith('started a challenge: 20 squats')


# ── Review B8: a team challenge is a post to the team thread ─────────────────

def test_a_member_refused_team_chat_cannot_post_into_the_thread_through_a_challenge(client, policy):
    adult, ha = _person('cs0_b8_adult', appmod.AGE_ADULT)
    unknown, hk = _person('cs0_b8_unknown')
    team = _team_with(adult, unknown)
    policy('enforce')
    r = client.post(f'/api/teams/{team.id}/challenges', headers=hk, json={'preset_key': 'squats_20'})
    assert r.status_code == 403 and r.get_json()['capability'] == 'team_chat'
    assert client.get(f'/api/teams/{team.id}/messages', headers=ha).get_json() == []


def test_a_child_with_chat_consent_takes_part_in_challenges(client, policy):
    parent, hp = _person('cs0_b8_parent', appmod.AGE_ADULT)
    kid, hk = _person('cs0_b8_kid', appmod.AGE_CHILD)
    team = _team_with(parent, kid)
    _consent(kid, parent, appmod.CAP_TEAM_CHAT)
    policy('enforce')
    r = client.post(f'/api/teams/{team.id}/challenges', headers=hp, json={'preset_key': 'walk_10'})
    assert r.status_code == 201
    public_id = r.get_json()['challenge']['public_id']
    done = client.post(f'/api/teams/{team.id}/challenges/{public_id}/complete', headers=hk)
    assert done.status_code == 200 and done.get_json()['completed'] is True


def test_personal_challenges_are_untouched_by_the_policy(client, policy):
    unknown, h = _person('cs0_b8_solo')
    policy('enforce')
    r = client.post('/api/challenges', headers=h, json={'title': 'Stretch every morning'})
    assert r.status_code in (200, 201)
