#!/usr/bin/env python3
"""Risk-based review queue: split a batch into lanes, hand reviewers packets,
apply their verdicts, and keep a ledger of every decision.

WHY THIS EXISTS. content/SCHEMA.md says an author clearing the validator's gates
on their own batch is not a review, and only `accepted` items reach a reader.
The library needs thousands of items. Those two facts together are the whole
problem: somebody has to independently judge thousands of items, and it cannot
be the person who wrote them, and it should not have to be the owner reading
every line.

So review is split by RISK, because "is this claim true" and "is this joke any
good" need different work:

  HIGH lane — fact, movement, and trivia items, and anything whose confidence is
    `established`, `simplified` or `contested`. A wrong health claim aimed at a
    nine-year-old is the failure this library exists to avoid. The reviewer must
    verify the claim, and an `established` item may not be accepted without a
    source that actually says what the item says.

  LOW lane — jokes, riddles and Rickie asides, which are `editorial`: there is
    nothing to verify. They still get an independent read for quality, safety,
    age-appropriateness and originality. Lighter, not absent.

SEPARATION. The reviewer is a different agent from the author and is given the
item and the rubric ONLY — never the author's rationale, never the intended
verdict, never "this batch is meant to be good". A reviewer who is told what the
answer should be is a second author.

WHAT IS RECORDED. Every decision lands in two places: the item's own `review`
block (who, how hard, what they checked), and an append-only ledger at
content/reviews/<batch>.jsonl holding the verdict, the reason, and — for
rejections and revisions — what specifically was wrong. Rejected items stay on
disk with their reason, because that reason is how the next batch avoids the
same mistake.

SPOT-CHECKS. `--spot-check` samples already-accepted items and queues them for a
second, independent reviewer who does not know they were accepted. A lane whose
sample fails is evidence about the reviewer, not just the items.

USAGE
    python scripts/content/review_queue.py --batch 0003 --emit      # make packets
    python scripts/content/review_queue.py --batch 0003 --apply verdicts.json
    python scripts/content/review_queue.py --batch 0003 --spot-check 12
    python scripts/content/review_queue.py --batch 0003 --status
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ITEMS = ROOT / "content" / "items"
LEDGER_DIR = ROOT / "content" / "reviews"
TODAY = date.today().isoformat()

HIGH_RISK_TYPES = {"fact", "movement", "trivia", "experiment"}
HIGH_RISK_CONFIDENCE = {"established", "simplified", "contested"}

VERDICTS = {"accept", "revise", "reject"}

HIGH_RUBRIC = """\
You are reviewing health and science content for StreakFit, a daily movement app
used by children from about nine years old and by adults and seniors.

You did NOT write these items. Your job is to judge them, not to improve them or
to be generous to them. An item you are unsure about is a `revise`, not an
`accept` — the cost of a wrong health claim reaching a child is not symmetric
with the cost of one more round of editing.

For each item decide `accept`, `revise` or `reject`, and give a reason.

TRUTH
  - Is the claim actually true? If `confidence` is `established`, there must be a
    source and the source must say what the item says; if it does not, reject or
    revise.
  - `simplified` means true at the level of detail given, with something left out
    on purpose. Check the simplification does not mislead. "Muscles can only
    pull" is fine. "Stretching prevents injury" is not.
  - `contested` means genuinely argued over, AND the text must say so.
  - A claim stated more firmly than the evidence supports is the specific failure
    this library has had before. Firmness is part of accuracy.

SAFETY
  - Nothing a child could copy and be hurt by, unless the text names the danger.
  - No breath-holding, no eyes-closed movement, no meal skipping, no
    weight/body-composition framing, no medical advice or diagnosis.
  - Nothing that shames, compares bodies, or treats a body as a thing to fix.

QUALITY (a true, safe, boring item is still a failure)
  - Is it worth a reader's attention? Does it teach something?
  - For trivia: are all four options plausible? Could somebody get it right
    WITHOUT knowing the answer — is the correct option the longest, the most
    specific, the only grammatical one, or are the distractors obvious jokes or
    bare dismissals ("nothing", "it's a myth")?
  - Does the explanation actually explain, or just restate the answer?
  - Is it age-appropriate at the stated `min_age`?
"""

LOW_RUBRIC = """\
You are reviewing jokes, riddles and short character lines for StreakFit, a daily
movement app used by children from about nine and by adults and seniors. The
character, Rickie, is a raccoon who is a good coach — dry, warm, self-deprecating,
never performing.

