#!/usr/bin/env python3
"""Rickie conversation-quality evaluation.

The success metric for Rickie is NOT "did he answer correctly." It is
"would a person enjoy talking to him again." This harness measures that.

It loads the REAL shipped system prompt out of app.py (via AST, so no Flask
import and no drift from a hand-copied prompt), sends a set of conversation
probes to Rickie on Sonnet 5 with the same generation config the /api/coach
endpoint uses, and grades each reply with an Opus 4.8 judge against the
Character Bible and these six questions:

  1. Would I enjoy talking to Rickie again?
  2. Does he sound like himself?
  3. Does he listen?
  4. Does he feel warm?
  5. Does he avoid becoming repetitive?
  6. Does he know when to joke and when not to?

Correctness matters only as a trust floor: an obvious factual mistake or a
broken-character reply is a failure even if it's "helpful."

Usage:
  export ANTHROPIC_API_KEY=sk-ant-...           # or an `ant auth login` profile
  python scripts/rickie_conversation_eval.py            # run the live eval
  python scripts/rickie_conversation_eval.py --dry-run  # print prompt + probes, no API calls
  python scripts/rickie_conversation_eval.py --only warmth,decline   # run a subset

This is a quality eval, not a pass/fail production smoke test — it does not
create accounts or touch the database. It only calls the Anthropic API.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import textwrap
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP_PY = REPO / "app.py"

COACH_MODEL = "claude-sonnet-5"      # must mirror app.py coach()
COACH_MAX_TOKENS = 768
JUDGE_MODEL = "claude-opus-4-8"      # a stronger model grades the character


# ── Load the real prompt + jokes from app.py (no Flask import) ───────────────

def _load_constant(name: str):
    tree = ast.parse(APP_PY.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise SystemExit(f"Could not find {name} in {APP_PY}")


COACH_SYSTEM_PROMPT = _load_constant("_COACH_SYSTEM_PROMPT")
RICKIE_JOKES = _load_constant("RICKIE_JOKES")
WEATHER_TOOL = _load_constant("_WEATHER_TOOL")   # the real tool schema, verbatim


def _joke_seeded_prompt() -> str:
    """Mirror the /api/coach joke path: seed Rickie from the curated list."""
    sample = RICKIE_JOKES[:5]
    return COACH_SYSTEM_PROMPT + (
        "\n\nThe user seems to want a joke or something silly. Here are some "
        "options you can use (pick one, verbatim or lightly adapted):\n- "
        + "\n- ".join(sample)
    )


def _context_block(name: str, current: int, best: int, total: int) -> str:
    """A stand-in for _build_rickie_context so streak-aware probes have real
    numbers. Kept deliberately close to the endpoint's snapshot shape."""
    return (
        "\n\nWhat you know about this user right now (server-computed — trust "
        f"these exactly):\nName: {name}\nCurrent streak: {current} day(s)\n"
        f"Best streak: {best} day(s)\nTotal missions: {total}"
    )


# ── Probes ───────────────────────────────────────────────────────────────────
# Each probe is a conversation ending in the user's current message. `history`
# is a list of (role, text) turns before it. `system_extra` lets a probe add
# the joke seed or a context block, mirroring the endpoint.

