#!/usr/bin/env python3
"""Make each item's recorded review say what actually happened to it.

Two corrections, both downgrades. Neither changes a word of content.

1. `depth: "sourced"` means "the claim was looked up and the source says what
   the item says". 416 of 440 served items carried it with **zero sources**,
   and the review records show why: the reviewers wrote `"depth": "sourced"`
   next to `"sources_checked": []`, citing bodies of literature by name without
   checking one. That is `read` — a reviewer judged it — and calling it
   `sourced` overstates the only field that records how hard anybody looked.

   The content is not being quarantined for this. Nothing here shows a claim to
   be wrong; the label is wrong, and 95% of the library is not removed over a
   labelling error. The `established`-needs-sources gate, which is the one that
   guards claims, is intact and all 8 served `established` items have sources.

2. `review.by` is added, because nothing in the store distinguished a review
   done by an agent from one done by a person. Every review to date was an
   agent lane (`rereview-high-01`, `*-low-lane`). No human has reviewed this
   library, and until the field existed there was no way to say so in the data
   rather than in a paragraph somebody has to remember.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ITEMS = ROOT / "content" / "items"
REVIEWS = ROOT / "content" / "reviews"


def sources_actually_checked() -> dict[str, int]:
    """id -> how many sources the review record says were checked."""
    checked: dict[str, int] = {}
    for path in sorted(REVIEWS.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" in rec:
                checked[rec["id"]] = max(checked.get(rec["id"], 0),
                                         len(rec.get("sources_checked") or []))
    return checked


def main() -> int:
    checked = sources_actually_checked()
    moved = Counter()
    for path in sorted(ITEMS.glob("*.jsonl")):
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            review = item.get("review") or {}

            # Every review so far was an agent lane. Recorded as data, not prose.
            if review.get("pass"):
                review.setdefault("by", "ai-agent")
            else:
                review.setdefault("by", None)

            if review.get("depth") == "sourced":
                has = bool(item.get("sources")) or checked.get(item["id"], 0) > 0
                if not has:
                    review["depth"] = "read"
                    note = review.get("notes") or ""
                    review["notes"] = (
                        note + " DEPTH CORRECTED sourced->read: the review record "
                        "checked no sources.").strip()
                    moved[item.get("stage")] += 1
            item["review"] = review
            rows.append(item)
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                        encoding="utf-8")

    total = sum(moved.values())
    print(f"depth corrected sourced -> read on {total} items")
    for stage, n in moved.most_common():
        print(f"  {stage}: {n}")
    print("review.by set on every item")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
