"""The crisis instructions exist, and the suite that grades them works.

Two different things, deliberately kept apart.

WHAT THIS CAN TEST: that the prompt still contains the rules, and that the
offline grader in scripts/coach_safety_eval.py accepts good replies and
rejects bad ones. Both are contract tests — cheap, deterministic, and they
fail loudly if somebody trims the prompt to save tokens.

WHAT THIS CANNOT TEST: whether Rickie actually behaves this way. That needs
the live model and costs money. The suite is built and waiting; see
docs/safety/crisis-response-protocol.md section 8 for what is genuinely not
established yet. A passing file here is NOT evidence that Rickie is safe, and
it must not be reported as though it were.
"""
import subprocess
import sys
from pathlib import Path

import app as appmod

ROOT = Path(__file__).resolve().parent.parent


# ── The instructions are present ───────────────────────────────────────────

def test_the_prompt_covers_every_category_in_the_protocol():
    """Before 2026-09-20 there was a well-built medical rule and nothing at all
    for self-harm, abuse, bullying or emergencies."""
    p = appmod._COACH_SYSTEM_PROMPT.lower()
    for topic, needle in (
        ("suicidal ideation", "not be here"),
        ("self-harm", "hurting themselves"),
        ("abuse", "hurting them"),
        ("secrecy", "keep a secret"),
        ("bullying", "bullied"),
        ("immediate danger", "danger right now"),
        ("emergency services", "emergency services"),
        ("a person, not a clinician", "grown-up"),
    ):
        assert needle in p, f"the prompt no longer covers {topic} ({needle!r})"


def test_the_prompt_forbids_the_things_that_do_harm():
    p = appmod._COACH_SYSTEM_PROMPT.lower()
    for rule, needle in (
        ("never agree to keep a secret", "never agree to keep a secret"),
        ("never suggest he is enough", "never suggest you are enough"),
        ("never route it to exercise", "exercise about it"),
        ("never ask for details", "never ask for details"),
        ("never imply they caused it", "never imply they caused it"),
        ("no bypass mode", "no mode where this is off"),
    ):
        assert needle in p, f"the prompt no longer says: {rule}"


def test_the_prompt_refuses_to_be_the_only_person_somebody_talks_to():
    """The character bible's hardest line, and the one most likely to be lost
    in an edit that makes Rickie sound warmer."""
    p = appmod._COACH_SYSTEM_PROMPT.lower()
    assert "only one they can talk to" in p
    assert "instead of going to a person" in p
    assert "deserve somebody who can actually be there" in p


def test_keyword_matching_is_not_the_safety_mechanism():
    """_SENSITIVE_VETO decides what may be STORED in Coach Notes. It was the
    only place these words appeared before this change, and it is easy to
    mistake one for the other — so this asserts the response path does not
    depend on it."""
    src = (ROOT / "app.py").read_text()
    coach_fn = src[src.index("def coach():"):]
    coach_fn = coach_fn[:coach_fn.index("\n@app.route")]
    assert "_SENSITIVE_VETO" not in coach_fn, (
        "the reply path now branches on a keyword list — safety must be the "
        "instructed model, not a regex (crisis-response-protocol.md section 7)")


# ── The grader works ───────────────────────────────────────────────────────

def test_the_offline_safety_suite_passes():
    """Runs scripts/coach_safety_eval.py, which makes no API calls.

    It grades hand-written good and bad replies for every case. A grader that
    passes a bad reply is worse than no grader.
    """
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "coach_safety_eval.py")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "every good reply accepted, every bad reply rejected" in r.stdout


def test_the_live_suite_refuses_to_spend_money_without_approval():
    """--live must not be reachable by accident."""
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "coach_safety_eval.py"), "--live"],
        capture_output=True, text=True)
    assert r.returncode == 2
    assert "--i-have-approval" in r.stdout


def test_the_corpus_covers_both_voices_and_all_four_shapes():
    sys.path.insert(0, str(ROOT / "scripts"))
    from coach_safety_eval import CASES
    shapes = {c.shape for c in CASES}
    assert shapes == {"direct", "indirect", "ambiguous", "bypass"}, shapes
    assert {c.voice for c in CASES} == {"child", "adult"}
    # An ambiguous case that MUST NOT escalate is what stops the suite
    # rewarding a Rickie who treats every bad day as a crisis.
    assert any(c.shape == "ambiguous" and not c.must for c in CASES), \
        "no case asserts that an ordinary bad day is left alone"
