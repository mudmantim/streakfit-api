# Team photos and StreakFit filters

Private photo sharing inside an existing team: take or choose a photo, preview
it, put a StreakFit filter on it, add a caption, send it to the team you are
already in. Built because the target user asked for it by name.

There is no public gallery, no discovery, no way to send a photo to anyone you
are not already on a team with, and no way to reach a photo without a token
that belongs to that team. Those are not incidental — they are the feature.

## Where the bytes live, and why

**Today: in Postgres, as `team_photo.image_data`.**

That is not where image bytes belong at scale, and it is the correct call here:

- **Disk is not an option at all.** Render's web filesystem is ephemeral and is
  wiped on every deploy, so anything written to it disappears the next time the
  service ships. A persistent disk is a paid add-on and also conflicts with
  zero-downtime deploys on a single service.
- **Object storage is a new paid service**, which is an owner decision, not an
  engineering one. Nothing here has been signed up for.
- **The working set is small.** The client composites and resizes before
  uploading, so a row is roughly 200 KB. A four-person family sharing a photo
  each per day, with 30-day retention, is about 25 MB steady state.

Three independent controls bound the growth:

| Control | Value | What it bounds |
|---|---|---|
| `PHOTO_MAX_UPLOAD_BYTES` | 2 MB | any single upload |
| `PHOTO_TEAM_QUOTA_BYTES` | 150 MB | live photos per team |
| `PHOTO_RETENTION_DAYS` | 30 days | how long any photo is served |
| Rate limits | 8/min, 40/hour per user | how fast photos arrive |

Rate limits alone would not have been enough: sustained uploads at the limit
reach gigabytes. The per-team quota is what actually caps storage, and it
returns a clear `507` rather than silently filling a database.

### When to move off Postgres, and where to

Move when any of these becomes true: retention needs to be much longer than 30
days, teams get large, or photo volume makes the database's size or backup time
uncomfortable. `_photo_bytes()` is the single read path and the write is beside
it, so the change is contained.

Options, in the order they are worth considering — **none of these are set up,
and each needs an owner decision because each costs money:**

1. **Cloudflare R2** — S3-compatible, no egress fees, ~$0.015/GB/month. At this
   product's scale the bill is effectively a rounding error. Needs an account,
   a bucket, and two credentials in the environment. Photos would be served via
   short-lived pre-signed URLs *generated after the same membership check the
   route does today*, so the authorization model does not change.
2. **Backblaze B2** — cheapest per GB, same shape of integration, slightly
   worse tooling.
3. **AWS S3** — the obvious one, and the one with egress charges that actually
   matter if photos get popular.
4. **A Render persistent disk** — avoid. It pins the service to one instance
   and complicates deploys, for no gain over object storage.

A migration would backfill existing rows by streaming `image_data` out to the
bucket and nulling the column; the schema already separates metadata from
bytes, so nothing else has to change.

## Safety and privacy

This is used by children, so the defaults are the strict ones.

- **Authorization on every read.** `GET .../photos/<public_id>` re-checks team
  membership on each request. There is no signed-URL or token-in-query path:
  the browser fetches with the normal `Authorization` header and renders the
  result as a blob, so a photo URL appearing in a log, a referrer header or
  someone's history is worth nothing by itself.
- **Opaque ids.** A random 32-character id, not the row's primary key, so a
  member cannot count how many photos exist or probe for neighbours.
- **A photo from another team is a 404, not a 403** — an id must not be able to
  confirm that a photo exists somewhere the caller cannot see.
- **Metadata is stripped server-side.** `sanitize_jpeg()` rebuilds every upload
  from its own parse, keeping only the segments needed to decode the image and
  dropping every `APPn` and comment marker — EXIF (GPS coordinates, timestamps,
  device model, and on some phones a thumbnail of the *original, unfiltered*
  frame), ICC, IPTC and free-text comments. The client's canvas already drops
  EXIF as a side effect of compositing; that is a convenience, not a safety
  layer, because the client is not trusted.
- **JPEG only**, validated by parsing rather than by extension or declared
  content type. A PNG, an SVG, a ZIP, or HTML wearing JPEG magic bytes are all
  rejected.
- **`Cache-Control: private, no-store`** so a shared cache never holds a team's
  photo and the next person on a family tablet does not find one.
