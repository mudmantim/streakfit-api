"""Loads StreakFit's content from the store on disk.

The facts, questions and jokes used to live as Python literals — 539 items,
167 KB, 2,399 lines, sitting in the same files as the routes and the reward
economy. The product needs thousands, and at that size literals stop being a
style question: a typo fix lands in the same diff as a migration, a review
means reading a Python list, and nobody can count the library without importing
the application.

They now live in `content/items/*.jsonl`, one item per line, with stable ids,
provenance and an explicit confidence level. See `content/SCHEMA.md`.

This module reads that store and exposes it in the shapes the application
already expects, so nothing else had to change. It is the only place that knows
the store's layout.
"""
from __future__ import annotations

import json
from pathlib import Path

CONTENT_DIR = Path(__file__).resolve().parent / "content" / "items"

# Only these reach a reader.
#
# `validated` is the interesting one: it means the structure is sound and NOBODY
# HAS READ IT. That is a real state a lot of content sits in, and serving it
# because it passed a regex is how a library of five thousand unreviewed lines
# ends up described as finished. Rejected and revise rows stay on disk with
# their reason, because that reason is how the next batch avoids the mistake.
SERVED_STAGE = "accepted"


def _load_items() -> list[dict]:
    """Every item in the store, in a stable order.

    Sorted by filename then by line, so two processes build identical libraries
    and a user's "insight for today" does not change because a file system
    listed a directory differently.
    """
    if not CONTENT_DIR.is_dir():
        raise RuntimeError(
            f"Content store missing at {CONTENT_DIR}. StreakFit cannot start without it — "
            "serving an empty library would look like a working app with nothing to say."
        )
    items: list[dict] = []
    for path in sorted(CONTENT_DIR.glob("*.jsonl")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as exc:
                # Fail loudly. A skipped line is a fact that silently stops
                # existing, and the library is big enough that nobody notices.
                raise RuntimeError(f"{path.name} line {number} is not valid JSON: {exc}") from exc
    if not items:
        raise RuntimeError(f"Content store at {CONTENT_DIR} is empty.")
    return items


ALL_ITEMS = _load_items()
SERVED = [i for i in ALL_ITEMS if i.get("stage") == SERVED_STAGE]

# ── The shapes the application already uses ─────────────────────────────────
#
# Kept exactly as they were. The store is a better place to put the content, not
# a reason to rewrite every consumer of it on the same day.

# The daily discovery slot. Facts and movement discoveries were the whole of it;
# riddles, mini-experiments and Rickie's own asides now rotate through the same
# place rather than needing a surface of their own. One slot, more kinds of
# thing in it — which is what stops it feeling like the same card every day
# without adding another card to the screen.
#
# `type` is carried through so the UI can render a riddle as a riddle. Nothing
# reads it as a category; the category field is still the category.
_DISCOVERY_TYPES = ("fact", "movement", "riddle", "experiment", "rickie")

INSIGHT_LIBRARY = [
    {"text": i["text"], "category": i["category"], "type": i["type"],
     "min_age": i.get("min_age", 9)}
    for i in SERVED if i["type"] in _DISCOVERY_TYPES
]

BRAIN_BOOST_LIBRARY = [
    {"question": i["question"], "options": list(i["options"]),
     "correct_index": i["answer_index"], "explanation": i["explanation"],
     "category": i["category"]}
    for i in SERVED if i["type"] == "trivia"
]

RICKIE_JOKES = [i["text"] for i in SERVED if i["type"] == "joke"]

# Id lookup, so a review note or a bug report can name one item and anybody can
# find it. The application does not need this yet; a person does.
BY_ID = {i["id"]: i for i in ALL_ITEMS}


def counts() -> dict:
    """By stage, type and confidence — reported separately on purpose, because
    "we generated 5,000", "5,000 passed validation" and "5,000 are in the
    product" are three different claims."""
    out: dict = {"total": len(ALL_ITEMS), "by_stage": {}, "by_type": {}, "by_confidence": {}}
    for item in ALL_ITEMS:
        for field, key in (("stage", "by_stage"), ("type", "by_type"),
                           ("confidence", "by_confidence")):
            bucket = out[key]
            bucket[item.get(field)] = bucket.get(item.get(field), 0) + 1
    return out


# Backwards compatibility: these names were imported by app.py when the content
# lived in two places. Both now come from the same store.
EXTRA_INSIGHTS: list[dict] = []
EXTRA_BRAIN_BOOST: list[dict] = []
