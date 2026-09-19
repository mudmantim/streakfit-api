#!/usr/bin/env python3
"""Quality gates for the content store.

Run over one batch while writing it, and over the whole corpus before shipping:

    python scripts/content/validate.py                 # everything
    python scripts/content/validate.py --batch 0002    # one batch
    python scripts/content/validate.py --counts        # just the numbers

Two kinds of finding, and the difference matters:

  ERROR    the item may not be served. Reported against its id, and an accepted
           item carrying one fails the run.
  WARNING  worth a human looking. Never blocks, because a gate that blocks on
           taste is a gate that gets switched off.

What this cannot do is tell you whether the content is any good. It catches
structure, contradiction, missing provenance and the specific tells that have
actually bitten this library — answer-position bias, length bias, a question
answerable from its grammar, a fact stated more confidently than its evidence.
Every one of those was found by a person first and only then written down here.
Passing this file is necessary and nowhere near sufficient.
"""
from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ITEMS = ROOT / "content" / "items"

TYPES = {"fact", "trivia", "joke", "riddle", "movement", "experiment", "rickie"}
CONFIDENCE = {"established", "simplified", "contested", "editorial"}
STAGES = ("generated", "validated", "reviewed", "accepted", "revise", "rejected")
SERVED_STAGE = "accepted"
ID_RE = re.compile(r"^SF-[A-Z]{3}-\d{6}$")

# ── Vocabulary that must never reach a reader ───────────────────────────────
# Whole words only: substring matching once flagged "within" for "thin".
BANNED = [
    "weight loss", "lose weight", "losing weight", "body fat", "belly fat",
    "calorie", "calories", "diet plan", "dieting", "slim", "toned",
    "overweight", "obese", "bmi", "waistline", "flat stomach", "six pack",
    "burn fat", "skinny",
    "diagnose", "diagnosis", "you should take", "prevents disease", "medication",
    "lazy", "excuses", "no excuses", "guilty", "ashamed",
]
ACCUSATIONS = [
    "you failed", "you've failed", "you gave up", "you quit", "you lost your streak",
    "you're lazy", "you are lazy", "you should be ashamed", "don't be lazy",
]
# Cruelty in humour. A joke may be silly, self-deprecating or absurd; it may not
# be at somebody's expense.
CRUEL = re.compile(
    r"\b(stupid|idiot|loser|fat|ugly|dumb|pathetic|useless|failure|"
    r"nobody likes|laugh at (him|her|them)|make fun of)\b", re.I)
# Actions a child could copy and be hurt by. Allowed only where the explanation
# names the danger.
DANGEROUS = re.compile(
    r"hold\w* (your |the )?breath|breath.?hold\w*|eyes (closed|shut) while (walking|running)"
    r"|skip(ping)? (a |your |my )?(next )?meals?|stop(ping)? eating", re.I)
BRITISH = re.compile(
    r"\b(colour\w*|centre|fibre\w*|practis\w*|recognis\w*|stabilis\w*|favourite"
    # realis\w* also matched "realistic", "realism" and "realist", which are
    # American English too — it flagged ordinary copy as a Briticism. Bounded to
    # the verb forms that are actually British.
    r"|behaviour\w*|realis(e|es|ed|ing)\b|neighbour\w*|apologis\w*|metres?|kilometres?"
    r"|litres?|grey|kerbs?)\b", re.I)
BARE = re.compile(
    r"^(nothing( at all| measurable)?|no effect|none|never|no real benefit"
    r"|it doesn'?t matter|it'?s a myth|not at all)\.?$", re.I)
LEADING_Q = re.compile(
    r"\b(what'?s (wrong|missing)|what does .{0,30} get wrong)\b", re.I)

STOP = set("a an the of to in and or is are it its you your on for that this with as at be by "
           "from not what which how why does do can if than then more most some their they them "
           "we our but".split())


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", text.lower()) if w not in STOP and len(w) > 3}


def body(item: dict) -> str:
    """Everything a reader sees. Used for vocabulary and safety checks."""
    if item["type"] == "trivia":
        return " ".join([item.get("question", ""), item.get("explanation", "")]
                        + list(item.get("options", [])))
    return item.get("text", "")


def claim(item: dict) -> str:
    """What an item actually TELLS you — the answer and the explanation, not the
    distractors.

    Duplicate detection needs this rather than `body`. Three wrong options are
    three-quarters of a question's words and they dilute the similarity of the
    quarter that matters, which is how a mini-experiment about standing up from
    a chair sat beside a question whose answer was "a squat" and neither the
    gate nor I noticed.
    """
    if item["type"] == "trivia":
        options = item.get("options") or []
        idx = item.get("answer_index", 0)
        answer = options[idx] if 0 <= idx < len(options) else ""
        return f"{answer} {item.get('explanation', '')}"
    return item.get("text", "")


