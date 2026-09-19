# StreakFit content store

Every discovery, question, joke and riddle the app can show, as line-oriented
JSON on disk rather than literals in `app.py`.

## Why not keep it in Python

479 items is 167 KB and 2,399 lines of application code. Five thousand is
roughly 1.7 MB and 25,000 lines, in the same file as the routes, the models and
the reward economy. That is not a style objection:

- a one-word fix to a joke shows up in the same diff as a migration;
- reviewing a batch of 200 means reading a 200-entry Python literal;
- the test suite imports the whole application to look at a fact;
- and nobody can tell you how many items exist without running the app.

JSONL solves all four. One item per line means a batch is a file, a review is a
diff, a corrupt entry costs one line instead of the file, and `wc -l` is an
honest inventory.

## Layout

```
content/
  SCHEMA.md            this file — the contract
  items/
    0001-migrated-facts.jsonl      one file per batch
    0001-migrated-trivia.jsonl
    0001-migrated-jokes.jsonl
    ...
```

A batch file is append-only in practice: corrections edit a line in place, and
new work goes in a new file. The batch number in the filename is the review
unit — `scripts/content/validate.py --batch 0002` checks exactly one.

## The item

```json
{
  "id": "SF-TRV-000123",
  "type": "trivia",
  "category": "Heart & Lungs",
  "question": "Why does the same hill get easier after a few weeks of walking it?",
  "options": ["...", "...", "...", "..."],
  "answer_index": 0,
  "explanation": "...",
  "min_age": 9,
  "confidence": "established",
  "sources": ["https://..."],
  "status": "accepted",
  "added": "2026-09-19",
  "batch": "0001-migrated"
}
```

### Fields

| field | applies to | notes |
|---|---|---|
| `id` | all | `SF-<TYPE>-<6 digits>`. **Stable and never reused**, including after a deletion — an id in a review note must not come back meaning something else. |
| `type` | all | `fact` · `trivia` · `joke` · `riddle` · `movement` · `experiment` · `rickie` |
| `category` | all | Free text, but drawn from a known set per type; the validator reports new ones rather than rejecting them. |
| `text` | all but `trivia` | The body. |
| `question` `options` `answer_index` `explanation` | `trivia` | Exactly 4 options, all distinct. |
| `min_age` | all | The youngest reader it suits. 9 is the product's floor; 13 and 16 exist for material that is fine but lands better older. |
| `confidence` | all | `established` · `simplified` · `contested` · `editorial` — see below. |
| `sources` | required unless `editorial` | URLs. A factual claim with no source cannot be `accepted`. |
| `status` | all | `accepted` · `pending` · `rejected`. **Only `accepted` items are served.** |
| `added` `batch` | all | Provenance. Which batch, when. |
| `notes` | optional | Why it was rejected, or what was checked. |

### The confidence field is the point

The previous content review found the real failure mode was not false claims so
much as **true claims stated more firmly than the evidence supports**. So the
store makes a claim's standing explicit:

- **`established`** — a fact with a source that agrees with it. "The gluteus
  maximus is the largest muscle."
- **`simplified`** — true at the level of detail given, with something left out
  on purpose. "Muscles can only pull." A specialist would add a caveat; a
  nine-year-old does not need one.
- **`contested`** — genuinely argued over in the field, and the text must say
  so. "Whether sleep flushes waste out of brain tissue."
- **`editorial`** — encouragement, humour, or a framing. Nothing to verify, and
  no source required. A joke is `editorial`. So is "small wins are worth
  noticing".

An item may not be `accepted` with `confidence: established` and no `sources`.
That single rule is what stops the library drifting back toward confident
nonsense as it grows.