You did NOT write these. There is nothing factual to verify here, so your job is
quality, safety and originality. A weak joke is a real defect: it takes up the
one slot the reader gets that day.

For each item decide `accept`, `revise` or `reject`, and give a reason.

  - Is it actually funny, or at least warm? "Inoffensive" is not the bar.
  - Is it ORIGINAL? Recycled public-domain riddles and joke-book staples were
    rejected wholesale from an earlier batch. If you recognize it, reject it.
  - Is the humour ever at somebody's expense? It may only be at the raccoon's.
  - Is it solvable / gettable by a nine-year-old, without needing a word or
    reference they will not have?
  - Nothing about bodies, weight, appearance, illness, death or dumpster-diving
    as an actual health suggestion.
  - For riddles: is the answer unambiguous, and does it differ from the other
    riddles' answers in this batch?
"""


def load_batch(batch: str) -> list[tuple[Path, int, dict]]:
    out = []
    for path in sorted(ITEMS.glob(f"{batch}*.jsonl")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            if line.strip():
                out.append((path, n, json.loads(line)))
    if not out:
        raise SystemExit(f"no items found for batch {batch!r} in {ITEMS}")
    return out


def lane(item: dict) -> str:
    if item.get("type") in HIGH_RISK_TYPES or item.get("confidence") in HIGH_RISK_CONFIDENCE:
        return "high"
    return "low"


def write_back(rows: list[tuple[Path, int, dict]]) -> None:
    by_file: dict[Path, dict[int, dict]] = {}
    for path, n, item in rows:
        by_file.setdefault(path, {})[n] = item
    for path, edits in by_file.items():
        lines = path.read_text(encoding="utf-8").splitlines()
        out = []
        idx = 0
        for line in lines:
            if not line.strip():
                out.append(line)
                continue
            out.append(json.dumps(edits[idx], ensure_ascii=False)
                       if idx in edits else line)
            idx += 1
        path.write_text("\n".join(out) + "\n", encoding="utf-8")


def ledger_append(batch: str, records: list[dict]) -> Path:
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    path = LEDGER_DIR / f"{batch}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


def cmd_emit(batch: str, stage: str, limit: int | None) -> int:
    rows = [r for r in load_batch(batch) if r[2].get("stage") == stage]
    if limit:
        rows = rows[:limit]
    packets = {"high": [], "low": []}
    for _p, _n, item in rows:
        packets[lane(item)].append(
            {k: v for k, v in item.items()
             # The reviewer sees the item and nothing about how it was judged
             # before. Handing over `review` or `stage` invites agreement.
             if k not in ("review", "stage", "batch", "added")})
    outdir = LEDGER_DIR / "packets"
    outdir.mkdir(parents=True, exist_ok=True)
    for name, items in packets.items():
        if not items:
            continue
        path = outdir / f"{batch}-{name}.json"
        path.write_text(json.dumps(
            {"lane": name, "rubric": HIGH_RUBRIC if name == "high" else LOW_RUBRIC,
             "items": items}, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"{name:5s} lane: {len(items):4d} items -> {path.relative_to(ROOT)}")
    if not any(packets.values()):
        print(f"nothing at stage {stage!r} in batch {batch}")
    return 0


def cmd_apply(batch: str, verdict_file: Path, reviewer: str, depth: str) -> int:
    verdicts = json.loads(verdict_file.read_text(encoding="utf-8"))
    if isinstance(verdicts, dict):
        verdicts = verdicts.get("verdicts", [])
    by_id = {v["id"]: v for v in verdicts}
    bad = [v for v in verdicts if v.get("verdict") not in VERDICTS]
    if bad:
        raise SystemExit(f"{len(bad)} verdicts are not accept/revise/reject: "
                         f"{[b.get('id') for b in bad][:5]}")
    missing = [v for v in verdicts
               if v.get("verdict") != "accept" and not (v.get("reason") or "").strip()]
    if missing:
        raise SystemExit(f"{len(missing)} non-accept verdicts have no reason recorded; "
                         "a rejection without a reason teaches the next batch nothing")

    rows = load_batch(batch)
    changed, records = [], []
    counts = {"accept": 0, "revise": 0, "reject": 0, "unjudged": 0}
    for path, n, item in rows:
        v = by_id.get(item["id"])
        if not v:
            counts["unjudged"] += 1
            continue
        decision = v["verdict"]
        counts[decision] += 1
        # An `established` claim without a source cannot be accepted, whatever a
        # reviewer said. The schema rule outranks the verdict.
        if decision == "accept" and item.get("confidence") == "established" \
                and not item.get("sources"):
            decision = "revise"
            v = dict(v, reason="reviewer accepted, but `established` with no sources "
                              "— schema forbids serving it")
            counts["accept"] -= 1
            counts["revise"] += 1
        item["stage"] = {"accept": "accepted", "revise": "revise",
                         "reject": "rejected"}[decision]
        item["review"] = {
            "pass": f"{TODAY}-{batch}-{lane(item)}-lane",
            "depth": depth,
            "reviewer": reviewer,
            "notes": v.get("reason", ""),
        }
        changed.append((path, n, item))
        records.append({
            "at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "batch": batch, "id": item["id"], "lane": lane(item),
            "verdict": decision, "reviewer": reviewer, "depth": depth,
            "reason": v.get("reason", ""), "sources_checked": v.get("sources_checked", []),
        })
    write_back(changed)
    path = ledger_append(batch, records)
    print(f"applied {len(records)} verdicts  "
          f"accepted={counts['accept']} revise={counts['revise']} "
          f"rejected={counts['reject']} unjudged={counts['unjudged']}")
    print(f"ledger: {path.relative_to(ROOT)}")
    return 0


def cmd_spot_check(batch: str, n: int, seed: int) -> int:
    """Sample ACCEPTED items for a second, independent look.

    The sample is written without its verdict or review block, so the second
    reviewer cannot tell these were already accepted — which is the only way the
    check says anything about the first reviewer.
    """
    rows = [r for r in load_batch(batch) if r[2].get("stage") == "accepted"]
    if not rows:
        raise SystemExit(f"batch {batch} has no accepted items to spot-check")
    rng = random.Random(seed)
    sample = rng.sample(rows, min(n, len(rows)))
    outdir = LEDGER_DIR / "packets"
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"{batch}-spotcheck.json"
    path.write_text(json.dumps({
        "lane": "spot-check",
        "rubric": HIGH_RUBRIC,
        "note": "These are a random sample from a finished batch. You are not "
                "told what was decided about them. Judge them cold.",
        "items": [{k: v for k, v in item.items()
                   if k not in ("review", "stage", "batch", "added")}
                  for _p, _n, item in sample],
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"spot-check sample of {len(sample)} (seed {seed}) -> {path.relative_to(ROOT)}")
    print(f"  drawn from {len(rows)} accepted items in batch {batch}")
    return 0


def cmd_status(batch: str) -> int:
    rows = load_batch(batch)
    stages: dict[str, int] = {}
    lanes: dict[str, int] = {}
    for _p, _n, item in rows:
        stages[item.get("stage")] = stages.get(item.get("stage"), 0) + 1
        lanes[lane(item)] = lanes.get(lane(item), 0) + 1
    print(f"batch {batch}: {len(rows)} items")
    print(f"  by stage : {stages}")
    print(f"  by lane  : {lanes}")
    ledger = LEDGER_DIR / f"{batch}.jsonl"
    if ledger.exists():
        recs = [json.loads(x) for x in ledger.read_text().splitlines() if x.strip()]
        print(f"  ledger   : {len(recs)} recorded decisions")
        reasons = [r for r in recs if r["verdict"] != "accept"]
        for r in reasons[:8]:
            print(f"     {r['verdict']:7s} {r['id']}  {r['reason'][:80]}")
    else:
        print("  ledger   : none yet")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--emit", action="store_true")
    ap.add_argument("--stage", default="generated")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--apply", type=Path)
    ap.add_argument("--reviewer", default="unnamed-reviewer")
    ap.add_argument("--depth", default="read", choices=("sourced", "read", "tested"))
    ap.add_argument("--spot-check", type=int)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()

    if a.emit:
        return cmd_emit(a.batch, a.stage, a.limit)
    if a.apply:
        return cmd_apply(a.batch, a.apply, a.reviewer, a.depth)
    if a.spot_check:
        return cmd_spot_check(a.batch, a.spot_check, a.seed)
    return cmd_status(a.batch)


if __name__ == "__main__":
    raise SystemExit(main())
