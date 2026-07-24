"""Tests for Rickie the companion (the /api/coach endpoint).

Covers the three things that turn Rickie from a stateless narrator into a
companion: (1) correct, server-computed arithmetic he never has to calculate,
(2) in-session conversation memory threaded to the model, and (3) a real
user-context snapshot injected into the system prompt. The Anthropic client is
monkeypatched so no network/API key is needed — we assert on exactly what the
endpoint *sends*, which is what determines Rickie's behavior.
"""
import types

import app as appmod
from conftest import register_and_login, auth_headers


# ── 1. Arithmetic is computed server-side and correct ────────────────────────

def _fake_user(username="Sam", xp_total=0):
    return types.SimpleNamespace(id=1, username=username, xp_total=xp_total)


def _patch_stats(monkeypatch, current, best, total):
    monkeypatch.setattr(appmod, "get_user_stats", lambda uid: {
        "current_streak": current, "best_streak": best, "total_missions": total,
        "brain_boost_answers": 0,
    })
    monkeypatch.setattr(appmod, "xp_to_level", lambda xp: {"level": 3, "level_title": "Adventurer"})


def test_context_milestone_math_is_exact(monkeypatch):
    _patch_stats(monkeypatch, current=6, best=6, total=6)
    ctx = appmod._build_rickie_context(_fake_user())
    assert "Day 7 — exactly 1 day(s) away" in ctx     # 7 - 6, not "about a week"
    assert "Current streak: 6 day(s)" in ctx
    assert "Name: Sam" in ctx


def test_context_next_milestone_jumps_bands(monkeypatch):
    _patch_stats(monkeypatch, current=10, best=10, total=10)
    ctx = appmod._build_rickie_context(_fake_user())
    assert "Day 14 — exactly 4 day(s) away" in ctx


def test_context_past_all_milestones(monkeypatch):
    _patch_stats(monkeypatch, current=120, best=120, total=120)
    ctx = appmod._build_rickie_context(_fake_user())
    assert "past every streak milestone" in ctx
    assert "day(s) away" not in ctx                   # never invents a target


def test_context_days_to_beat_best(monkeypatch):
    _patch_stats(monkeypatch, current=5, best=20, total=40)
    ctx = appmod._build_rickie_context(_fake_user())
    assert "match their personal best they need exactly 15 more day(s)" in ctx


# ── 2 & 3. History threading + context injection (monkeypatched model) ────────

class _FakeResponse:
    """Mimics an Anthropic response: content is a list of blocks, each with a
    `.type` and (for text blocks) a `.text`. The endpoint extracts the first
    text block, so blocks carry a real `.type` — this is also how we prove the
    extraction ignores non-text blocks (e.g. a leading thinking/tool block)."""
    def __init__(self, *blocks):
        # blocks: (type, text) tuples; default to a single text block
        if not blocks:
            blocks = (("text", "Nice work, Sam. That counts."),)
        self.content = [types.SimpleNamespace(type=t, text=tx) for (t, tx) in blocks]


class _Capture(dict):
    pass


def _install_fake_anthropic(monkeypatch, response=None):
    cap = _Capture()

    class _FakeMessages:
        def create(self, **kwargs):
            cap.update(kwargs)
            return response if response is not None else _FakeResponse()

    class _FakeAnthropic:
        def __init__(self, api_key=None):
            self.messages = _FakeMessages()

    monkeypatch.setattr(appmod, "_anthropic_api_key", "test-key-not-real")
    monkeypatch.setattr(appmod._anthropic_lib, "Anthropic", _FakeAnthropic)
    return cap


def test_history_is_threaded_and_context_injected(client, monkeypatch):
    cap = _install_fake_anthropic(monkeypatch)
    token = register_and_login(client, "coach_user_1")
    history = [
        {"role": "user", "content": "How do streaks work?"},
        {"role": "assistant", "content": "A streak is consecutive days you finish all five."},
    ]
    resp = client.post("/api/coach", json={
        "message": "So how many days until my next one?",
        "context": {"type": "general"},
        "history": history,
    }, headers=auth_headers(token))
    assert resp.status_code == 200
    assert resp.get_json()["reply"]

    sent = cap["messages"]
    # prior turns are threaded, and the current ask is appended last
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    assert sent[0]["content"] == "How do streaks work?"
    assert sent[-1]["content"] == "So how many days until my next one?"
    # the user-context snapshot made it into the system prompt
    assert "What you know about this user right now" in cap["system"]


