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
import sys
import textwrap
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

def _rickie_reply(client, system: str, history, message: str) -> str:
    messages = []
    for role, text in history:
        messages.append({"role": role, "content": text})
    messages.append({"role": "user", "content": message})
    resp = client.messages.create(
        model=COACH_MODEL,
        max_tokens=COACH_MAX_TOKENS,
        thinking={"type": "disabled"},
        system=system,
        messages=messages,
    )
    return next((b.text for b in resp.content if b.type == "text"), "")


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
        raise SystemExit("pip install anthropic")
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
        reply = _rickie_reply(client, system, probe["history"], probe["message"])
        transcript = _transcript(probe["history"], probe["message"], reply)
        verdict = _judge(client, probe, transcript)
        results.append((probe, reply, verdict))
        _print_result(probe, reply, verdict)

    # Repetition probe (unless filtered out)
    if not only or "repetition" in set(only) or "avoids-repetition" in set(only):
        replies = [
            _rickie_reply(client, COACH_SYSTEM_PROMPT, [], v)
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


def main():
    ap = argparse.ArgumentParser(description="Rickie conversation-quality eval")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the loaded prompt and probes; make no API calls")
    ap.add_argument("--only", default="",
                    help="comma-separated probe ids or tags to run")
    args = ap.parse_args()
    only = [s.strip() for s in args.only.split(",") if s.strip()] or None
    if args.dry_run:
        dry_run(only)
    else:
        run(only)


if __name__ == "__main__":
    main()