def load(batch: str | None = None) -> list[dict]:
    rows, bad = [], []
    for path in sorted(ITEMS.glob("*.jsonl")):
        if batch and not path.name.startswith(batch):
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                bad.append(f"{path.name}:{n} is not valid JSON ({exc})")
                continue
            item["_file"] = path.name
            rows.append(item)
    if bad:
        for b in bad:
            print(f"ERROR  {b}")
        sys.exit(2)
    return rows


# ── Gates ───────────────────────────────────────────────────────────────────

def check_shape(item: dict) -> list[str]:
    errs = []
    for field in ("id", "type", "category", "min_age", "confidence", "stage", "added", "batch"):
        if not item.get(field) and item.get(field) != 0:
            errs.append(f"missing {field}")
    if not ID_RE.match(item.get("id", "")):
        errs.append(f"id {item.get('id')!r} is not SF-XXX-000000")
    if item.get("type") not in TYPES:
        errs.append(f"unknown type {item.get('type')!r}")
    if item.get("confidence") not in CONFIDENCE:
        errs.append(f"unknown confidence {item.get('confidence')!r}")
    if item.get("stage") not in STAGES:
        errs.append(f"unknown stage {item.get('stage')!r}")
    if item.get("stage") in ("reviewed", "accepted"):
        review = item.get("review") or {}
        if not review.get("pass"):
            errs.append(f"stage {item['stage']!r} with no review recorded — "
                        "passing validation is not a review")
        if review.get("depth") not in ("sourced", "read", "tested"):
            errs.append(f"review depth {review.get('depth')!r} is not sourced/read/tested")
    if item.get("min_age") not in (9, 13, 16):
        errs.append(f"min_age {item.get('min_age')!r} is not one of 9, 13, 16")

    if item.get("type") == "trivia":
        opts = item.get("options") or []
        if len(opts) != 4:
            errs.append(f"{len(opts)} options, expected 4")
        elif len(set(opts)) != 4:
            errs.append("duplicate options")
        if not isinstance(item.get("answer_index"), int) or not 0 <= item.get("answer_index", -1) < 4:
            errs.append("answer_index out of range")
        for field in ("question", "explanation"):
            if not (item.get(field) or "").strip():
                errs.append(f"empty {field}")
        if item.get("question", "").strip() and not item["question"].strip().endswith("?"):
            errs.append("question does not end in a question mark")
        if item.get("question", "").lower().startswith("true or false"):
            errs.append("true/false framing — ask about the fact instead")
        if LEADING_Q.search(item.get("question", "")):
            errs.append("the question asserts its own answer")
    elif not (item.get("text") or "").strip():
        errs.append("empty text")
    return errs


def check_provenance(item: dict) -> list[str]:
    """The one rule that stops the library drifting back to confident nonsense."""
    if item.get("confidence") == "established" and not item.get("sources"):
        return ["confidence 'established' with no sources"]
    if item.get("confidence") == "editorial" and item.get("sources"):
        return ["editorial content does not need sources — is the confidence wrong?"]
    return []


def check_language(item: dict) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    text = body(item)
    low = text.lower()
    for banned in BANNED:
        if re.search(r"\b" + re.escape(banned) + r"\b", low):
            errs.append(f"banned vocabulary: {banned!r}")
    for phrase in ACCUSATIONS:
        if phrase in low:
            errs.append(f"the product must never say this: {phrase!r}")
    if item["type"] in ("joke", "riddle", "rickie") and CRUEL.search(text):
        errs.append("humour at somebody's expense")
    for m in BRITISH.finditer(text):
        warns.append(f"British spelling {m.group(0)!r} — the library is American English")
    if DANGEROUS.search(text):
        explanation = item.get("explanation", "") or item.get("text", "")
        if not DANGEROUS.search(explanation):
            errs.append("describes an action a child could copy, uncorrected")
    return errs, warns


