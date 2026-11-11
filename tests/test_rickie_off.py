"""Rickie Off: a real setting, enforced by the server, not a hidden button.

Owner decision 2026-11-11 ("Yes, get a real Rickie Off setting"). The fourth
`rickie_mode`, 'off', means no Rickie at all for that account: no pictures of
him, no lines in his voice, no toasts, no roaming and no Ask Rickie, while
every fitness feature keeps working. See docs/product/rickie-requirements.md.

Two halves, as in test_team_rickie_removed.py:

- real requests where there is behaviour: the setting is stored, invalid
  values are still refused, the defaults have not moved, and POST /api/coach
  refuses an Off account with 409 rickie_off WITHOUT a model call (the model
  client is replaced by a recorder, and the recorder must stay empty);
- source-text assertions for the page, where the subject is the source: the
  option exists, the help text no longer promises "he never stops being
  available", and every Rickie surface has an Off branch.
"""
import re
import types
from pathlib import Path

import pytest

import app as appmod
from conftest import register_and_login, auth_headers

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
ROAM_JS = (ROOT / 'static' / 'rickie-roam.js').read_text(encoding='utf-8')
STYLE = (ROOT / 'static' / 'style.css').read_text(encoding='utf-8')
INDEX = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')


def _code(text, block_comments=True):
    """Source with comments stripped, so an explanatory comment can never be
    what satisfies an assertion. app.js keeps its block comments
    (`block_comments=False`): a non-greedy /* ... */ strip there runs from a
    "/*" inside a string literal to the next real comment's end and deletes
    half the file. Its comments are line comments, which are stripped."""
    if block_comments:
        text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    text = re.sub(r'<!--.*?-->', '', text, flags=re.S)
    return re.sub(r'(?m)^\s*//.*$', '', text)


def _function_body(js, signature):
    m = re.search(re.escape(signature) + r'\s*\{(.*?)\n\}', js, re.S)
    assert m, f'{signature} not found in app.js'
    return m.group(1)


# ── The model client: a recorder that must stay empty under Off ─────────────

def _install_model_recorder(monkeypatch):
    calls = []

    class _Messages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(type='text', text='Nice work. That counts.')],
                stop_reason='end_turn')

    class _Anthropic:
        def __init__(self, api_key=None, **kwargs):
            self.messages = _Messages()

    monkeypatch.setattr(appmod, '_anthropic_api_key', 'test-key-not-real')
    monkeypatch.setattr(appmod._anthropic_lib, 'Anthropic', _Anthropic)
    return calls


def _set_mode(client, token, mode):
    return client.patch('/api/me', json={'rickie_mode': mode}, headers=auth_headers(token))


# ── The setting ──────────────────────────────────────────────────────────────

def test_off_is_a_valid_mode():
    assert appmod.VALID_RICKIE_MODES == {'full', 'quiet', 'minimal', 'off'}


def test_setting_off_is_accepted_and_stored(client):
    token = register_and_login(client, 'rk_off_set')
    r = _set_mode(client, token, 'off')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['rickie_mode'] == 'off'
    me = client.get('/api/me', headers=auth_headers(token)).get_json()
    assert me['rickie_mode'] == 'off'


@pytest.mark.parametrize('mode', ['full', 'quiet', 'minimal'])
def test_the_other_modes_are_still_accepted(client, mode):
    token = register_and_login(client, f'rk_mode_{mode}')
    r = _set_mode(client, token, mode)
    assert r.status_code == 200
    assert client.get('/api/me', headers=auth_headers(token)).get_json()['rickie_mode'] == mode


@pytest.mark.parametrize('bad', ['OFF', 'none', 'hidden', 'loud', '', 0, None, ['off']])
def test_an_invalid_mode_is_still_rejected_and_changes_nothing(client, bad):
    token = register_and_login(client, 'rk_bad_' + str(abs(hash(repr(bad))))[:6])
    r = _set_mode(client, token, bad)
    assert r.status_code == 400
    assert 'off' in r.get_json()['error']           # the message names the new choice
    assert client.get('/api/me', headers=auth_headers(token)).get_json()['rickie_mode'] == 'full'


def test_the_default_is_still_full(client):
    """Making Minimal (or anything else) the default is a separate owner
    decision. Off must not have moved it."""
    token = register_and_login(client, 'rk_default')
    assert client.get('/api/me', headers=auth_headers(token)).get_json()['rickie_mode'] == 'full'
    assert appmod.User.__table__.c.rickie_mode.default.arg == 'full'


def test_off_round_trips_back_to_full(client):
    token = register_and_login(client, 'rk_round')
    assert _set_mode(client, token, 'off').status_code == 200
    assert _set_mode(client, token, 'full').get_json()['rickie_mode'] == 'full'


# ── The coach refuses an Off account, before any model call ─────────────────

