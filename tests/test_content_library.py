"""The information layer: depth, variety, and what it must never say.

StreakFit's insight and Brain Boost content is its second pillar after movement
itself, and it is read by children. These tests hold the two things that matter
most about it — that it does not run out or repeat predictably, and that it
never strays into bodies, diets, or medical advice.
"""
import datetime
import re


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
    # The joke pool was never scanned. Sixty strings that Rickie says out loud,
    # exempt from every vocabulary and tone rule in this file because nobody had
    # added them to the generator — found by the content-store validator, which
    # reads the whole library rather than the two lists somebody remembered.
    for joke in appmod.RICKIE_JOKES:
        yield "joke", joke


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
    3, so 'always pick the second one' scored 75%.

    Measured on the STORED order, which is now what gets served. A runtime
    shuffle used to sit in between and has been removed: with the store
    scattered too, it was applying its permutation a second time, and squaring
    a permutation of four elements is not uniform — the answer came back to
    where it started far too often. 27% at the most common position became 51%.
    """
    from collections import Counter

    spread = Counter(q["correct_index"] for q in appmod.BRAIN_BOOST_LIBRARY)
    assert set(spread) == {0, 1, 2, 3}, f"some positions are never correct: {dict(spread)}"
    most_common = spread.most_common(1)[0][1]
    assert most_common < len(appmod.BRAIN_BOOST_LIBRARY) * 0.4, (
        f"one position is correct too often: {dict(spread)}"
    )


def test_the_question_served_is_the_question_stored():
    """Nothing reorders options between the store and the reader any more, so
    the answer somebody submits is the one they read. This used to need a
    stability test because a shuffle sat in the middle."""
    for date_str in ("2026-01-01", "2026-06-15"):
        shown = appmod.get_daily_brain_boost(date_str, user_id=7)
        stored = next(q for q in appmod.BRAIN_BOOST_LIBRARY
                      if q["question"] == shown["question"])
        assert shown["options"] == stored["options"]
        assert shown["correct_index"] == stored["correct_index"]
        assert shown is not stored, "served a reference to the library itself"


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


def test_every_discovery_has_a_category_and_a_body():
    """The category set used to be "whatever the first 90 items said", which
    stopped meaning anything once the library gained riddles and experiments
    with categories of their own. What matters is that every item declares one
    and that nothing is empty."""
    for entry in appmod.INSIGHT_LIBRARY:
        assert entry["category"].strip(), entry
        assert entry["text"].strip(), entry
        assert entry["type"] in ("fact", "movement", "riddle", "experiment", "rickie"), entry


def test_no_duplicate_content():
    texts = [e["text"] for e in appmod.INSIGHT_LIBRARY]
    assert len(set(texts)) == len(texts), "duplicate insight text"
    questions = [q["question"] for q in appmod.BRAIN_BOOST_LIBRARY]
    assert len(set(questions)) == len(questions), "duplicate Brain Boost question"


# Two shapes, two limits. A fact is one line on a card and 200 characters is
# generous for it. A mini-experiment is a set of instructions, and the three
# that went over were the three that gained a safety precaution — "stand near a
# wall", "stop well before anything pulls". Trimming those back to fit a limit
# written for one-liners would be letting a number edit a safety note.
_LENGTH_LIMITS = {"fact": 200, "movement": 200, "rickie": 200,
                  "riddle": 240, "experiment": 340}


def test_a_discovery_stays_short_enough_to_read_on_a_phone():
    too_long = [
        (e["type"], len(e["text"]), e["text"][:60])
        for e in appmod.INSIGHT_LIBRARY
        if len(e["text"]) > _LENGTH_LIMITS.get(e["type"], 200)
    ]
    assert not too_long, f"{len(too_long)} over their limit: {too_long[:3]}"


def test_an_experiment_that_needs_balance_says_what_to_hold():
    """Safety, as a property of the content rather than a hope about it.

    Anything asking a reader to stand on one foot, hop, or shift their weight
    has to name something to hold or offer a way to do it sitting down. The app
    does not know who is reading — an eleven-year-old, a grandparent, somebody
    with a knee that decides these things — and "stand on one foot" with no
    qualifier assumes it does.
    """
    needs_balance = re.compile(r"\b(stand on one foot|one foot|hop|hopping|"
                               r"shift your weight|eyes closed)\b", re.I)
    safe_wording = re.compile(r"\b(near a wall|worktop|hold|holding|something to "
                              r"hold|sitting|seated|chair|if you would rather|"
                              r"only as far as)\b", re.I)
    offenders = [
        e["text"][:70] for e in appmod.INSIGHT_LIBRARY
        if e["type"] == "experiment" and needs_balance.search(e["text"])
        and not safe_wording.search(e["text"])
    ]
    assert not offenders, "balance task with nothing to hold:\n" + "\n".join(offenders)


def test_no_experiment_asks_anyone_to_hold_their_breath():
    for e in appmod.INSIGHT_LIBRARY:
        if e["type"] != "experiment":
            continue
        assert not re.search(r"hold\w* (your |the )?breath|breath.?hold", e["text"], re.I), e["text"]


# ── Guessability: the answer must not be findable without knowing anything ──
#
# These exist because the position test found only half the problem. "Always
# pick the second option" used to score 75%; so did "always pick the longest",
# and nothing was watching. They are guards, not a definition of good content:
# a library could pass every one of them and still be dull or wrong. What they
# catch is the specific failure of writing a real explanation as the answer and
# three dismissals as the distractors, which is the shape the library drifted
# into twice.

def _correct(q):
    return q["options"][q["correct_index"]]


def _distractors(q):
    return [o for i, o in enumerate(q["options"]) if i != q["correct_index"]]


def test_the_correct_answer_is_not_usually_the_longest_option():
    """It was the longest in 143 of 190 questions, so 'pick the longest' scored
    75% with no knowledge at all."""
    longest = [q["question"] for q in appmod.BRAIN_BOOST_LIBRARY
               if len(_correct(q)) == max(len(o) for o in q["options"])]
    share = len(longest) / len(appmod.BRAIN_BOOST_LIBRARY)
    assert share < 0.45, (
        f"the correct option is the longest in {share:.0%} of questions "
        f"(chance is 25%); e.g. {longest[:3]}"
    )


def test_no_question_gives_the_answer_away_by_length_alone():
    """The stricter half: a visibly longer option is a tell a reader can use on
    a phone, where a few characters' difference is invisible but a whole extra
    clause is not."""
    obvious = []
    for q in appmod.BRAIN_BOOST_LIBRARY:
        longest_wrong = max(len(o) for o in _distractors(q))
        if len(_correct(q)) - longest_wrong > 12:
            obvious.append(f"{q['question']} (+{len(_correct(q)) - longest_wrong})")
    assert not obvious, "correct option stands out by length:\n" + "\n".join(obvious[:5])


def test_the_correct_answer_is_not_systematically_the_shortest_either():
    """The obvious overcorrection: pad every distractor and the tell inverts."""
    shortest = [q for q in appmod.BRAIN_BOOST_LIBRARY
                if len(_correct(q)) == min(len(o) for o in q["options"])]
    share = len(shortest) / len(appmod.BRAIN_BOOST_LIBRARY)
    assert share < 0.45, f"the correct option is the shortest in {share:.0%} of questions"


def test_no_distractor_is_a_bare_dismissal():
    """'Nothing at all', 'No effect', 'Never' are never the answer, and a
    regular player learns to eliminate them on sight — which is three options
    reduced to two."""
    bare = re.compile(
        r"^(nothing( at all| measurable)?|no effect|none|never|no real benefit"
        r"|it doesn'?t matter|it'?s a myth|not at all)\.?$", re.I)
    offenders = [f"{q['question']} -> {o!r}"
                 for q in appmod.BRAIN_BOOST_LIBRARY
                 for o in _distractors(q) if bare.match(o.strip())]
    assert not offenders, "\n".join(offenders[:8])


def test_no_option_instructs_an_action_a_child_could_be_hurt_copying():
    """A wrong answer may state a harmful BELIEF so the explanation can take it
    apart. It may not read as an instruction to do something dangerous, which
    is different: 'Hold your breath as long as possible' and 'Close both eyes
    while walking fast' were both sitting in a list of things to try at home.

    Where a genuinely dangerous idea does appear as an option, the explanation
    has to name it and say why — a distractor nobody corrects is just a bad
    idea printed in an app.
    """
    dangerous = re.compile(
        r"hold\w* (your |the )?breath|breath.?hold\w*"
        r"|eyes (closed|shut) while (walking|running)"
        r"|skip(ping)? (a |your |my )?(next )?meals?|stop(ping)? eating", re.I)
    offenders = []
    for q in appmod.BRAIN_BOOST_LIBRARY:
        for o in _distractors(q):
            if dangerous.search(o) and not dangerous.search(q["explanation"]):
                offenders.append(f"{q['question']} -> {o!r} (explanation never addresses it)")
    assert not offenders, "\n".join(offenders[:5])


def test_the_library_speaks_one_dialect_and_one_set_of_units():
    """The 180-entry expansion arrived in British English with metric units and
    the original 90 was American with imperial, so the same library said both
    'color' and 'colour' and gave one fact in miles and again in kilometers."""
    british = re.compile(
        r"\b(colour\w*|centre|fibre\w*|practis\w*|recognis\w*|stabilis\w*|favourite"
        r"|behaviour\w*|realis\w*|neighbour\w*|apologis\w*|metres?|kilometres?"
        r"|litres?|grey|kerbs?)\b", re.I)
    offenders = [f"{kind}: {m.group(0)!r} in {text[:60]!r}"
                 for kind, text in _all_text() for m in british.finditer(text)]
    assert not offenders, "mixed dialect:\n" + "\n".join(offenders[:8])


def test_every_question_asks_something_rather_than_asserting_it():
    """'True or false: it's okay to ask for help when you're struggling' was a
    question in name only. A yes/no framing is fine when the answer is a fact
    people get wrong; it is not fine when the answer is simply the kind one."""
    for q in appmod.BRAIN_BOOST_LIBRARY:
        assert q["question"].strip().endswith("?"), q["question"]
        assert not q["question"].lower().startswith("true or false"), (
            f"{q['question']!r} — if the fact is worth asking about, ask about the fact"
        )


def test_no_question_is_answerable_from_the_grammar_alone():
    """The tell the length tests could not see.

    When three distractors open with the same word and the correct option does
    not, the answer is the odd one out and no knowledge is required to spot it.
    Nine questions had this — "What is reaction time?" had three options
    beginning "How" and an answer beginning "The" — and every one of them
    passed the position and length checks.

    This is a guard, not a definition of good content: a question can satisfy
    it and still be dull, wrong or unkind. Those are read by a person.
    """
    offenders = []
    for q in appmod.BRAIN_BOOST_LIBRARY:
        firsts = [o.split()[0].lower().strip('",') for o in _distractors(q) if o.split()]
        answer_first = _correct(q).split()[0].lower().strip('",')
        if len(set(firsts)) == 1 and answer_first != firsts[0]:
            offenders.append(f"{q['question']} — distractors all start {firsts[0]!r}")
    assert not offenders, "answer is the odd one out by grammar:\n" + "\n".join(offenders[:6])


def test_no_question_asserts_the_answer_in_the_asking():
    """"What's wrong with 'no pain, no gain'?" told the reader that something
    was wrong with it, which ruled out the option saying nothing was."""
    leading = re.compile(r"\b(what'?s (wrong|missing)|why (is|does).{0,30}\bfail|"
                         r"what does .{0,30} get wrong)\b", re.I)
    offenders = [q["question"] for q in appmod.BRAIN_BOOST_LIBRARY
                 if leading.search(q["question"])]
    assert not offenders, "the question answers itself:\n" + "\n".join(offenders[:5])


def test_no_two_entries_say_the_same_thing():
    """Insight-to-insight, question-to-question and across the two: a person
    who reads both surfaces should not be told one fact twice."""
    import itertools

    stop = set("a an the of to in and or is are it its you your on for that this with as at be "
               "by from not what which how why does do can if than then more most some their "
               "they them we our but".split())

    def words(t):
        return {w for w in re.findall(r"[a-z]+", t.lower()) if w not in stop and len(w) > 3}

    entries = [(q["question"], words(q["options"][q["correct_index"]] + " " + q["explanation"]))
               for q in appmod.BRAIN_BOOST_LIBRARY]
    entries += [(e["text"], words(e["text"])) for e in appmod.INSIGHT_LIBRARY]

    dupes = []
    for (ta, wa), (tb, wb) in itertools.combinations(entries, 2):
        if not wa or not wb:
            continue
        if len(wa & wb) / len(wa | wb) >= 0.40:
            dupes.append(f"{ta[:60]!r}\n    ~ {tb[:60]!r}")
    assert not dupes, "near-duplicate content:\n" + "\n".join(dupes[:5])


# ── The content store ───────────────────────────────────────────────────────
#
# The library lives in content/items/*.jsonl now, not in application code. These
# hold the properties that make that store trustworthy; the editorial gates live
# in scripts/content/validate.py and run over the whole corpus.

def test_the_store_is_what_the_application_serves():
    """No parallel copy. If these ever diverge, one of them is a ghost."""
    import streakfit_content as store

    served = [i for i in store.ALL_ITEMS if i["stage"] == "accepted"]
    assert len(appmod.INSIGHT_LIBRARY) == sum(
        1 for i in served if i["type"] in store._DISCOVERY_TYPES)
    assert len(appmod.BRAIN_BOOST_LIBRARY) == sum(1 for i in served if i["type"] == "trivia")
    assert len(appmod.RICKIE_JOKES) == sum(1 for i in served if i["type"] == "joke")


def test_every_id_is_unique_and_well_formed():
    import streakfit_content as store

    ids = [i["id"] for i in store.ALL_ITEMS]
    assert len(set(ids)) == len(ids), "a content id is reused"
    for item_id in ids:
        assert re.match(r"^SF-[A-Z]{3}-\d{6}$", item_id), item_id


def test_a_confident_claim_carries_a_source():
    """The single rule that stops the library drifting back toward confident
    nonsense as it grows: `established` means somebody looked it up."""
    import streakfit_content as store

    unsourced = [i["id"] for i in store.ALL_ITEMS
                 if i["stage"] == "accepted"
                 and i["confidence"] == "established" and not i.get("sources")]
    assert not unsourced, f"claimed as established with no source: {unsourced[:5]}"


def test_only_accepted_content_reaches_a_reader():
    import streakfit_content as store

    texts = {e["text"] for e in appmod.INSIGHT_LIBRARY} | set(appmod.RICKIE_JOKES)
    for item in store.ALL_ITEMS:
        if item["stage"] != "accepted" and item.get("text"):
            assert item["text"] not in texts, f"{item['id']} is {item['stage']} but served"


def test_the_store_validator_passes():
    """The editorial gates, run as a test so a batch cannot be merged red."""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(appmod.__file__).resolve().parent
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / "content" / "validate.py")],
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-1000:]


def test_nothing_reaches_a_reader_without_a_recorded_review():
    """Validation is a program checking structure. It is not somebody reading
    the thing. An item may only be served once a review pass has been recorded
    against it, with how hard it was checked."""
    import streakfit_content as store

    unreviewed = []
    for item in store.ALL_ITEMS:
        if item["stage"] != "accepted":
            continue
        review = item.get("review") or {}
        if not review.get("pass") or review.get("depth") not in ("sourced", "read", "tested"):
            unreviewed.append(item["id"])
    assert not unreviewed, f"served without a recorded review: {unreviewed[:5]}"


def test_the_stages_are_reported_separately():
    """"We generated 5,000", "5,000 passed validation" and "5,000 are in the
    product" are three different claims, and the counts must not collapse
    them."""
    import streakfit_content as store

    counts = store.counts()
    assert "by_stage" in counts
    assert sum(counts["by_stage"].values()) == counts["total"]