def check_trivia_tells(items: list[dict]) -> tuple[list[str], list[str]]:
    """Corpus-level: the ways an answer can be found without knowing anything."""
    errs, warns = [], []
    trivia = [i for i in items if i["type"] == "trivia" and i["stage"] == SERVED_STAGE]
    if len(trivia) < 20:
        return errs, warns

    def correct(i):
        return i["options"][i["answer_index"]]

    def distractors(i):
        return [o for n, o in enumerate(i["options"]) if n != i["answer_index"]]

    spread = Counter(i["answer_index"] for i in trivia)
    if len(spread) < 4:
        errs.append(f"some answer positions are never correct: {dict(spread)}")
    elif max(spread.values()) / len(trivia) >= 0.40:
        errs.append(f"one answer position is correct too often: {dict(spread)}")

    longest = sum(1 for i in trivia if len(correct(i)) == max(len(o) for o in i["options"]))
    if longest / len(trivia) >= 0.45:
        errs.append(f"the correct option is the longest in {longest/len(trivia):.0%} "
                    f"of questions (chance is 25%)")
    shortest = sum(1 for i in trivia if len(correct(i)) == min(len(o) for o in i["options"]))
    if shortest / len(trivia) >= 0.45:
        errs.append(f"the correct option is the shortest in {shortest/len(trivia):.0%}")

    for i in trivia:
        gap = len(correct(i)) - max(len(o) for o in distractors(i))
        if gap > 12:
            errs.append(f"{i['id']}: correct option stands out by {gap} characters")
        firsts = [o.split()[0].lower().strip('",') for o in distractors(i) if o.split()]
        first_correct = correct(i).split()[0].lower().strip('",') if correct(i).split() else ""
        if len(set(firsts)) == 1 and first_correct != firsts[0]:
            errs.append(f"{i['id']}: three distractors all start {firsts[0]!r} — "
                        "the answer is the odd one out by grammar")
        for o in distractors(i):
            if BARE.match(o.strip()):
                errs.append(f"{i['id']}: bare dismissal as a distractor: {o!r}")
        qw, aw = words(i["question"]), words(correct(i))
        if qw and aw and len(qw & aw) / len(aw) > 0.6:
            warns.append(f"{i['id']}: the answer restates the question")
    return errs, warns


def check_duplicates(items: list[dict]) -> list[str]:
    served = [i for i in items if i["stage"] == SERVED_STAGE]
    errs = []
    exact = defaultdict(list)
    for i in served:
        exact[claim(i).strip().lower()].append(i["id"])
    for _text, ids in exact.items():
        if len(ids) > 1:
            errs.append(f"identical content in {', '.join(ids)}")
    keyed = [(i["id"], words(claim(i))) for i in served]
    for (ida, wa), (idb, wb) in itertools.combinations(keyed, 2):
        if not wa or not wb:
            continue
        if len(wa & wb) / len(wa | wb) >= 0.40:
            errs.append(f"near-duplicate: {ida} and {idb}")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", help="only this batch, by filename prefix")
    ap.add_argument("--counts", action="store_true", help="print the counts and stop")
    args = ap.parse_args()

    items = load(args.batch)
    if not items:
        print("no content found")
        return 1

    by_stage = Counter(i.get("stage") for i in items)
    by_type = Counter(i.get("type") for i in items)
    by_conf = Counter(i.get("confidence") for i in items)
    accepted = [i for i in items if i.get("stage") == SERVED_STAGE]

    print(f"Content store — {len(items)} items across {len(set(i['_file'] for i in items))} files")
    print("  by stage      " + ", ".join(
        f"{stage} {by_stage[stage]}" for stage in STAGES if by_stage[stage]))
    print("  by type       " + ", ".join(f"{k} {v}" for k, v in sorted(by_type.items())))
    print("  by confidence " + ", ".join(f"{k} {v}" for k, v in sorted(by_conf.items())))
    if args.counts:
        return 0

    errors, warnings = [], []
    seen_ids = set()
    for item in items:
        prefix = f"{item.get('id', '?')} ({item.get('_file', '?')})"
        for err in check_shape(item) + check_provenance(item):
            errors.append(f"{prefix}: {err}")
        errs, warns = check_language(item)
        errors += [f"{prefix}: {e}" for e in errs]
        warnings += [f"{prefix}: {w}" for w in warns]
        if item.get("id") in seen_ids:
            errors.append(f"{prefix}: duplicate id")
        seen_ids.add(item.get("id"))

    # Corpus-level gates only make sense over everything, never one batch.
    if not args.batch:
        errs, warns = check_trivia_tells(items)
        errors += errs
        warnings += warns
        errors += check_duplicates(items)

    print()
    for w in warnings[:25]:
        print(f"  WARN   {w}")
    if len(warnings) > 25:
        print(f"  ... and {len(warnings) - 25} more warnings")
    for e in errors[:40]:
        print(f"  ERROR  {e}")
    if len(errors) > 40:
        print(f"  ... and {len(errors) - 40} more errors")

    print()
    # Never report items as cleared while errors stand — that line was printing
    # "614 items cleared to serve" directly above five blocking errors.
    cleared = 0 if errors else len(accepted)
    print(f"{len(errors)} errors, {len(warnings)} warnings, "
          f"{cleared} items cleared to serve")
    if errors:
        print("\nAccepted content may not carry an error. Fix, or set status to "
              "'pending' until it is fixed.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
