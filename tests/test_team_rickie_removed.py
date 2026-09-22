"""Team Rickie is gone, and Rickie the personal coach is not.

The card was a team-shaped thing that was not a team: no row, no membership,
no chat, no Campfire, rendered above the real teams from the user's own data.
It was removed by owner decision (Option C, Sept 2026). The risk in removing
it is not that it comes back by accident — it is that somebody removing "the
Rickie card" reaches for the Rickie NEXT to it, and quietly takes out the
personal coach, which is a different feature that nobody asked to lose.

So these tests are two halves of one statement: the team-shaped Rickie is
absent from the front end, and the coaching Rickie still has a working
endpoint and his entry points in the page.

Source-text assertions where the subject IS the source (a deleted function
cannot be asserted about at runtime), and real requests where there is
behaviour to exercise.
"""
import re
from pathlib import Path

import pytest

from conftest import register_and_login, auth_headers

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
STYLE = (ROOT / 'static' / 'style.css').read_text(encoding='utf-8')
INDEX = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')


def _code(text, line_comments=True):
    """Source with comments stripped.

    Every assertion below would otherwise be satisfiable by the explanatory
    comments that describe the removal — the word "Team Rickie" appears in
    several of them on purpose, because the reason it went is worth keeping.
    A test that reads those comments and concludes the card is present would
    fail forever; one that reads them and concludes it is absent would pass
    forever. Both are useless, so: strip first.
    """
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    if line_comments:
        text = re.sub(r'(?m)^\s*//.*$', '', text)
    return text


# ── The card is gone ────────────────────────────────────────────────────────

def test_the_team_rickie_card_is_not_built_anywhere():
    js = _code(APP_JS)
    for gone in ('_buildTeamRickieCard', '_refreshTeamRickieCard', 'team-rickie-card'):
        assert gone not in js, f'{gone} is still referenced in app.js'


def test_no_styles_remain_for_a_card_nothing_renders():
    assert 'team-rickie' not in _code(STYLE, line_comments=False), (
        'style.css still carries .team-rickie rules for an element that is '
        'never built — dead CSS that makes the card look revivable'
    )


def test_the_teams_list_renders_only_real_teams_and_the_actions_card():
    """The render path, read as a whole.

    `renderTeamsSection` must append team cards from the fetched data and the
    create/join card, and nothing else. Asserting on the absent name is not
    enough on its own: a card appended under a different class would pass
    that and still put a fake team back at the top of the tab.
    """
    js = _code(APP_JS)
    body = re.search(r'function renderTeamsSection\(state\)\s*\{(.*?)\n\}', js, re.S)
    assert body, 'renderTeamsSection not found'
    appended = re.findall(r'container\.appendChild\((\w+)', body.group(1))
    assert appended == ['loading', 'errWrap', '_buildTeamCard', '_buildTeamActionsCard'] or \
           set(appended) <= {'loading', 'errWrap', '_buildTeamCard',
                             '_buildTeamActionsCard', '_buildGuestTeamsPreview'}, (
        f'renderTeamsSection appends something unexpected: {appended}'
    )
    assert '_buildTeamCard' in appended and '_buildTeamActionsCard' in appended


# ── The empty state ─────────────────────────────────────────────────────────

def test_the_empty_state_says_teams_are_optional_and_offers_both_actions():
    js = _code(APP_JS)
    body = re.search(r'function _buildTeamActionsCard\(hasTeams\)\s*\{(.*?)\n\}', js, re.S)
    assert body, '_buildTeamActionsCard not found'
    text = body.group(1)
    assert 'optional' in text.lower(), (
        'the no-teams state must say teams are optional — the Teams tab is '
        'otherwise an empty page that reads as something being missing'
    )
    assert "'Create a team'" in text, 'no Create a team action'
    assert "'Join a team'" in text, 'no Join a team action'
    assert '_buildJoinTeamForm()' in text, 'joining must offer the invite-code form'


