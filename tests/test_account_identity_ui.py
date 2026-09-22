"""The four confirmed defects behind "the roster shows Member N".

Reproduced on production 2026-09-22, not theorised. They are separate bugs
that combined into one complaint:

1. The StreakFit name persisted only on `onchange`, which fires when an input
   loses focus. On a phone it frequently never fired: people typed a name,
   navigated away, and it was silently discarded. There was no Save button.
2. Success was silent. The handler spoke up on failure and said nothing on
   success, so "saved" and "never sent" looked identical.
3. The field was labelled "Rickie calls you" and nothing said teammates see
   it, so nobody set one and every roster read "Member N".
4. Logout cleared the token, `isGuest` and `guestCompleted` — and left the
   rest of the module populated, including `blockedUserIds`.

(4) is the one that is more than cosmetic. `blockedUserIds` is a private
moderation record: who YOU have blocked. It drives the Block/Unblock state on
every roster row, so carried across a sign-out it renders one account's
moderation decisions inside another account's session.

These assert the SHIPPED static assets, because every one of these defects
lived in markup or module state rather than in a response body. A server test
would have passed throughout.
"""
import re
from pathlib import Path

import pytest

import app as appmod

STATIC = Path(appmod.__file__).resolve().parent / 'static'
APP_JS = (STATIC / 'app.js').read_text(encoding='utf-8')
INDEX = (STATIC / 'index.html').read_text(encoding='utf-8')
STYLE = (STATIC / 'style.css').read_text(encoding='utf-8')
SW = (STATIC / 'sw.js').read_text(encoding='utf-8')


# ── 1 & 2: the name can be saved, and says so ──────────────────────────────

def test_the_name_field_has_an_explicit_save_control():
    """A name you cannot see how to save is a name that does not get saved."""
    assert 'id="display-name-save"' in INDEX, "no Save button for the StreakFit name"
    assert 'handleDisplayNameSave()' in INDEX, "the Save button is not wired to anything"


def test_saving_does_not_depend_on_the_field_losing_focus():
    """THE REGRESSION. `onchange` fires on blur, which on a phone often never
    happens — the user taps away to another screen and the value is gone."""
    m = re.search(r'id="display-name-input"(.*?)>', INDEX, re.S)
    assert m, 'display-name-input not found in the markup'
    assert 'onchange=' not in m.group(1), (
        "the name field still persists via onchange; that is the defect — it "
        "only fires when the field loses focus")


def test_success_is_reported_and_not_only_failure():
    assert '_showDisplayNameSaved' in APP_JS, "nothing reports a successful save"
    assert 'id="display-name-saved"' in INDEX, "no element to report it in"
    assert 'aria-live="polite"' in INDEX, (
        "the confirmation is not announced to a screen reader")


def test_clearing_the_name_is_reported_differently_from_saving_one():
    """"Saved" over an empty box reads as though nothing happened, when a name
    was in fact deliberately removed."""
    assert 'Cleared' in APP_JS, "clearing the name reports the same words as setting one"


# ── 3: the label tells the truth about who sees it ─────────────────────────

def test_the_label_does_not_claim_the_name_is_only_for_rickie():
    """It is also what teammates see. Labelling it "Rickie calls you" is why
    nobody set one, which is why every roster row read "Member N"."""
    assert 'Your StreakFit name' in INDEX, "the field still presents as Rickie-only"
    help_text = re.search(r'id="display-name-help"[^>]*>(.*?)</p>', INDEX, re.S)
    assert help_text, 'no help text for the name field'
    assert 'teammate' in help_text.group(1).lower(), (
        "the help text does not tell the user teammates can see this name")


# ── 4: account state does not survive a session ending ─────────────────────

ACCOUNT_SCOPED = [
    'currentUser',          # identity
    'blockedUserIds',       # PRIVATE moderation record
    'isGuest',
    'guestCompleted',
    'guestCompleteFired',
    '_pendingJoinCode',
    '_cachedGreetingLine',
    '_cachedDoneLine',
    '_teamPanelIsCreator',
    '_teamPanelMembers',
    '_teamThreadPainted',
    '_photoFilters',
]


def _reset_body():
    m = re.search(r'function _resetAccountState\(\)\s*\{(.*?)\n\}', APP_JS, re.S)
    assert m, 'there is no _resetAccountState()'
    return m.group(1)


@pytest.mark.parametrize('name', ACCOUNT_SCOPED)
def test_every_account_scoped_global_is_cleared(name):
    """Asserts the RESET is complete, not that particular names exist. A new
    account-scoped global with no reset fails here rather than shipping."""
    assert re.search(rf'\b{re.escape(name)}\s*=', _reset_body()), (
        f"{name} survives a session ending; it is account-scoped state")


def test_the_private_block_list_is_cleared():
    """Called out on its own because it is the one that is more than cosmetic:
    it is who YOU blocked, and it renders on every roster row."""
    assert re.search(r'\bblockedUserIds\s*=\s*\[\]', _reset_body()), (
        "blockedUserIds is not reset — one account's blocking decisions would "
        "render inside another account's session")


def test_logout_resets_account_state():
    m = re.search(r'function handleLogout\(\)\s*\{(.*?)\n\}', APP_JS, re.S)
    assert m, 'handleLogout not found'
    assert '_resetAccountState()' in m.group(1)


def test_an_expired_session_also_resets_account_state():
    """The involuntary path, and the more common one. An expiry happens to
    everybody; pressing Log out is a choice."""
    m = re.search(r'if \(res\.status === 401 && token\) \{(.*?)\n    \}', APP_JS, re.S)
    assert m, 'the 401 handler was not found'
    assert '_resetAccountState()' in m.group(1), (
        "a lapsed session leaves the previous account's state in memory")


# ── The roster says which row is you ───────────────────────────────────────

def test_the_roster_marks_your_own_row():
    assert '(You)' in APP_JS, "no row is marked as the reader's own"


def test_you_is_decided_client_side_and_never_becomes_a_server_field():
    """Which row is "you" differs for every reader of the same roster. A
    server field carrying it would mark the wrong person in any cached or
    shared response."""
    assert re.search(r'm\.user_id\s*===\s*currentUser\.id', APP_JS), (
        "the You marker is not derived from the reader's own id")
    server = (Path(appmod.__file__).resolve().parent / 'app.py').read_text(encoding='utf-8')
    assert '"is_self"' not in server and "'is_self'" not in server, (
        "the roster payload gained an is_self field; it must stay client-side")


# ── Shipping the fix at all ────────────────────────────────────────────────

def test_the_service_worker_cache_was_bumped():
    """app.js, index.html and style.css all changed, and all are precached.
    Without a bump a returning browser serves the old bundle and none of the
    above reaches the person."""
    assert "streakfit-v0818" not in SW, (
        "the service-worker cache version was not bumped, so cached clients "
        "keep the old bundle")


def test_the_save_button_is_a_comfortable_tap_target():
    m = re.search(r'\.display-name-save\s*\{(.*?)\}', STYLE, re.S)
    assert m, 'no styles for the Save button'
    assert 'min-height: 44px' in m.group(1), (
        "the Save button is smaller than the minimum comfortable tap target")