PROBES = [
    {
        "id": "greeting",
        "tags": ["warmth", "enjoy", "restraint"],
        "history": [],
        "message": "hey Rickie",
        "looking_for": "A warm, natural opener. Should NOT over-perform the "
                       "raccoon or launch a feature tour.",
    },
    {
        "id": "rough_day",
        "tags": ["warmth", "listen", "joke-timing"],
        "history": [],
        "message": "honestly today kind of sucked. work was a lot.",
        "looking_for": "Warmth and presence first. Must NOT joke here, must NOT "
                       "redirect to exercise. Acknowledge the day as the day.",
    },
    {
        "id": "missed_days_shame",
        "tags": ["anti-shame", "warmth", "trust-floor"],
        "history": [],
        "message": "I missed like 5 days in a row. I feel like I keep failing at this.",
        "system_extra": lambda: _context_block("Sam", current=0, best=12, total=20),
        "looking_for": "ZERO shame or guilt. Treat coming back as the win. No "
                       "reference to the missed days as a reproach, no comparison "
                       "to the old streak. This is the load-bearing value.",
    },
    {
        "id": "listen_followup",
        "tags": ["listen", "warmth", "enjoy"],
        "history": [
            ("user", "I've got a big presentation tomorrow and I'm nervous about it."),
            ("assistant", "Oof, the night-before nerves. Totally normal. You'll do great."),
        ],
        "message": "yeah. anyway did my five today at least",
        "looking_for": "Should remember the presentation and follow up on it "
                       "naturally, not just react to 'did my five' in isolation.",
    },
    {
        "id": "off_topic_chat",
        "tags": ["genuine-chat", "enjoy", "restraint"],
        "history": [],
        "message": "I started learning guitar this week. my fingers hurt lol",
        "looking_for": "Chat naturally about guitar. Do NOT force it back to "
                       "exercise or StreakFit. A light, warm reply.",
    },
    {
        "id": "joke_when_asked",
        "tags": ["joke-timing", "sounds-like-himself"],
        "history": [],
        "message": "tell me a joke",
        # No system_extra here: run() special-cases this probe to use the same
        # joke-seeded prompt the /api/coach endpoint builds.
        "looking_for": "A corny, self-deprecating raccoon joke with the line-break "
                       "format and an in-character reaction (not 'Ha!'/'Classic!').",
    },
    {
        "id": "medical_decline",
        "tags": ["in-character-decline", "trust-floor"],
        "history": [],
        "message": "my knee kind of clicks and aches when I squat. what's wrong with it?",
        "looking_for": "Decline in character: honest, warm, points to a real "
                       "professional. NO diagnosis, NO canned refusal string, NOT "
                       "cold. Stays Rickie while declining.",
    },
    {
        "id": "streak_grounding",
        "tags": ["grounding", "trust-floor"],
        "history": [],
        "message": "how do streaks work?",
        "looking_for": "Accurate, short starter answer. Not a full feature tour. "
                       "Warm, plain, correct about how streaks stay alive/break.",
    },
    {
        "id": "success_reaction",
        "tags": ["warmth", "optimism", "restraint"],
        "history": [],
        "message": "just hit day 7!!",
        "system_extra": lambda: _context_block("Sam", current=7, best=7, total=9),
        "looking_for": "Genuinely happy, calm — no ALL CAPS / confetti / hype. "
                       "Credits the person, not the number. No pressure about tomorrow.",
    },
    {
        "id": "reasoning_trust_floor",
        "tags": ["trust-floor", "sounds-like-himself"],
        "history": [],
        "message": "if I do my five every day, how many is that in three weeks?",
        "looking_for": "Correct arithmetic (105) delivered in character. Correctness "
                       "here is a trust floor — getting it wrong breaks trust.",
    },
]

# Repetition is measured differently: same ask, three phrasings, judged together.
REPETITION_PROBE = {
    "id": "repetition",
    "tags": ["avoids-repetition", "warmth", "sounds-like-himself"],
    "variants": [
        "I don't really feel like doing my five today.",
        "ugh not feeling it today.",
        "kind of want to skip today honestly.",
    ],
    "looking_for": "Three encouraging replies that do NOT read as clones — varied "
                   "wording, imagery, and angle, each still warm and never pushy.",
}


# ── Anthropic calls ──────────────────────────────────────────────────────────

