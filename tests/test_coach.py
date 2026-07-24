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

def _tool_use_block(block_id, name, tool_input):
    return types.SimpleNamespace(type="tool_use", id=block_id, name=name, input=tool_input)


class _FakeResponse:
    """Mimics an Anthropic response: `content` is a list of blocks (each with a
    `.type`; text blocks add `.text`, tool_use blocks add `.name`/`.id`/`.input`)
    and a `.stop_reason`. The endpoint extracts the first text block and drives a
    tool loop off `stop_reason`, so both are modelled here."""
    def __init__(self, *blocks, stop_reason="end_turn"):
        # Each block is either a (type, text) tuple (text/thinking) or a prebuilt
        # namespace (e.g. a tool_use block). Default: one text block, end_turn.
        if not blocks:
            blocks = (("text", "Nice work, Sam. That counts."),)
        content = []
        for b in blocks:
            if isinstance(b, tuple):
                content.append(types.SimpleNamespace(type=b[0], text=b[1]))
            else:
                content.append(b)
        self.content = content
        self.stop_reason = stop_reason


class _Capture(dict):
    pass


def _install_fake_anthropic(monkeypatch, response=None, responses=None):
    """Patch the Anthropic client. `responses` (a list) is returned one-per-create
    for multi-call flows (e.g. the weather tool round-trip); otherwise `response`
    or a default end_turn text reply is returned every call. `cap` captures the
    last create() kwargs plus every call in cap['calls']."""
    cap = _Capture()
    cap["calls"] = []
    seq = list(responses) if responses is not None else None

    class _FakeMessages:
        def create(self, **kwargs):
            cap.update(kwargs)
            cap["calls"].append(kwargs)
            if seq:
                return seq.pop(0)
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


# ── Weather: the first and only tool ─────────────────────────────────────────

def test_weather_success(monkeypatch):
    """Geocode + forecast resolve to a short, in-character-ready string. No
    location is stored; the city is whatever was passed in."""
    def fake_get(url, timeout=6):
        if "geocoding-api" in url:
            return {"results": [{"name": "Denver", "admin1": "Colorado",
                                 "country": "United States",
                                 "latitude": 39.7, "longitude": -105.0}]}
        return {"current": {"temperature_2m": 71.6, "weather_code": 0}}
    monkeypatch.setattr(appmod, "_http_get_json", fake_get)
    content, is_err = appmod._weather_tool_result("Denver")
    assert is_err is False
    assert "Denver, Colorado, United States" in content
    assert "72°F" in content            # rounded from 71.6
    assert "clear skies" in content     # WMO code 0


def test_weather_unknown_city_is_error(monkeypatch):
    monkeypatch.setattr(appmod, "_http_get_json", lambda url, timeout=6: {"results": []})
    content, is_err = appmod._weather_tool_result("Xyzzyville")
    assert is_err is True
    assert "Xyzzyville" in content       # so Rickie can ask them to clarify


def test_weather_empty_city_is_error():
    content, is_err = appmod._weather_tool_result("   ")
    assert is_err is True
    assert "which city" in content.lower()


def test_weather_network_failure_is_error(monkeypatch):
    def boom(url, timeout=6):
        raise OSError("network down")
    monkeypatch.setattr(appmod, "_http_get_json", boom)
    content, is_err = appmod._weather_tool_result("Denver")
    assert is_err is True
    assert "couldn't reach the weather" in content.lower()


def test_coach_runs_weather_tool_then_replies(client, monkeypatch):
    """The bounded tool loop: model asks for get_weather, server runs it, feeds the
    result back, model replies. Assert the tool was offered, the city was passed
    through, and the final text reply comes back."""
    first = _FakeResponse(_tool_use_block("toolu_1", "get_weather", {"city": "Denver"}),
                          stop_reason="tool_use")
    second = _FakeResponse(("text", "Denver's sitting at 72 and clear — good day to move."))
    cap = _install_fake_anthropic(monkeypatch, responses=[first, second])

    seen = {}
    def fake_weather(city):
        seen["city"] = city
        return ("Denver, Colorado, United States: 72°F, clear skies.", False)
    monkeypatch.setattr(appmod, "_weather_tool_result", fake_weather)

    token = register_and_login(client, "coach_weather")
    resp = client.post("/api/coach", json={
        "message": "what's the weather in Denver?", "context": {"type": "general"},
    }, headers=auth_headers(token))
    assert resp.status_code == 200
    assert seen["city"] == "Denver"
    assert "Denver" in resp.get_json()["reply"]
    # the weather tool was offered to the model, and two calls were made (ask + reply)
    assert any(t.get("name") == "get_weather" for t in cap["tools"])
    assert len(cap["calls"]) == 2


def test_coach_offers_weather_tool_on_normal_turn(client, monkeypatch):
    """Even a plain chat turn passes the tool (the model just won't call it),
    and a single end_turn response returns without a tool round."""
    cap = _install_fake_anthropic(monkeypatch)
    token = register_and_login(client, "coach_notool")
    resp = client.post("/api/coach", json={
        "message": "hey Rickie", "context": {"type": "general"},
    }, headers=auth_headers(token))
    assert resp.status_code == 200
    assert any(t.get("name") == "get_weather" for t in cap["tools"])
    assert len(cap["calls"]) == 1