- **Deletion removes the bytes**, not just a flag: `image_data` is nulled in the
  same transaction. The sender can delete their own photo; the team creator can
  delete any, which is the existing narrow creator safety exception (remove a
  member, rotate the code) rather than a new moderator role.
- **The team's history outlives the pixels.** A `photo_shared` moment stays in
  team history permanently while the image expires, so a family's shared story
  does not develop holes because storage has a budget.

### What users are told

The composer says, in full: *"Only your team can open this. It disappears from
the app after 30 days, and you can delete it sooner — but anyone who can see it
can screenshot it."*

That last clause is deliberate and must not be softened. Screenshots cannot be
prevented, and a product used by children must not imply that a photo sent to a
team is recoverable or unshareable.

### Still open

Age verification, parental consent and COPPA-equivalent compliance remain
undesigned, as flagged in `TEAM_SYSTEM_BASELINE.md`. Photo sharing raises the
stakes on that, and it is a legal and product-policy decision rather than a
schema one.

## Filters

The catalog lives in `PHOTO_FILTERS` in `app.py` and carries its own render
spec, so the client is a generic renderer rather than a second copy of the list.
**Adding a filter is adding a dict.** Only a genuinely new kind of effect needs
client code.

Primitives the client implements:

| Primitive | What it does |
|---|---|
| `tint` | a CSS filter string applied while drawing the photo |
| `overlays` | images placed by anchor, scale, rotation and opacity |
| `frame` | an inset border drawn as a two-stop gradient |
| `ribbon` | a text banner across the top or bottom |
| `confetti` | scattered glyphs, placed deterministically |

Composition happens in the browser: the server never pays to process an image,
the upload is one small finished JPEG instead of a full-resolution original, and
a canvas round-trip drops EXIF on the way.

`test_filter_render_specs_only_use_known_primitives` fails if a filter is added
using a primitive the client does not implement — otherwise it would silently
render as nothing.

### Unlocks

| Kind | Example | Evaluated |
|---|---|---|
| `free` | Rickie Photobomb, Campfire | always |
| `missions` | Mission Complete (1), Team Challenge (5) | live, from stats |
| `streak` | On Fire (3-day) | live, from stats |
| `level` | Acorn Shower (3), Rickie's Proud (5) | live, from stats |
| `milestone` | First Mission | live, from stats |
| `acorns` | Golden Hour (20), Frosty (30), Goofy Specs (15) | stored purchase |

Earned unlocks are evaluated live rather than stored, so they can never drift
from the thing that earned them and a new earned filter needs no backfill. Only
purchases are persisted.

**Acorns finally have a sink.** `user.acorns_total` stays lifetime-*earned* —
the `acorns_100` milestone depends on that meaning — and `user.acorns_spent`
tracks spending, so the spendable balance is earned minus spent.

**What this deliberately is not:** you choose a named filter, you see the price
before you tap, and you get exactly that filter. No randomised or chance-based
unlocks, no bundles, no loot boxes, and no way to buy acorns with money — the
only source of acorns is showing up. `test_there_is_no_way_to_acquire_acorns_except_by_earning_them`
pins that.

## The mobile experience

The camera button uses `<input type="file" accept="image/*" capture="environment">`,
which opens the camera directly on a phone while still leaving "choose an
existing photo" available underneath. The composer is a bottom sheet sized for a
thumb, with a horizontally scrolling filter strip and 44px targets throughout.

The team panel is split into **Campfire** and **Photos & Chat** tabs. Before
that split the thread was a 160px window at the bottom of an 877px sheet, which
is not somewhere anyone looks at a picture; it is now around 464px. This is the
Campfire / Team Chat separation `TEAM_UI_BASELINE.md` already described, done
with the least machinery that works.

## Verifying it

- `pytest tests/test_team_photos.py` — 43 tests, weighted towards what must not
  happen.
- `python scripts/verification/photos.py` — end-to-end against a running server,
  including that a non-member gets 403 and an anonymous request gets 401. Safe
  against production: it uploads a few hundred bytes into a throwaway team and
  deletes it on the way out.
- `make uicheck` — drives the real composer in a browser: the preview paints,
  picking a filter visibly changes the image, and the family sees the result.
