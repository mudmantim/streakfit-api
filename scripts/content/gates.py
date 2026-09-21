#!/usr/bin/env python3
"""Self-gates a trivia batch must clear before anybody is asked to review it.

`validate.py` runs the corpus tells (`check_trivia_tells`, `check_duplicates`)
over ACCEPTED items only. That is right for a gate guarding what gets served,
and it means a batch can sit at `generated` for weeks with a tell nobody has
measured. So a batch checks itself, here, before it costs a reviewer anything.

This lives in its own module because batch 0003 carried its own copy of these
rules, and a second copy is how the British-spelling regex and the banned
vocabulary list each drifted from their originals earlier in this project. One
list, imported.
"""
from __future__ import annotations

import difflib
import re

# A reader must not be able to score above chance without knowing anything.
MAX_LONGEST_KEY_SHARE = 0.45     # validate.py's corpus threshold
MAX_POSITION_SHARE = 0.40        # one answer position correct this often
MAX_KEY_LENGTH_GAP = 12          # characters the key may exceed every distractor by
NEAR_DUPLICATE_RATIO = 0.82      # difflib ratio between two question stems

# Distractors a reader learns to never pick.
BARE = re.compile(
    r"^(nothing( at all| measurable)?|no effect|none|never|no real benefit"
    r"|it doesn'?t matter|it'?s a myth|not at all)\.?$", re.I)
ABSOLUTE = re.compile(r"\b(always|never|only|entirely|all|none|every)\b", re.I)


def _key(item):
    return item["options"][item["answer_index"]]


def _distractors(item):
    return [o for n, o in enumerate(item["options"]) if n != item["answer_index"]]


def check_batch(items, existing_stems=()):
    """Return a list of human-readable failures. Empty means the batch is fit
    to hand to a reviewer — NOT that it is any good, which is their job."""
    fails = []
    n = len(items)
    if not n:
        return ["the batch is empty"]

    positions = {i: sum(1 for x in items if x["answer_index"] == i) for i in range(4)}
    if max(positions.values()) / n >= MAX_POSITION_SHARE:
        fails.append(f"one answer position is correct too often: {positions}")

    longest = sum(1 for x in items if len(_key(x)) == max(len(o) for o in x["options"]))
    if longest / n >= MAX_LONGEST_KEY_SHARE:
        fails.append(
            f"the correct option is the longest in {longest / n:.0%} of questions "
            f"(chance is 25%) — trim the key and let the detail live in the "
            f"explanation, where a reader gets it after answering")

    # The tell that survived batch 0003's review and was only caught by a cold
    # spot-check: three absolutes and one careful answer means the careful one
    # is right, every time, without reading the topic.
    hedged_key = 0
    for x in items:
        if not ABSOLUTE.search(_key(x)) and all(ABSOLUTE.search(d) for d in _distractors(x)):
            hedged_key += 1
    if hedged_key / n >= 0.25:
        fails.append(
            f"in {hedged_key / n:.0%} of questions the key is the only option "
            f"without an absolute — the hedged one is always right, so the topic "
            f"never has to be read")

    seen = {}
    for x in items:
        gap = len(_key(x)) - max(len(o) for o in _distractors(x))
        if gap > MAX_KEY_LENGTH_GAP:
            fails.append(f"{x.get('id', x['question'][:40])}: key stands out by {gap} characters")
        firsts = {d.split()[0].lower().strip('",') for d in _distractors(x) if d.split()}
        first_key = _key(x).split()[0].lower().strip('",') if _key(x).split() else ""
        if len(firsts) == 1 and first_key not in firsts:
            fails.append(f"{x.get('id', x['question'][:40])}: three distractors open "
                         f"{firsts.pop()!r} and the key does not — answerable by grammar")
        for d in _distractors(x):
            if BARE.match(d.strip()):
                fails.append(f"{x.get('id', x['question'][:40])}: bare dismissal as a "
                             f"distractor: {d!r}")
        if len(set(x["options"])) != 4:
            fails.append(f"{x.get('id', x['question'][:40])}: options are not distinct")
        if not x["question"].strip().endswith("?"):
            fails.append(f"{x.get('id', x['question'][:40])}: stem does not end in '?'")
        low = x["question"].strip().lower()
        if low in seen:
            fails.append(f"duplicate stem: {x['question'][:60]!r}")
        seen[low] = True

    stems = [x["question"] for x in items]
    for i in range(len(stems)):
        for j in range(i + 1, len(stems)):
            r = difflib.SequenceMatcher(None, stems[i].lower(), stems[j].lower()).ratio()
            if r >= NEAR_DUPLICATE_RATIO:
                fails.append(f"near-duplicate within the batch ({r:.0%}): "
                             f"{stems[i][:45]!r} / {stems[j][:45]!r}")
    for s in stems:
        for e in existing_stems:
            r = difflib.SequenceMatcher(None, s.lower(), e.lower()).ratio()
            if r >= NEAR_DUPLICATE_RATIO:
                fails.append(f"near-duplicate of a library stem ({r:.0%}): {s[:45]!r}")
    return fails


def report(items, existing_stems=()):
    """Print the shape of a batch and its failures. Returns an exit code."""
    n = len(items)
    positions = {i: sum(1 for x in items if x["answer_index"] == i) for i in range(4)}
    longest = sum(1 for x in items if len(_key(x)) == max(len(o) for o in x["options"]))
    conf = {}
    for x in items:
        conf[x.get("confidence")] = conf.get(x.get("confidence"), 0) + 1
    print(f"  items         : {n}")
    print(f"  answer spread : {positions}")
    print(f"  correct-is-longest: {longest}/{n} ({longest / n:.0%}, chance 25%, gate 45%)")
    print(f"  confidence    : {conf}")
    fails = check_batch(items, existing_stems)
    if fails:
        print("\nBATCH GATE FAILED — written, but not fit to review:")
        for f in fails:
            print(f"  ERROR  {f}")
        return 1
    print("  tells         : within thresholds")
    return 0
