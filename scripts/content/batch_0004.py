#!/usr/bin/env python3
"""Batch 0004 — assembled from items written by three separate author agents.

Batch 0003 was written by one author (me) and reviewed by others, and 47% of it
survived. The recurring failures were an author's failures, not a reviewer's:
the key was the only hedged option, the stem asserted the premise that needed
the evidence, firmness outran the evidence, and two citations were fabricated.

So the authoring moved too. Three agents wrote 30 items each, in separate
category sets, each briefed on those exact failure modes and told to prefer
`simplified` (which needs no source) over `established` unless they had actually
resolved a citation. This script only assembles what they produced: it assigns
ids, stamps provenance, and runs the self-gates. It does not edit their items,
because an assembler quietly rewriting an author's work would put the two roles
back in one place.

Everything lands at `generated`. Per content/SCHEMA.md an author clearing the
gates on their own batch is not a review, and nothing here has been reviewed.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gates  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "content" / "items"
AUTHORED = ROOT / "content" / "reviews"
STEMS = AUTHORED / "existing-stems.txt"
BATCH = "0004-authored"
TODAY = date.today().isoformat()

SOURCES = [("A", "authored-A.json"), ("B", "authored-B.json"), ("C", "authored-C.json")]


def next_id_start() -> int:
    highest = 0
    for path in OUT.glob("*.jsonl"):
        if path.name.startswith(BATCH):
            continue                      # idempotent: never burn ids on a re-run
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r'.*"id":\s*"SF-TRV-(\d{6})"', line)
            if m:
                highest = max(highest, int(m.group(1)))
    return highest + 1


def load_authored():
    out = []
    for tag, name in SOURCES:
        path = AUTHORED / name
        if not path.exists():
            raise SystemExit(f"missing {path} — author {tag} did not deliver")
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data["items"] if isinstance(data, dict) else data
        for it in items:
            it["_author"] = f"agent-{tag}"
        out += items
        print(f"  author {tag}: {len(items):3d} items from {name}")
    return out


def build():
    authored = load_authored()
    start = next_id_start()
    items = []
    for offset, a in enumerate(authored):
        conf = a.get("confidence", "simplified")
        srcs = list(a.get("sources") or [])
        # The schema rule, applied at assembly rather than left to a reviewer to
        # notice: `established` without a source cannot be served, so it is not
        # allowed to enter the batch claiming to be one.
        if conf == "established" and not srcs:
            conf = "simplified"
        items.append({
            "id": f"SF-TRV-{start + offset:06d}",
            "type": "trivia",
            "category": a["category"],
            "question": a["question"].strip(),
            "options": [o.strip() for o in a["options"]],
            "answer_index": int(a["answer_index"]),
            "explanation": a["explanation"].strip(),
            "min_age": int(a.get("min_age", 9)),
            "confidence": conf,
            "sources": srcs,
            "added": TODAY,
            "batch": BATCH,
            "author": a["_author"],
            "stage": "generated",
        })
    return items


def main() -> int:
    items = build()
    path = OUT / f"{BATCH}-trivia.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"\nwrote {len(items)} items to {path.relative_to(ROOT)}")
    print(f"  id range      : {items[0]['id']} .. {items[-1]['id']}")
    existing = STEMS.read_text(encoding="utf-8").splitlines() if STEMS.exists() else []
    existing = [re.sub(r"^\[[^\]]*\]\s*", "", s) for s in existing]
    code = gates.report(items, existing)
    print("  stage         : generated — NOT served, NOT reviewed")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
