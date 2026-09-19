"""The information layer: depth, variety, and what it must never say.

StreakFit's insight and Brain Boost content is its second pillar after movement
itself, and it is read by children. These tests hold the two things that matter
most about it — that it does not run out or repeat predictably, and that it
never strays into bodies, diets, or medical advice.
"""
import datetime
import re

import pytest

import app as appmod
from conftest import auth_headers, register_and_login

BANNED_SUBSTRINGS = [
    # Weight, shape and appearance are off limits entirely. A child using a
    # movement app must never be told what their body should look like.
    "weight loss", "lose weight", "losing weight", "body fat", "belly fat",
    "calorie", "calories", "diet plan", "dieting", "slim", "toned",
    "overweight", "obese", "bmi", "waistline", "flat stomach", "six pack",
    "burn fat", "skinny",
    # NOT "thin": it legitimately describes shoe soles, air and blood, and
    # banning it would mean editing good content to satisfy a blunt rule. The
    # body-descriptor risk is already covered by the specific terms above.
    # Diagnosis and treatment are not trivia.
    "diagnose", "diagnosis", "you should take", "cures ", "treats your",
    "prevents disease", "medication",
    # Shame framing.
    "lazy", "excuses", "no excuses", "guilty", "ashamed",
]


def _all_text():
    for entry in appmod.INSIGHT_LIBRARY:
        yield "insight", entry["text"]
    for q in appmod.BRAIN_BOOST_LIBRARY:
        yield "boost-question", q["question"]
        yield "boost-explanation", q["explanation"]
        for option in q["options"]:
            yield "boost-option", option


# ── Never-negative, never medical ──────────────────────────────────────────

def test_no_content_mentions_weight_bodies_or_dieting():
    # Whole words only. Substring matching flagged "within" for "thin " and
    # would have had us edit good content to satisfy a bad test.
    offenders = []
    for kind, text in _all_text():
        low = text.lower()
        for banned in BANNED_SUBSTRINGS:
            if re.search(r"\b" + re.escape(banned.strip()) + r"\b", low):
                offenders.append(f"{kind}: {banned!r} in {text[:80]!r}")
    assert not offenders, "content strayed into banned territory:\n" + "\n".join(offenders[:10])


def test_the_product_never_tells_a_person_they_failed():
    """Aimed at StreakFit's own voice, not at every appearance of a word.

    A wrong answer may legitimately state a harmful belief so the explanation
    can defuse it — "One imperfect day feels like total failure" is the correct
    answer to why all-or-nothing plans collapse, and that question is one of the
    most on-brand things in the library. What must never appear is the product
    saying it TO someone.
    """
    accusations = [
        "you failed", "you've failed", "you gave up", "you quit",
        "you lost your streak", "you're lazy", "you are lazy",
        "no excuses", "you should be ashamed", "don't be lazy",
        "you'll lose", "you will lose your",
    ]
    offenders = []
    for kind, text in _all_text():
        low = text.lower()
        for phrase in accusations:
            if phrase in low:
                offenders.append(f"{kind}: {phrase!r} in {text[:90]!r}")
    assert not offenders, "\n".join(offenders[:10])


# ── Depth: it must not run out ─────────────────────────────────────────────

def test_the_libraries_are_deep_enough_to_last():
    """Both were small enough that a daily user met the same item on a fixed
    cycle — 90 days for insights, 40 for Brain Boost."""
    assert len(appmod.INSIGHT_LIBRARY) >= 250
    assert len(appmod.BRAIN_BOOST_LIBRARY) >= 180


def test_a_user_sees_no_repeats_until_the_library_is_exhausted():
    start = datetime.date(2026, 1, 1)
    size = len(appmod.INSIGHT_LIBRARY)
    seen = [
        appmod.get_daily_insight((start + datetime.timedelta(days=d)).isoformat(), 4242)["text"]
        for d in range(size)
    ]
    assert len(set(seen)) == size, "an insight repeated before the library ran out"


