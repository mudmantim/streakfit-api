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
    r"hold\w* (your |the )?breath|breath.?hold\w*"
    # Eyes closed. This used to require "while walking or running", which is
    # narrower than the rule it stands for: the fall risk is BALANCING with
    # your eyes shut, and an item inviting exactly that ("closing your eyes
    # while standing still makes balancing much harder") passed this gate and
    # was served for months. Two reviewers found it independently.
    #
    # Note the scope: for trivia, `body()` includes the DISTRACTORS, which is
    # deliberate — a wrong option is still read, and "close your eyes and
    # stand on one leg" is no safer for being the wrong answer.
    #
    # Deliberately NOT matched: eyes closed while seated or still and not
    # balancing, e.g. touching your nose to demonstrate proprioception. That
    # is the illustration the balance items should have used.
    # "eyes closed", "eyes shut" AND "closing your eyes" — the live item said
    # the third, so matching only the first two would have missed the exact
    # case this rule was widened for.
    r"|(?:eyes (?:closed|shut)|clos(?:e|ing)[a-z]* (?:your |the )?eyes)"
    r"[^.]{0,70}\b(?:walk|run|balanc|stand|one (?:leg|foot)|heel|tiptoe)"
    r"|\b(?:walk|run|balanc|stand)[a-z]*[^.]{0,70}"
    r"(?:eyes (?:closed|shut)|clos(?:e|ing)[a-z]* (?:your |the )?eyes)"
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
    errs = []
    if item.get("confidence") == "established" and not item.get("sources"):
        errs.append("confidence 'established' with no sources")
    if item.get("confidence") == "editorial" and item.get("sources"):
        errs.append("editorial content does not need sources — is the confidence wrong?")

    # A NON-EMPTY sources list is not a checked one. Batch 0003 shipped two
    # `established` items whose citations were fabricated: one PMID resolved to
    # a 1990 paper on bacterial meningitis, another to a report on a natural-gas
    # pipeline. Both claims happened to be true and both citations were
    # authoritative-looking nonsense, and this function passed them, because all
    # it asked was whether the list had something in it.
    #
    # Nothing here can fetch a URL and read it. What it CAN do is refuse to let
    # an `established` claim reach a reader unless a review recorded that it was
    # checked AT SOURCE — `depth: sourced` is the reviewer asserting they
    # resolved the citation, and it is a different claim from having read the
    # item. Anything weaker belongs at `simplified`, which needs no source.
    if item.get("confidence") == "established" and item.get("stage") == "accepted":
        depth = (item.get("review") or {}).get("depth")
        if depth != "sourced":
            errs.append(
                f"'established' accepted with review depth {depth!r} — an "
                "established claim may only be served if a reviewer recorded "
                "that they resolved the source (depth 'sourced'); a non-empty "
                "sources list is not a checked one")
    return errs


# An unearned comparative is the failure mode this library actually has.
#
# Four independent re-reviewers of the inherited pool reached the same
# conclusion without conferring: almost nothing is outright false, and the
# defects are nearly all a TRUE CORE wrapped in a quantity nobody measured.
# "Stair climbing uses more muscles than almost any other everyday movement."
# "Most repair happens while you sleep." "A few seconds a day improves your
# balance." "Bone stands up to squashing about as well as concrete."
#
# Every one of those was tagged `simplified` with no sources, and passed,
# because the schema only requires a source at `established`. So `simplified`
# had become a sourcing exemption: the label a claim wears to avoid being
# checked. The reviewers each proposed the same rule, which is this one.
#
# The trigger is narrow on purpose — a shape of claim, not a topic. Hedged
# language ("tends to", "can", "often") is left alone, because hedging is the
# honest version and penalising it would push authors back toward firmness.
_EMPIRICAL_CLAIM = re.compile(
    r"\b(?:more|less|fewer|better|worse|faster|slower|stronger|harder|easier)\s+than\b"
    # "most days" and "most of the time" are frequency idioms about a person's
    # own behaviour, not proportions of a population, so they are excluded by
    # the negative lookahead. Tuned against the real corpus rather than
    # invented: without it the gate fired on "move on most days", which is
    # advice, not a measured claim.
    r"|\b(?:most|almost all|nearly all|the majority of)\s+"
    r"(?!days\b|of the time\b|mornings\b|evenings\b|weeks\b)\w+"
    r"|\bmore\s+\w+\s+than\s+(?:any|almost)\b"
    r"|\b(?:twice|three times|half|a third|two thirds)\s+as\b"
    r"|\b\d+\s*(?:%|percent)\b"
    r"|\bas\s+\w+\s+as\s+(?:a|an|the)\b",
    re.I)

# Hedges that turn a comparative into a claim about a tendency. If one of these
# is present the sentence is no longer asserting a measured quantity.
_HEDGED = re.compile(
    r"\b(?:tends?\s+to|can\b|may\b|might\b|often|usually|generally|for\s+some"
    r"|roughly|about|around|some\s+people|it\s+varies|varies)\b", re.I)


def check_measured_claims(item: dict) -> list[str]:
    """A comparative or a proportion needs a source, whatever the confidence.

    Not a warning. An item making a measured claim with nothing behind it is
    exactly what the last three content incidents were, and a warning is a
    thing that gets scrolled past.
    """
    if item.get("type") in ("joke", "riddle", "rickie"):
        return []
    if item.get("sources"):
        return []
    if item.get("stage") != "accepted":
        return []           # only bites on what actually reaches a reader
    text = body(item)
    m = _EMPIRICAL_CLAIM.search(text)
    if not m or _HEDGED.search(text):
        return []
    return [f"measured claim {m.group(0)!r} with no sources — a comparative, "
            f"proportion or dose needs one whatever the confidence tier says; "
            f"hedge it or cite it"]


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

    # Errors are split by whether the item is SERVED.
    #
    # The contract at the top of this file says an accepted item carrying an
    # error fails the run. The implementation failed on any error at all,
    # which only became a contradiction once the pipeline started parking
    # defective items at `revise` in bulk: an item moved to `revise` BECAUSE
    # it has a defect would then break the build forever, so the only way to
    # get green was to delete the evidence. Parking is the whole point of the
    # stage, so a parked item's errors are reported and do not block.
    errors, parked, warnings = [], [], []
    seen_ids = set()
    for item in items:
        prefix = f"{item.get('id', '?')} ({item.get('_file', '?')})"
        found = (check_shape(item) + check_provenance(item)
                 + check_measured_claims(item))
        errs, warns = check_language(item)
        found += errs
        if item.get("id") in seen_ids:
            # A duplicate id is a corpus problem whatever the stage: two rows
            # answering to one id break every lookup, served or not.
            errors.append(f"{prefix}: duplicate id")
        seen_ids.add(item.get("id"))
        bucket = errors if item.get("stage") == SERVED_STAGE else parked
        bucket += [f"{prefix}: {e}" for e in found]
        warnings += [f"{prefix}: {w}" for w in warns]

    # Corpus-level gates only make sense over everything, never one batch.
    if not args.batch:
        errs, warns = check_trivia_tells(items)
        errors += errs
        warnings += warns
        errors += check_duplicates(items)

    print()
    if parked:
        print(f"  ({len(parked)} errors on items already parked at 'revise' or "
              f"'rejected' — reported, not blocking; they are why those items "
              f"are parked)")
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