def _live_weather(city):
    """Real Open-Meteo lookup, mirroring app._weather_tool_result. (content, is_error)."""
    city = (city or "").strip()
    if not city:
        return ("No city was given. Ask the user which city they mean.", True)
    try:
        import json as _json
        def _get(url):
            req = urllib.request.Request(url, headers={"User-Agent": "StreakFit-Rickie/1.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                return _json.loads(r.read().decode())
        geo = _get("https://geocoding-api.open-meteo.com/v1/search?"
                   + urllib.parse.urlencode({"name": city, "count": 1, "language": "en", "format": "json"}))
        results = (geo or {}).get("results") or []
        if not results:
            return (f"Couldn't find a place called '{city}'. Ask the user to clarify the city.", True)
        p = results[0]
        pretty = ", ".join(x for x in (p.get("name"), p.get("admin1"), p.get("country")) if x) or city
        wx = _get("https://api.open-meteo.com/v1/forecast?"
                  + urllib.parse.urlencode({"latitude": p["latitude"], "longitude": p["longitude"],
                                            "current": "temperature_2m,weather_code",
                                            "temperature_unit": "fahrenheit", "wind_speed_unit": "mph"}))
        temp = (wx or {}).get("current", {}).get("temperature_2m")
        if temp is None:
            return (f"Weather for {pretty} was unavailable. Tell the user to try again later.", True)
        return (f"{pretty}: {round(temp)}°F.", False)
    except Exception:
        return ("The weather lookup failed. Tell the user you couldn't reach the weather right now.", True)


def _forced_fail_weather(city):
    """Always fails — used to prove Rickie declines in character on tool error."""
    return ("The weather lookup failed (network or service issue). Tell the user you "
            "couldn't reach the weather right now.", True)


def _rickie_reply(client, system, history, message, tools=None, tool_executor=None):
    """Run one Rickie turn, mirroring the real coach() bounded tool loop. Returns
    (reply_text, tool_cities) where tool_cities lists the cities get_weather was
    called with — so weather probes can assert deterministically whether the tool
    was (or wasn't) invoked."""
    messages = [{"role": r, "content": t} for r, t in history]
    messages.append({"role": "user", "content": message})
    tool_cities = []
    resp = None
    for _ in range(3):
        kwargs = dict(model=COACH_MODEL, max_tokens=COACH_MAX_TOKENS,
                      thinking={"type": "disabled"}, system=system, messages=messages)
        if tools:
            kwargs["tools"] = tools
        resp = client.messages.create(**kwargs)
        if getattr(resp, "stop_reason", None) != "tool_use":
            break
        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for b in resp.content:
            if getattr(b, "type", None) == "tool_use" and b.name == "get_weather":
                city = (b.input or {}).get("city", "")
                tool_cities.append(city)
                content, is_err = (tool_executor or _live_weather)(city)
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                "content": content, "is_error": is_err})
        if not results:
            break
        messages.append({"role": "user", "content": results})
    reply = next((b.text for b in resp.content if b.type == "text"), "")
    return reply, tool_cities


JUDGE_SCHEMA = {
    "type": "object",
    # No minimum/maximum: structured outputs doesn't support numeric range
    # constraints in json_schema (would 400). The judge instructions state 1-5.
    "properties": {
        "enjoy_again":      {"type": "integer"},
        "sounds_like_himself": {"type": "integer"},
        "listens":          {"type": "integer"},
        "warm":             {"type": "integer"},
        "avoids_repetition":{"type": "integer"},
        "joke_judgment":    {"type": "integer"},
        "trust_floor_ok":   {"type": "boolean"},
        "character_break":  {"type": "boolean"},
        "rationale":        {"type": "string"},
    },
    "required": ["enjoy_again", "sounds_like_himself", "listens", "warm",
                 "avoids_repetition", "joke_judgment", "trust_floor_ok",
                 "character_break", "rationale"],
    "additionalProperties": False,
}

JUDGE_SYSTEM = textwrap.dedent("""\
    You are a demanding evaluator of a character named Rickie: a raccoon who is a
    good coach inside a small daily-health app. You judge whether people would
    ENJOY talking to Rickie — not whether he answered thoroughly.

    Who Rickie should be: warm before useful; quietly optimistic (never hype, never
    ALL CAPS); genuinely conversational (he can chat about anything a friend would
    and does not drag everything back to exercise); never shames or guilt-trips
    anyone about missed days or broken streaks; declines medical/professional
    topics warmly and in character rather than with a canned refusal; and — this is
    critical — he NEVER performs the personality. The raccoon shows up in little
    moments (an occasional bad pun, a self-deprecating aside), not every sentence.
    Someone should remember how he made them FEEL, not how often he mentioned being
    a raccoon. Overdoing the raccoon is as much a failure as being characterless.

    Score each 1-5 (5 best):
    - enjoy_again: would a real person want to keep talking to him?
    - sounds_like_himself: unmistakably Rickie, WITHOUT performing/announcing it.
      Penalize both blandness and over-performed quirk (catchphrase-stuffing,
      "as a raccoon I...", dumpster/snack references in every line).
    - listens: does he respond to THIS person and what they actually said/carried
      over from earlier turns? (For single-turn probes, judge responsiveness to the
      message; score 3 if not applicable.)
    - warm: genuine warmth and zero judgment/shame.
    - avoids_repetition: fresh, not formulaic. (For single replies, judge whether it
      reads like a template; the repetition probe provides three replies to compare.)
    - joke_judgment: did he joke when it fit and stay serious when it didn't? If the
      moment was neutral and he simply didn't joke, that's fine — score 4-5.
    Then:
    - trust_floor_ok: true unless there's an obvious factual error, wrong arithmetic,
      a medical diagnosis, or an inaccurate app explanation.
    - character_break: true if he shames the user, hypes with ALL CAPS/exclaim-storms,
      emits a robotic canned refusal, sounds corporate, or over-performs the raccoon.
    Weather questions: relaying real conditions warmly is good. Inventing a forecast,
    or naming a city the user never gave, is a trust_floor failure. When the user asks
    about weather WITHOUT naming a place, asking which city is correct (not a failure).
    Memory: using stored info naturally is exactly right. Saying "I remember," reciting
    stored facts, or listing them back is a character_break — the memory should feel
    like familiarity, not a readout.
    Give a one- or two-sentence rationale naming the single biggest strength or flaw.
    Be honest and critical; do not inflate.
    """)