def test_the_section_heading_agrees_with_an_empty_tab():
    """The heading is part of the empty state, not scenery above it.

    "Who you're building this with" is a statement about people. Printed over
    a tab with no teams it claims company that is not there — a smaller
    version of exactly what the Team Rickie card was doing, and visible in the
    same screenshot.
    """
    js = _code(APP_JS)
    body = re.search(r'function _setTeamsSubtitle\(hasTeams\)\s*\{(.*?)\n\}', js, re.S)
    assert body, '_setTeamsSubtitle not found — the heading is fixed text again'
    assert 'Optional' in body.group(1), 'the empty heading must not imply company'
    assert re.search(r'_setTeamsSubtitle\(teams\.length > 0\)', js), (
        'nothing calls _setTeamsSubtitle with the real team count'
    )
    assert 'id="teams-subtitle"' in INDEX, 'the subtitle has no id to address'


# ── Rickie the coach is untouched ───────────────────────────────────────────

def test_the_coach_endpoint_still_answers_for_an_ordinary_user(client, monkeypatch):
    """The coach route still exists and is reachable by a normal account.

    Deliberately asserts on the 503-without-a-key path rather than calling
    Anthropic: what is being protected here is that `/api/coach` is still
    mounted and still authenticates, not that the model replies.
    """
    import app as appmod
    monkeypatch.setattr(appmod, '_anthropic_api_key', None, raising=False)
    token = register_and_login(client, 'rickie_still_here')
    r = client.post('/api/coach', json={'message': 'hello'}, headers=auth_headers(token))
    assert r.status_code != 404, '/api/coach is gone — the personal coach was removed'
    assert r.status_code in (200, 503), r.status_code


def test_the_coach_is_still_openable_from_the_page():
    """Both surviving entry points, named individually.

    Worth spelling out because they do not live in the same file, and a first
    version of this test counted `openCoach(` in app.js alone and concluded
    the coach had been gutted. It had not: the general "Ask Rickie" button is
    an inline handler in index.html, and only the insight one is in app.js.
    The Team Rickie card was the third, and is the only one that went.

    Counting call sites was the wrong assertion anyway — it would have gone
    green for three references to a coach nobody could reach.
    """
    js = _code(APP_JS)
    assert 'function openCoach' in js, 'openCoach() is gone'

    # 1. The general entry point: the "Ask Rickie" button on the dashboard.
    assert re.search(r'id="coach-ask-btn".*?openCoach\(\{\s*type:\s*[\'"]general',
                     INDEX, re.S), (
        'the general "Ask Rickie" button no longer opens the coach — with the '
        'team card gone this is how a user reaches Rickie about anything'
    )

    # 2. The contextual one: asking about today's insight.
    assert re.search(r"openCoach\(\{\s*type:\s*'insight'", js), (
        "the insight card no longer opens the coach"
    )

    assert 'coach-panel' in INDEX or 'coach-panel' in js, 'no coach panel in the page'


# ── The prompt no longer advertises a team that does not exist ──────────────

def test_rickie_is_not_told_he_is_a_starter_team():
    """The real prompt object, not the file that contains it.

    A first version fell back to scanning app.py whole when it could not find
    the constant, and failed on the COMMENT explaining why the sentence was
    removed. Reading the actual string is both stricter and honest: it is what
    gets sent to the model.
    """
    import app as appmod
    prompt = appmod._COACH_SYSTEM_PROMPT
    assert isinstance(prompt, str) and len(prompt) > 500

    assert 'starter team everybody can be part of' not in prompt, (
        'Rickie is still told he is the starter team everybody can join — he '
        'will say it back to a child, and it is not true'
    )
    # And the replacement has to actually say the true thing, or a future
    # edit that simply deletes the sentence passes this file while leaving
    # Rickie with no answer at all for "can I join your team?".
    assert 'optional' in prompt.lower(), 'the prompt no longer says teams are optional'
    assert 'no "Team Rickie"' in prompt or 'no Team Rickie' in prompt, (
        'the prompt should state plainly that there is no Team Rickie'
    )


@pytest.mark.parametrize('claim', ['starter team', 'Team Rickie is the'])
def test_no_user_facing_text_still_promises_a_team_rickie(claim):
    """Across every string a user can end up reading, not just the prompt."""
    import app as appmod
    surfaces = [appmod._COACH_SYSTEM_PROMPT, APP_JS, INDEX]
    for surface in surfaces:
        text = re.sub(r'/\*.*?\*/', '', surface, flags=re.S)
        text = re.sub(r'(?m)^\s*//.*$', '', text)
        assert claim not in text, f'user-facing text still promises: {claim!r}'