def test_coach_refuses_when_off_and_makes_no_model_call(client, monkeypatch):
    calls = _install_model_recorder(monkeypatch)
    token = register_and_login(client, 'rk_coach_off')
    assert _set_mode(client, token, 'off').status_code == 200

    for body in ({'message': 'How do streaks work?'},
                 {'message': 'Tell me more', 'context': {'type': 'insight', 'insight_text': 'x'}}):
        r = client.post('/api/coach', json=body, headers=auth_headers(token))
        assert r.status_code == 409
        assert r.get_json() == {'error': 'rickie_off'}
    assert calls == [], 'an Off account reached the model'


def test_coach_refusal_does_not_depend_on_the_model_being_configured(client, monkeypatch):
    """The answer to an Off account is rickie_off, not coach_unavailable,
    whether or not a key is set."""
    monkeypatch.setattr(appmod, '_anthropic_api_key', None)
    token = register_and_login(client, 'rk_coach_nokey')
    _set_mode(client, token, 'off')
    r = client.post('/api/coach', json={'message': 'hi'}, headers=auth_headers(token))
    assert (r.status_code, r.get_json()) == (409, {'error': 'rickie_off'})


def test_coach_refusal_stores_no_conversation(client, monkeypatch):
    _install_model_recorder(monkeypatch)
    token = register_and_login(client, 'rk_coach_nostore')
    _set_mode(client, token, 'off')
    client.post('/api/coach', json={'message': 'remember this'}, headers=auth_headers(token))
    user_id = client.get('/api/me', headers=auth_headers(token)).get_json()['id']
    assert appmod._load_coach_messages(user_id) == []


@pytest.mark.parametrize('mode', ['full', 'quiet', 'minimal'])
def test_coach_still_answers_in_every_other_mode(client, monkeypatch, mode):
    calls = _install_model_recorder(monkeypatch)
    token = register_and_login(client, f'rk_coach_{mode}')
    assert _set_mode(client, token, mode).status_code == 200
    r = client.post('/api/coach', json={'message': 'How do streaks work?'},
                    headers=auth_headers(token))
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['reply'] == 'Nice work. That counts.'
    assert len(calls) == 1


def test_turning_rickie_back_on_restores_the_coach(client, monkeypatch):
    calls = _install_model_recorder(monkeypatch)
    token = register_and_login(client, 'rk_coach_back')
    _set_mode(client, token, 'off')
    assert client.post('/api/coach', json={'message': 'hi'},
                       headers=auth_headers(token)).status_code == 409
    _set_mode(client, token, 'quiet')
    assert client.post('/api/coach', json={'message': 'hi'},
                       headers=auth_headers(token)).status_code == 200
    assert len(calls) == 1


def test_forgetting_the_conversation_still_works_when_off(client):
    """Privacy controls are not Rickie's to take away: an Off account can
    still delete what was stored while he was on."""
    token = register_and_login(client, 'rk_forget_off')
    _set_mode(client, token, 'off')
    r = client.delete('/api/coach/memory', headers=auth_headers(token))
    assert r.status_code == 200


# ── /api/daily: his own first-person insight is not sent to an Off account ──

_RICKIE_ASIDE = {'type': 'rickie', 'category': 'Rickie',
                 'text': 'I tried to demonstrate a burpee once. The bin lid won.'}
_FACT = {'type': 'fact', 'category': 'Body', 'text': 'Muscles grow between workouts.'}


@pytest.mark.parametrize('mode,insight,expected', [
    ('off', _RICKIE_ASIDE, None),
    ('off', _FACT, _FACT),
    ('full', _RICKIE_ASIDE, _RICKIE_ASIDE),
    ('minimal', _RICKIE_ASIDE, _RICKIE_ASIDE),
])
def test_daily_insight_under_each_mode(client, monkeypatch, mode, insight, expected):
    monkeypatch.setattr(appmod, 'get_daily_insight', lambda date_str, user_id='demo': dict(insight))
    token = register_and_login(client, f'rk_daily_{mode}_{insight["type"]}')
    _set_mode(client, token, mode)
    r = client.get('/api/daily', headers=auth_headers(token))
    assert r.status_code == 200
    data = r.get_json()
    assert data['insight'] == expected
    assert len(data['exercises']) == 5               # the mission itself is untouched


def test_missions_xp_and_streaks_work_with_rickie_off(client):
    token = register_and_login(client, 'rk_mission_off')
    _set_mode(client, token, 'off')
    daily = client.get('/api/daily', headers=auth_headers(token)).get_json()
    for ex in daily['exercises']:
        r = client.post(f"/api/daily/{ex['key']}/complete",
                        json={'mission_date': daily['mission_date']},
                        headers=auth_headers(token))
        assert r.status_code == 200, r.get_json()
    me = client.get('/api/me', headers=auth_headers(token)).get_json()
    assert me['current_streak'] == 1
    assert me['xp_total'] > 0
    assert me['rickie_mode'] == 'off'


# ── The page (source-text) ───────────────────────────────────────────────────