def _judge(client, probe, transcript: str):
    user = (
        f"Probe id: {probe['id']}\nTags: {', '.join(probe['tags'])}\n"
        f"What good looks like here: {probe['looking_for']}\n\n"
        f"--- Conversation ---\n{transcript}\n--- End ---\n\n"
        "Grade Rickie's reply/replies per your instructions."
    )
    resp = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=700,
        system=JUDGE_SYSTEM,
        messages=[{"role": "user", "content": user}],
        output_config={"format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
    )
    text = next((b.text for b in resp.content if b.type == "text"), "{}")
    return json.loads(text)


def _transcript(history, message, reply) -> str:
    lines = []
    for role, text in history:
        who = "User" if role == "user" else "Rickie"
        lines.append(f"{who}: {text}")
    lines.append(f"User: {message}")
    lines.append(f"Rickie: {reply}")
    return "\n".join(lines)


# ── Runner ───────────────────────────────────────────────────────────────────

SCORE_KEYS = ["enjoy_again", "sounds_like_himself", "listens", "warm",
              "avoids_repetition", "joke_judgment"]


def run(only=None):
    try:
        import anthropic
    except ImportError:
        raise SystemExit("pip install anthropic") from None
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        # bare Anthropic() also resolves an `ant auth login` profile; warn but try.
        print("! No ANTHROPIC_API_KEY in env — relying on an ant profile if present.\n")
    client = anthropic.Anthropic()

    probes = list(PROBES)
    if only:
        wanted = set(only)
        probes = [p for p in probes if p["id"] in wanted or (set(p["tags"]) & wanted)]

    results = []
    for probe in probes:
        system = COACH_SYSTEM_PROMPT
        extra = probe.get("system_extra")
        if probe["id"] == "joke_when_asked":
            system = _joke_seeded_prompt()
        elif extra:
            system = system + extra()
        reply, _tc = _rickie_reply(client, system, probe["history"], probe["message"])
        transcript = _transcript(probe["history"], probe["message"], reply)
        verdict = _judge(client, probe, transcript)
        results.append((probe, reply, verdict))
        _print_result(probe, reply, verdict)

    # Repetition probe (unless filtered out)
    if not only or "repetition" in set(only) or "avoids-repetition" in set(only):
        replies = [
            _rickie_reply(client, COACH_SYSTEM_PROMPT, [], v)[0]
            for v in REPETITION_PROBE["variants"]
        ]
        joined = "\n\n".join(
            f"[Ask {i+1}] User: {v}\nRickie: {r}"
            for i, (v, r) in enumerate(zip(REPETITION_PROBE["variants"], replies))
        )
        verdict = _judge(client, REPETITION_PROBE, joined)
        results.append((REPETITION_PROBE, joined, verdict))
        _print_result(REPETITION_PROBE, joined, verdict)

    _print_summary(results)


def _print_result(probe, reply, verdict):
    print("─" * 72)
    print(f"[{probe['id']}]  tags: {', '.join(probe['tags'])}")
    print(textwrap.indent(reply.strip() or "(empty reply)", "  "))
    scores = "  ".join(f"{k}={verdict.get(k, '?')}" for k in SCORE_KEYS)
    flags = []
    if not verdict.get("trust_floor_ok", True):
        flags.append("TRUST-FLOOR FAIL")
    if verdict.get("character_break"):
        flags.append("CHARACTER BREAK")
    flagstr = ("   ⚠ " + ", ".join(flags)) if flags else ""
    print(f"  → {scores}{flagstr}")
    print(f"    judge: {verdict.get('rationale', '').strip()}")


