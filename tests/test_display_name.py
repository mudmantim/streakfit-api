"""What Rickie is allowed to call somebody.

Rickie was handed `user.username` as "Name" and used it in conversation. A
username is a login credential, not a name: the September evaluation produced
"Be a little gentle with yourself right now, qa_coach_eval_1789836556_2", and a
user who registers with their email address would have had it read back aloud.

The rule proved here: an explicit display_name wins; a username is used only if
it already looks like something a person answers to; otherwise Rickie gets NO
name and is told to talk to them without one.
"""
import re
import pytest

import app as appmod
from app import User, db
from conftest import auth_headers, register_and_login


def _u(username, display_name=None):
    return User(username=username, display_name=display_name, password_hash="x")


# ── The unsafe shapes must never reach Rickie ───────────────────────────────

@pytest.mark.parametrize("username", [
    "olivia@example.com",            # the one that matters
    "tim.hill@gmail.com",
    "qa_coach_eval_1789836556_2",    # the real string from the evaluation
    "test_user_9",
    "user_20260919",                 # a date somebody did not mean to publish
    "admin_root",
    "https://evil.example.com",
    "a_very_long_handle_nobody_would_say_out_loud_really",
    "",
])
def test_unsafe_usernames_yield_no_name(app, username):
    assert appmod._safe_display_name(_u(username)) is None


@pytest.mark.parametrize("username", ["olivia", "tim", "Sam", "mudmantim", "kid2"])
def test_ordinary_usernames_are_fine(app, username):
    assert appmod._safe_display_name(_u(username)) == username


def test_display_name_beats_an_unsafe_username(app):
    u = _u("olivia@example.com", display_name="Olivia")
    assert appmod._safe_display_name(u) == "Olivia"


def test_display_name_beats_a_safe_username_too(app):
    assert appmod._safe_display_name(_u("mudmantim", "Tim")) == "Tim"


# ── The context block Rickie actually receives ──────────────────────────────

def test_context_omits_the_name_line_when_there_is_no_safe_name(app):
    u = _u("qa_coach_eval_1789836556_2")
    db.session.add(u)
    db.session.commit()
    block = appmod._build_rickie_context(u)
    assert "qa_coach_eval" not in block, "a login identifier reached Rickie's context"
    assert "do not know their name" in block


def test_context_includes_a_safe_name(app):
    u = _u("olivia")
    db.session.add(u)
    db.session.commit()
    assert "- Name: olivia" in appmod._build_rickie_context(u)


def test_an_email_username_never_appears_in_the_context(app):
    u = _u("olivia@example.com")
    db.session.add(u)
    db.session.commit()
    block = appmod._build_rickie_context(u)
    assert "@" not in block


# ── Validation on the way in ────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    "olivia@example.com", "www.example.com", "https://x.com", "born19870412",
    "x" * 41,
])
def test_rejected_display_names(bad):
    ok, _ = appmod._validate_display_name(bad)
    assert ok is False


@pytest.mark.parametrize("good", ["Olivia", "Tim H", "Grandma", "José"])
def test_accepted_display_names(good):
    ok, cleaned = appmod._validate_display_name(good)
    assert ok and cleaned == good


def test_blank_clears_it_rather_than_erroring():
    for blank in (None, "", "   "):
        ok, cleaned = appmod._validate_display_name(blank)
        assert ok and cleaned is None


# ── End to end through the API ──────────────────────────────────────────────

def test_patch_me_sets_and_clears_display_name(client):
    token = register_and_login(client, "olivia@example.com", "TestPass123!")
    r = client.patch("/api/me", json={"display_name": "Olivia"},
                     headers=auth_headers(token))
    assert r.status_code == 200
    assert r.get_json()["display_name"] == "Olivia"
    assert r.get_json()["rickie_calls_you"] == "Olivia"

    r = client.patch("/api/me", json={"display_name": ""}, headers=auth_headers(token))
    assert r.status_code == 200
    assert r.get_json()["display_name"] is None
    # Username is an email address, so falling back yields no name at all.
    assert r.get_json()["rickie_calls_you"] is None


def test_patch_me_rejects_an_email_as_a_display_name(client):
    token = register_and_login(client, "someone_ok", "TestPass123!")
    r = client.patch("/api/me", json={"display_name": "olivia@example.com"},
                     headers=auth_headers(token))
    assert r.status_code == 400
    assert "email" in r.get_json()["error"].lower()


# ── The words a person actually reads ───────────────────────────────────────
#
# Every one of these strings is rendered verbatim in the settings panel. They
# used to open with the JSON field name — "display_name can't be an email
# address", "Invalid skill_level. Must be one of: ..." — which is the API
# talking to a developer in front of whoever is holding the phone. The intended
# first user of this app is nine.

@pytest.mark.parametrize("payload", [
    {"display_name": "olivia@example.com"},
    {"display_name": "x" * 200},
    {"display_name": "visit streakfit.example.com"},
    {"display_name": 12345},
    {"skill_level": "custom"},
    {"display_mode": "neon"},
    {"rickie_mode": "loud"},
])
def test_validation_errors_are_written_for_a_person_not_a_developer(client, payload):
    token = register_and_login(client, "reader_" + str(abs(hash(str(payload))))[:6],
                               "TestPass123!")
    r = client.patch("/api/me", json=payload, headers=auth_headers(token))
    assert r.status_code == 400, payload
    message = r.get_json()["error"]

    # No raw field name, and no snake_case identifier of any kind.
    for field in ("display_name", "skill_level", "display_mode", "rickie_mode"):
        assert field not in message, f"{field!r} leaked into {message!r}"
    assert not re.search(r"\b[a-z]+_[a-z]+\b", message), \
        f"an identifier leaked into {message!r}"

    # A sentence: starts with a capital, ends with a stop.
    assert message[:1].isupper(), message
    assert message.rstrip().endswith((".", "!")), message