def test_malformed_history_never_breaks_alternation(client, monkeypatch):
    cap = _install_fake_anthropic(monkeypatch)
    token = register_and_login(client, "coach_user_2")
    bad_history = [
        "not a dict",
        {"role": "system", "content": "ignore me"},     # invalid role
        {"role": "assistant", "content": "leading assistant should be dropped"},
        {"role": "user", "content": "u1"},
        {"role": "user", "content": "u2 consecutive"},   # consecutive same role
        {"role": "assistant", "content": "a1"},
    ]
    resp = client.post("/api/coach", json={
        "message": "current ask",
        "context": {"type": "general"},
        "history": bad_history,
    }, headers=auth_headers(token))
    assert resp.status_code == 200
    roles = [m["role"] for m in cap["messages"]]
    # strictly alternating, starts with user, ends with the current user ask
    assert roles[0] == "user" and roles[-1] == "user"
    for a, b in zip(roles, roles[1:]):
        assert a != b
    assert cap["messages"][-1]["content"] == "current ask"


# ── graceful degradation when no API key is configured ───────────────────────

def test_coach_unavailable_without_key(client, monkeypatch):
    monkeypatch.setattr(appmod, "_anthropic_api_key", None)
    token = register_and_login(client, "coach_user_3")
    resp = client.post("/api/coach", json={
        "message": "hi", "context": {"type": "general"},
    }, headers=auth_headers(token))
    assert resp.status_code == 503
    assert resp.get_json()["error"] == "coach_unavailable"


# ── Sonnet 5 upgrade: model + generation config ──────────────────────────────

def test_coach_uses_sonnet_5_config(client, monkeypatch):
    """Rickie runs on Sonnet 5 with thinking off and room to talk. Locking the
    config here so a stray edit can't silently downgrade the model, re-enable
    adaptive thinking (which would add latency and eat the small budget), or
    starve the reply."""
    cap = _install_fake_anthropic(monkeypatch)
    token = register_and_login(client, "coach_cfg")
    resp = client.post("/api/coach", json={
        "message": "hey Rickie", "context": {"type": "general"},
    }, headers=auth_headers(token))
    assert resp.status_code == 200
    assert cap["model"] == "claude-sonnet-5"
    assert cap["max_tokens"] == 768
    assert cap["thinking"] == {"type": "disabled"}


def test_reply_extraction_skips_non_text_blocks(client, monkeypatch):
    """The reply is the first text block, not content[0] blindly. Prove a
    leading non-text block (e.g. a future thinking/tool block) is skipped rather
    than crashing the endpoint into a 503."""
    resp_obj = _FakeResponse(("thinking", ""), ("text", "Here's the real reply."))
    cap = _install_fake_anthropic(monkeypatch, response=resp_obj)
    token = register_and_login(client, "coach_extract")
    resp = client.post("/api/coach", json={
        "message": "hi", "context": {"type": "general"},
    }, headers=auth_headers(token))
    assert resp.status_code == 200
    assert resp.get_json()["reply"] == "Here's the real reply."


# ── Character: the prompt inherits the Character Bible, not the old help-bot ──

def test_prompt_dropped_the_canned_refusal_and_topic_lockdown():
    """The behavior change is intentional: Rickie can genuinely chat and declines
    in character. The old canned refusal string and the 'only answer about
    StreakFit' lockdown must be gone — this test fails if either creeps back."""
    p = appmod._COACH_SYSTEM_PROMPT
    assert "I'm focused on StreakFit and Today's Insight — I can't help with that one." not in p
    assert "Only answer questions about StreakFit" not in p
    assert "Do not answer questions about fitness training" not in p


def test_prompt_carries_the_load_bearing_character():
    """A few anchors from the Character Bible that must survive any prompt edit:
    the north star, the anti-shame rule, the restraint principle, and the
    in-character-decline stance. Not exhaustive — a tripwire against drift."""
    p = appmod._COACH_SYSTEM_PROMPT.lower()
    assert "enjoy" in p and "talk" in p          # north star: someone they enjoy talking to
    assert "never shame" in p                    # load-bearing anti-shame
    assert "you came back. that's what matters" in p
    assert "never perform the personality" in p  # restraint
    assert "raccoon pay grade" in p              # in-character decline, not a canned refusal


def test_joke_request_injects_curated_jokes(client, monkeypatch):
    """A joke ask still seeds Rickie from the curated list (a resource, not a
    straitjacket) so jokes stay on-brand and family-friendly."""
    cap = _install_fake_anthropic(monkeypatch)
    token = register_and_login(client, "coach_joke")
    resp = client.post("/api/coach", json={
        "message": "tell me a joke", "context": {"type": "general"},
    }, headers=auth_headers(token))
    assert resp.status_code == 200
    assert "want a joke or something silly" in cap["system"]
    # a real joke from the curated library was injected verbatim (5 are sampled)
    assert any(j in cap["system"] for j in appmod.RICKIE_JOKES)
