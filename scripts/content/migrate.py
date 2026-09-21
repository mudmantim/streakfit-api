#!/usr/bin/env python3
"""Move the existing content out of app.py and into the store, once.

Everything already in the product has been through the September 2026 review,
so it arrives `accepted`. What it does NOT arrive with is sources: the review
checked 24 claims against sources and read the rest, and pretending otherwise
by stamping `established` on all of it would launder a judgement into a
citation. Items the review actually sourced get `established` and their URLs;
the rest get `simplified` or `editorial`, which is what they honestly are.

Run once. Re-running rebuilds the same files from the same input, so it is safe,
but after this the store is the source of truth and app.py is not.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SECRET_KEY", "migrate")
os.environ.setdefault("JWT_SECRET_KEY", "migrate")

import app as appmod  # noqa: E402

BATCH = "0001-migrated"
TODAY = date.today().isoformat()
OUT = ROOT / "content" / "items"

# The claims the September review actually looked up, and what it looked at.
SOURCED = {
    "sneeze": ["https://pmc.ncbi.nlm.nih.gov/articles/PMC8663001/",
               "https://www.popsci.com/science/article/2013-08/fyi-how-forceful-sneeze/"],
    "first few minutes of exercise": ["https://pubmed.ncbi.nlm.nih.gov/10368878/"],
    "enamel": ["https://pmc.ncbi.nlm.nih.gov/articles/PMC7076334/",
               "https://pmc.ncbi.nlm.nih.gov/articles/PMC10135549/"],
    "long-distance runners": ["https://en.wikipedia.org/wiki/Endurance_running_hypothesis",
                              "https://www.ucdavis.edu/blog/humans-are-born-run"],
}

# Words that mark a claim the writer already flagged as unsettled.
CONTESTED = re.compile(r"\b(argued over|nobody agrees|still open|contested|disputed|"
                       r"does not agree with itself|was directly contradicted)\b", re.I)

# Encouragement, framing and humour — the only things that legitimately need no
# source. The list is deliberately narrow, and anything not clearly in it falls
# through to `simplified`.
#
# The first version of this had it backwards: a claim was editorial UNLESS it
# contained "because" or a digit, which made "The gluteus maximus is the largest
# muscle you own" editorial and exempt from ever being source-checked. 446 of
# 539 items landed in the bucket that means "nothing to verify". Defaulting to
# `simplified` gets the same items into the review queue instead of out of it,
# which is the whole reason the field exists.
ENCOURAGEMENT = re.compile(
    r"\b(worth (noticing|doing|saying|having)|you can always|it still counts|"
    r"there'?s no (right|wrong)|be kind|talk to yourself|small wins|"
    r"showing up|the habit|nobody is watching|it doesn'?t have to|"
    r"give yourself|proud of|good enough|a promise|reaching out|"
    r"listen(ing)? (to|without)|asking for help|comparing yourself)\b", re.I)

# Anything that asserts something about the world: a number, a superlative, a
# mechanism, or an absolute. Mirrors the triage used in the September review.
FACTUAL = re.compile(
    r"\d|\b(because|which is why|that'?s why|the reason|causes?|caused by|"
    r"the (strongest|largest|biggest|smallest|longest|fastest|hardest|only|most)|"
    r"always|never|every|cannot|can'?t|impossible|"
    r"muscle|bone|heart|lung|blood|brain|nerve|joint|sleep|oxygen|"
    r"tendon|ligament|cartilage|vitamin|mineral|calorie|water|skin)\b", re.I)


def classify(text: str) -> tuple[str, list[str]]:
    """Establish what KIND of claim this is, conservatively.

    Wrong in the safe direction on purpose: an encouragement mislabelled
    `simplified` costs a reviewer a glance, while a fact mislabelled
    `editorial` is a fact nobody will ever check.
    """
    for needle, urls in SOURCED.items():
        if needle.lower() in text.lower():
            return "established", urls
    if CONTESTED.search(text):
        return "contested", []
    if ENCOURAGEMENT.search(text) and not FACTUAL.search(text):
        return "editorial", []
    return "simplified", []


def _shuffled_once(boost: dict) -> dict:
    """Scatter one question's options, deterministically from its text.

    This used to live in app.py and run on every request. It is a one-time
    normalisation, not a serving concern — see the note in main().
    """
    import random

    order = list(range(len(boost["options"])))
    random.Random("boost-options:" + boost["question"]).shuffle(order)
    out = dict(boost)
    out["options"] = [boost["options"][i] for i in order]
    out["correct_index"] = order.index(boost["correct_index"])
    return out


def emit(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    counters = {"FCT": 0, "TRV": 0, "JOK": 0}

    def next_id(kind: str) -> str:
        counters[kind] += 1
        return f"SF-{kind}-{counters[kind]:06d}"

    # ── Insights become facts, or movement discoveries where they are about
    #    moving rather than about bodies in general.
    facts = []
    for entry in appmod.INSIGHT_LIBRARY:
        confidence, sources = classify(entry["text"])
        kind = "movement" if entry["category"] in ("MOVEMENT", "FLEXIBILITY", "BALANCE") else "fact"
        facts.append({
            "id": next_id("FCT"), "type": kind, "category": entry["category"],
            "text": entry["text"], "min_age": 9,
            "confidence": confidence, "sources": sources,
            "status": "accepted", "added": TODAY, "batch": BATCH,
        })

    # The stored answer position is normalised here rather than left to the
    # serve-time shuffle.
    #
    # The September rewrite set correct_index to 0 on almost every question and
    # relied on `_presented_brain_boost` to scatter them, so the SERVED spread
    # was fine (49/52/47/44) while the STORED data sat at 188 of 192 on index 0.
    # Nothing a reader could exploit — and a trap for anything that ever reads
    # the store without going through the app: an export, a review, a different
    # renderer. The shuffle is derived from the question text and is therefore
    # the same permutation the app would have applied, so baking it in changes
    # nothing anyone sees and makes the file honest by itself.
    trivia = []
    for q in appmod.BRAIN_BOOST_LIBRARY:
        blob = q["question"] + " " + q["explanation"]
        confidence, sources = classify(blob)
        shown = _shuffled_once(q)
        trivia.append({
            "id": next_id("TRV"), "type": "trivia", "category": q.get("category", "General"),
            "question": shown["question"], "options": list(shown["options"]),
            "answer_index": shown["correct_index"], "explanation": shown["explanation"],
            "min_age": 9, "confidence": confidence, "sources": sources,
            "status": "accepted", "added": TODAY, "batch": BATCH,
        })

    # A joke has nothing to verify by construction, so it is always editorial —
    # it is the one type where that is a fact about the form, not a judgement.
    jokes = [{
        "id": next_id("JOK"), "type": "joke", "category": "Rickie",
        "text": j, "min_age": 9, "confidence": "editorial", "sources": [],
        "status": "accepted", "added": TODAY, "batch": BATCH,
    } for j in appmod.RICKIE_JOKES]

    emit(OUT / f"{BATCH}-facts.jsonl", facts)
    emit(OUT / f"{BATCH}-trivia.jsonl", trivia)
    emit(OUT / f"{BATCH}-jokes.jsonl", jokes)

    print(f"{len(facts):4} facts and movement discoveries")
    print(f"{len(trivia):4} trivia questions")
    print(f"{len(jokes):4} jokes")
    print(f"{len(facts)+len(trivia)+len(jokes):4} items total -> content/items/")
    by_conf = {}
    for row in facts + trivia + jokes:
        by_conf[row["confidence"]] = by_conf.get(row["confidence"], 0) + 1
    print("confidence:", dict(sorted(by_conf.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