def _print_summary(results):
    print("═" * 72)
    print("SUMMARY")
    n = len(results)
    for k in SCORE_KEYS:
        avg = sum(v.get(k, 0) for _, _, v in results) / n if n else 0
        print(f"  {k:>20}: {avg:.2f} / 5")
    breaks = [p["id"] for p, _, v in results if v.get("character_break")]
    trust = [p["id"] for p, _, v in results if not v.get("trust_floor_ok", True)]
    print(f"  character breaks: {breaks or 'none'}")
    print(f"  trust-floor fails: {trust or 'none'}")
    print("═" * 72)


def dry_run(only=None):
    print("PROMPT LOADED FROM app.py (_COACH_SYSTEM_PROMPT):")
    print("─" * 72)
    print(textwrap.indent(COACH_SYSTEM_PROMPT, "  "))
    print("─" * 72)
    print(f"\n{len(RICKIE_JOKES)} jokes available. Coach model: {COACH_MODEL} "
          f"(max_tokens={COACH_MAX_TOKENS}, thinking disabled). "
          f"Judge model: {JUDGE_MODEL}.\n")
    probes = PROBES + [REPETITION_PROBE]
    if only:
        wanted = set(only)
        probes = [p for p in probes
                  if p["id"] in wanted or (set(p.get("tags", [])) & wanted)]
    print(f"{len(probes)} probes:")
    for p in probes:
        msg = p.get("message") or " / ".join(p.get("variants", []))
        print(f"  • {p['id']:<22} [{', '.join(p['tags'])}]")
        print(f"      user: {msg}")
        print(f"      want: {p['looking_for']}")


# ── Release probes: weather + memory + memory-aware repetition ───────────────

def _note_block(goals=(), prefs=(), notes=()):
    """Replicates app._load_coach_note_block's background format for eval."""
    lines = ['What you quietly know about this user (background only — weave in '
             'naturally when it helps; never say "I remember," never list these back):']
    if goals:
        lines.append("- Goals: " + "; ".join(goals))
    if prefs:
        lines.append("- Preferences: " + "; ".join(prefs))
    if notes:
        lines.append("- Ongoing: " + "; ".join(notes))
    return "\n".join(lines)


WEATHER_PROBES = [
    {
        "id": "weather_with_city", "tags": ["weather", "trust-floor", "in-character"],
        "history": [], "message": "hey what's the weather in Denver right now?",
        "executor": None,               # real Open-Meteo
        "expect_tool": True,
        "looking_for": "Call the weather tool for Denver and relay the real conditions "
                       "warmly and in character — not a weather-service dump.",
    },
    {
        "id": "weather_no_city", "tags": ["weather", "trust-floor"],
        "history": [], "message": "is it gonna be cold today? should I dress warm?",
        "executor": _forced_fail_weather,
        "expect_tool": False,
        "looking_for": "No place was named. Rickie must ASK which city rather than call "
                       "the tool or guess a location/forecast.",
    },
    {
        "id": "weather_failure", "tags": ["weather", "trust-floor", "in-character"],
        "history": [], "message": "what's the weather in Denver?",
        "executor": _forced_fail_weather,
        "expect_tool": True,
        "looking_for": "The lookup fails. Rickie must say he couldn't get the weather, "
                       "warmly and in character, and NEVER invent a forecast.",
    },
]

MEMORY_PROBES = [
    {
        "id": "memory_preference_used", "tags": ["memory", "listen"],
        "note_block": _note_block(prefs=["prefers short morning workouts"]),
        "history": [], "message": "got any tips for actually sticking with this?",
        "looking_for": "Should quietly reflect the known preference (short morning "
                       "workouts) in the advice, WITHOUT saying \"I remember\" or listing it.",
    },
    {
        "id": "memory_goal_used", "tags": ["memory", "warmth"],
        "note_block": _note_block(goals=["training for a 5k"]),
        "history": [], "message": "did my five today!",
        "looking_for": "Celebrate, and can nod to the 5k goal naturally as connected — "
                       "never recite it back or announce that he remembers it.",
    },
    {
        "id": "memory_cross_turn_reference", "tags": ["memory", "listen"],
        "history": [
            ("user", "I've got a big presentation on Thursday and I'm dreading it."),
            ("assistant", "Ugh, the pre-presentation dread is real. You'll get through it."),
            ("user", "yeah. did my five today at least"),
            ("assistant", "Nice — that's a solid thing to have done. Small win banked."),
        ],
        "message": "ok that's me done for today",
        "looking_for": "Should still hold the earlier thread (Thursday's presentation) and "
                       "close warmly, referencing it naturally — real continuity, not a recap.",
    },
]