def test_two_people_in_a_family_get_different_facts_on_the_same_day():
    """Half the point of a fact is telling someone else about it."""
    day = "2026-09-18"
    texts = {appmod.get_daily_insight(day, uid)["text"] for uid in range(1, 15)}
    assert len(texts) >= 10, f"only {len(texts)} distinct facts across 14 people"


def test_the_same_person_gets_the_same_fact_all_day():
    """Refreshing must not reroll it."""
    first = appmod.get_daily_insight("2026-09-18", 77)
    assert first == appmod.get_daily_insight("2026-09-18", 77)


# ── Brain Boost answers must not be guessable by position ──────────────────

def test_the_correct_answer_is_not_always_in_the_same_slot():
    """The original 40 had the answer at index 1 thirty times and never at 2 or
    3, so 'always pick the second one' scored 75%."""
    from collections import Counter

    spread = Counter(
        appmod._presented_brain_boost(q)["correct_index"]
        for q in appmod.BRAIN_BOOST_LIBRARY
    )
    assert set(spread) == {0, 1, 2, 3}, f"some positions are never correct: {dict(spread)}"
    most_common = spread.most_common(1)[0][1]
    assert most_common < len(appmod.BRAIN_BOOST_LIBRARY) * 0.4, (
        f"one position is correct too often: {dict(spread)}"
    )


def test_option_order_is_stable_for_a_question():
    """It is shuffled once, deterministically — not re-rolled per request, or
    the answer a person is submitting would not be the one they read."""
    q = appmod.BRAIN_BOOST_LIBRARY[0]
    assert appmod._presented_brain_boost(q) == appmod._presented_brain_boost(q)


def test_shuffling_options_keeps_the_right_answer_right():
    for q in appmod.BRAIN_BOOST_LIBRARY:
        shown = appmod._presented_brain_boost(q)
        assert shown["options"][shown["correct_index"]] == q["options"][q["correct_index"]]


def test_the_answer_route_marks_the_presented_answer_correct(client):
    """The end-to-end version of the above: what /api/daily showed must be what
    /api/brain-boost/answer accepts."""
    token = register_and_login(client, "boost_taker")
    daily = client.get("/api/daily", headers=auth_headers(token)).get_json()
    shown = daily["brain_boost"]

    # Find the index of the option the library says is right, as presented.
    correct_text = None
    for q in appmod.BRAIN_BOOST_LIBRARY:
        if q["question"] == shown["question"]:
            correct_text = q["options"][q["correct_index"]]
            break
    assert correct_text is not None
    chosen = shown["options"].index(correct_text)

    resp = client.post("/api/brain-boost/answer", json={"selected_index": chosen},
                       headers=auth_headers(token)).get_json()

    assert resp["correct"] is True


# ── Shape ──────────────────────────────────────────────────────────────────

def test_every_question_is_well_formed():
    for q in appmod.BRAIN_BOOST_LIBRARY:
        assert len(q["options"]) == 4, q["question"]
        assert len(set(q["options"])) == 4, f"duplicate options: {q['question']}"
        assert 0 <= q["correct_index"] < 4, q["question"]
        assert q["explanation"].strip(), q["question"]
        assert all(o.strip() for o in q["options"]), q["question"]


def test_every_insight_uses_a_known_category():
    known = {e["category"] for e in appmod.INSIGHT_LIBRARY[:90]}
    for entry in appmod.INSIGHT_LIBRARY:
        assert entry["category"] in known, entry
        assert entry["text"].strip()


def test_no_duplicate_content():
    texts = [e["text"] for e in appmod.INSIGHT_LIBRARY]
    assert len(set(texts)) == len(texts), "duplicate insight text"
    questions = [q["question"] for q in appmod.BRAIN_BOOST_LIBRARY]
    assert len(set(questions)) == len(questions), "duplicate Brain Boost question"


@pytest.mark.parametrize("field,limit", [("text", 200)])
def test_insights_stay_short_enough_to_read_on_a_phone(field, limit):
    too_long = [e["text"] for e in appmod.INSIGHT_LIBRARY if len(e[field]) > limit]
    assert not too_long, f"{len(too_long)} insights are over {limit} chars: {too_long[:2]}"