def test_the_settings_offer_off_and_the_help_text_is_honest():
    html = _code(INDEX)
    select = re.search(r'<select id="rickie-mode-select".*?</select>', html, re.S)
    assert select, 'rickie-mode-select not found'
    values = re.findall(r'<option value="(\w+)"', select.group(0))
    assert values == ['full', 'quiet', 'minimal', 'off']
    assert '🦝' not in re.search(r'<option value="off">(.*?)</option>', select.group(0)).group(1)

    help_text = re.search(r'id="rickie-mode-help">(.*?)</p>', html, re.S).group(1)
    assert 'never stops being available' not in help_text
    assert 'Off' in help_text and 'Ask Rickie' in help_text


def test_off_suppresses_every_reaction_and_the_coach_entry():
    js = _code(APP_JS, block_comments=False)
    assert "mode === 'minimal' || mode === 'off'" in _function_body(js, 'function _rickieAllowsReaction(isMilestoneMoment)')
    assert re.search(r"if \(_rickieOff\(\) && !\(summary && summary\.plain\)\) return;",
                     _function_body(js, 'function showRickieReaction(line, summary)'))
    assert re.search(r'^\s*if \(_rickieOff\(\)\) return;',
                     _function_body(js, 'function openCoach(context)'), re.M)
    # "Tell me more" opens the coach, so it is not built for an Off account.
    assert '!off' in _function_body(js, 'function renderInsightCard(insight)')
    # A refusal from the server is explained, not shown as an outage.
    assert "'rickie_off'" in _function_body(js, 'async function _sendCoachMessage(message, context)')


def test_guests_are_still_full():
    js = _code(APP_JS, block_comments=False)
    assert "if (isGuest || !currentUser) return 'full';" in _function_body(js, 'function _rickieMode()')


def test_the_off_class_is_applied_and_cleared():
    js = _code(APP_JS, block_comments=False)
    body = _function_body(js, 'function _syncRickiePresence()')
    assert "classList.toggle('rickie-off', off)" in body
    assert '_syncRickiePresence()' in _function_body(js, 'function _applyRickieExpression()')
    assert '_syncRickiePresence()' in _function_body(js, 'async function loadUserPreferences()')
    assert "classList.remove('rickie-off')" in _function_body(js, 'function _resetAccountState()')


def test_every_drawn_rickie_surface_is_hidden_under_off():
    css = _code(STYLE)
    rule = re.search(r'((?:body\.rickie-off [^,{]+,\s*)*body\.rickie-off [^,{]+)\{\s*display:\s*none !important;',
                     css)
    assert rule, 'no body.rickie-off hiding rule in style.css'
    hidden = {s.strip().replace('body.rickie-off ', '') for s in rule.group(1).split(',')}
    # Every class app.js points Rickie's face at, plus the static ones.
    faces = re.search(r"querySelectorAll\(\s*'([^']+)'\s*\+\s*'([^']+)'",
                      _function_body(_code(APP_JS, block_comments=False), 'function _applyRickieExpression()'))
    for sel in (faces.group(1) + faces.group(2)).split(','):
        assert sel.strip() in hidden, f'{sel.strip()} is a Rickie image not hidden under Off'
    for sel in ('#rickie-roam-band', '#coach-ask-btn', '.coach-panel', '.team-msg-rickie',
                '#settings-row-roam'):
        assert sel in hidden, f'{sel} is not hidden under Off'


def test_no_rickie_image_in_the_page_escapes_the_off_rule():
    """Every <img> of Rickie in index.html sits after login (the auth screen
    is before any account exists) and carries a class the Off rule hides."""
    css = _code(STYLE)
    dashboard = INDEX.split('id="dashboard-view"', 1)[1]
    for cls in re.findall(r'<img src="/static/rickie[^"]*"[^>]*class="([^"]+)"', dashboard):
        assert any(f'body.rickie-off .{c}' in css for c in cls.split()), \
            f'a Rickie image with class "{cls}" is not hidden under Off'


def test_the_roamer_stands_down_under_off():
    js = _code(ROAM_JS)
    assert "classList.contains('rickie-off')" in js
    step = re.search(r'function step\(\)\s*\{(.*?)\n    \}', js, re.S).group(1)
    assert 'rickieOff()' in step
    react = re.search(r'function react\(event\)\s*\{(.*?)\n    \}', js, re.S).group(1)
    assert 'rickieOff()' in react


def test_rickie_voiced_copy_has_an_off_branch():
    """The lines that name him or speak as him, each with plain wording under Off."""
    js = _code(APP_JS, block_comments=False)
    for signature, plain in [
        ('function renderBrainBoostCard(brainBoost)', "'Today\\'s Brain Boost is ready.'"),
        ('function renderInsightCard(insight)', '"Today\'s insight is ready."'),
        ('function _mbBuildNotes(data)', '"Your first mission is where this all started."'),
        ('async function openMemoryBook()', "'Your Memory Book'"),
        ('function _syncDisplayNameField()', "'Not your login. Teammates see this if you join a team.'"),
    ]:
        assert plain in _function_body(js, signature), f'{signature}: no Off wording'
    # The places that already had Minimal's plain wording use it for Off too.
    for signature in ('function renderJourneyCard()', 'function _renderTodayStrip(daily)',
                      'function _updateRickieMoodBadge()'):
        assert '_rickieNeutral()' in _function_body(js, signature), signature
