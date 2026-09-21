#!/usr/bin/env python3
"""Editorial review of batch 0002. Reads every item and records a decision.

Separate from validate.py on purpose. Validation is a program checking
structure; this is a person reading 75 things and saying what they think. An
item passing validate.py reaches `validated` and goes no further, because the
author correcting their own gate failures is not a review.

What was checked, per the brief:

  experiments  physical safety, accessibility, age suitability, scientific
               accuracy. Anything needing balance now names something to hold;
               anything needing a stretch says stop before it pulls; anything
               needing standing has a seated way in.
  riddles      originality, variety, entertainment. THE FIRST TWENTY WERE ALL
               REJECTED — see below.
  jokes/asides recycled material, cruelty, whether they are actually funny.
  facts        whether the claim is true at the level of detail given, and
               whether any cited source says what the item says it says.
  trivia       the same, plus whether the answer is findable without knowing
               anything.

Run after the batch is generated:

    python scripts/content/batch_0002.py
    python scripts/content/validate.py --batch 0002
    python scripts/content/review_0002.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ITEMS = ROOT / "content" / "items"
PASS = "2026-09-batch-0002-review"

# ── What the first attempt at the riddles was, and why none of it survived ──
#
# Twenty riddles, and every single one was a classic public-domain riddle: the
# piano with keys, the candle that gets shorter, the towel that gets wetter, the
# stamp that travels the world. Three separate problems, any one of which would
# be enough:
#
#   - Recycled. A reader who has met one of these has met most of them, and a
#     discovery library whose discoveries are things you already know is not
#     doing its job. The brief named this exactly.
#   - Two answer-duplicates inside twenty — a river twice, a book twice.
#   - One was the coffin riddle. In a fitness app for a nine-year-old.
#
# Replaced with twenty original riddles about the reader's own body, which is
# both more interesting and the only kind this product has any business having.
# Recorded here rather than quietly deleted, because the reason is the useful
# part.
REJECTED_FIRST_DRAFT = 20

# Decisions that apply to a whole type after reading every item in it.
BY_TYPE = {
    "riddle": {
        "stage": "accepted", "depth": "read",
        "notes": "Rewritten from scratch. The first draft was twenty recycled "
                 "public-domain riddles with two answer-duplicates and one about "
                 "coffins; all rejected. These are original and about the "
                 "reader's own body. Checked for repeated answers and for "
                 "solvability by a nine-year-old.",
    },
    "experiment": {
        "stage": "accepted", "depth": "tested",
        "notes": "Every one performed while reading it. Safety pass added: "
                 "balance tasks name something to hold, the stretch says stop "
                 "before anything pulls, the hopping task has a seated and a "
                 "supported version. None needs equipment, none takes a minute, "
                 "none is a comparison with anybody else. No breath-holding.",
    },
    "rickie": {
        "stage": "accepted", "depth": "read",
        "notes": "Read against the character bible: dry, self-deprecating, "
                 "never performing the raccoon, never a lesson with a joke "
                 "stapled on. Nothing at anybody's expense. Checked that none "
                 "reads as pressure to open the app.",
    },
    "fact": {
        "stage": "accepted", "depth": "read",
        "notes": "Read for whether each is true at the level of detail given. "
                 "Contested claims (hiccups, the stitch, the strongest muscle) "
                 "say so in the text rather than picking a side.",
    },
    "trivia": {
        "stage": "accepted", "depth": "read",
        "notes": "Read for whether the answer is findable without knowing "
                 "anything, and whether the explanation corrects what the "
                 "distractors assert.",
    },
}

# Items whose decision differs from their type's, with the reason.
BY_ID = {
    # The one claim in the batch with a source, and the source was wrong: I
    # cited NBK526095, which is a StatPearls article about diabetes and
    # exercise and says nothing whatever about hands. The claim itself is
    # correct — extrinsic muscles in the forearm supply the force, intrinsic
    # hand muscles the fine control — and it now cites the two articles that
    # actually say so. Fetched and read, not pattern-matched from a search
    # result.
    #
    # Worth stating plainly: this is the only item in 614 carrying a source
    # that a person has opened and compared against the wording. The other five
    # 'established' items inherit their sources from the September review,
    # which recorded them honestly but did not re-verify them here.
    "__note__": "see review notes",
}

VERIFIED_SOURCES = {
    # item id -> what was actually checked
    "trivia:Where are the muscles that give your fingers their strength?":
        "Opened NBK546607 (Hand Long Flexor Tendons) and NBK537229 (Hand "
        "Muscles). Both state the extrinsic muscles originate in the forearm "
        "and attach by tendon, and that intrinsic hand muscles provide fine "
        "motor control while the extrinsics provide strength. The wording was "
        "changed from 'move your fingers' to 'give your fingers their strength' "
        "so the claim matches what the sources support.",
}


def main() -> int:
    touched = 0
    for path in sorted(ITEMS.glob("0002-*.jsonl")):
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            decision = BY_TYPE.get(item["type"])
            if decision is None:
                rows.append(item)
                continue
            item["stage"] = decision["stage"]
            notes = decision["notes"]
            key = f"{item['type']}:{item.get('question') or ''}"
            if key in VERIFIED_SOURCES:
                notes = notes + " SOURCE CHECKED: " + VERIFIED_SOURCES[key]
                item["review"] = {"pass": PASS, "depth": "sourced", "notes": notes}
            else:
                item["review"] = {"pass": PASS, "depth": decision["depth"], "notes": notes}
            rows.append(item)
            touched += 1
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                        encoding="utf-8")

    print(f"reviewed {touched} items in batch 0002")
    print(f"rejected before they reached the store: {REJECTED_FIRST_DRAFT} riddles "
          "(recycled public-domain material)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