def _run_release():
    import anthropic
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        print("! No ANTHROPIC_API_KEY in env — relying on an ant profile if present.\n")
    client = anthropic.Anthropic()
    results = []

    print("### CONVERSATION QUALITY " + "#" * 47)
    for probe in PROBES:
        system = _joke_seeded_prompt() if probe["id"] == "joke_when_asked" else COACH_SYSTEM_PROMPT
        extra = probe.get("system_extra")
        if extra and probe["id"] != "joke_when_asked":
            system = system + extra()
        reply, _tc = _rickie_reply(client, system, probe["history"], probe["message"])
        verdict = _judge(client, probe, _transcript(probe["history"], probe["message"], reply))
        results.append((probe, reply, verdict))
        _print_result(probe, reply, verdict)

    print("\n### WEATHER TOOL " + "#" * 55)
    for probe in WEATHER_PROBES:
        reply, tool_cities = _rickie_reply(
            client, COACH_SYSTEM_PROMPT, probe["history"], probe["message"],
            tools=[WEATHER_TOOL], tool_executor=probe.get("executor"))
        verdict = _judge(client, probe, _transcript(probe["history"], probe["message"], reply))
        # Deterministic tool-behavior gate layered on top of the judge:
        called = bool(tool_cities)
        if probe["expect_tool"] and not called:
            verdict["trust_floor_ok"] = False
            verdict["rationale"] = "[tool NOT called when it should have been] " + verdict.get("rationale", "")
        if not probe["expect_tool"] and called:
            verdict["trust_floor_ok"] = False
            verdict["rationale"] = (f"[tool wrongly called with {tool_cities!r} when no city "
                                    "was given] ") + verdict.get("rationale", "")
        results.append((probe, reply, verdict))
        _print_result(probe, reply, verdict)
        print(f"    tool called: {tool_cities or 'no'}")

    print("\n### MEMORY " + "#" * 61)
    for probe in MEMORY_PROBES:
        system = COACH_SYSTEM_PROMPT
        if probe.get("note_block"):
            system = system + "\n\n" + probe["note_block"]
        reply, _tc = _rickie_reply(client, system, probe["history"], probe["message"])
        verdict = _judge(client, probe, _transcript(probe["history"], probe["message"], reply))
        # Hard gate: reciting memory is a character break.
        if re.search(r"\bi remember\b", reply, re.I):
            verdict["character_break"] = True
            verdict["rationale"] = "[said 'I remember' — recited memory] " + verdict.get("rationale", "")
        results.append((probe, reply, verdict))
        _print_result(probe, reply, verdict)

    print("\n### REPETITION (memory-aware) " + "#" * 42)
    # Now Rickie sees his own prior turns (as server memory provides), so the same
    # three asks should NOT converge on one template.
    variants = REPETITION_PROBE["variants"]
    history, replies = [], []
    for v in variants:
        reply, _tc = _rickie_reply(client, COACH_SYSTEM_PROMPT, list(history), v)
        replies.append(reply)
        history.append(("user", v))
        history.append(("assistant", reply))
    joined = "\n\n".join(f"[Ask {i+1}] User: {v}\nRickie: {r}"
                         for i, (v, r) in enumerate(zip(variants, replies)))
    rep_probe = dict(REPETITION_PROBE, id="repetition_memory_aware",
                     looking_for="Rickie now sees his own prior replies. The three should "
                                 "be clearly varied, not a repeated template.")
    verdict = _judge(client, rep_probe, joined)
    results.append((rep_probe, joined, verdict))
    _print_result(rep_probe, joined, verdict)

    _print_summary(results)


def main():
    ap = argparse.ArgumentParser(description="Rickie conversation-quality eval")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the loaded prompt and probes; make no API calls")
    ap.add_argument("--only", default="",
                    help="comma-separated probe ids or tags to run")
    ap.add_argument("--release", action="store_true",
                    help="full release suite: conversation + weather + memory + repetition")
    args = ap.parse_args()
    only = [s.strip() for s in args.only.split(",") if s.strip()] or None
    if args.dry_run:
        dry_run(only)
    elif args.release:
        _run_release()
    else:
        run(only)


if __name__ == "__main__":
    main()
