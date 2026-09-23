# Rickie the Raccoon, in 3D — project plan

**Status: BACKLOG. Approved to plan, not approved to build.** No 3D engine, no
asset purchase, no change to the existing 2D character. The concept image from
the owner's ChatGPT conversation is visual inspiration, not a production asset
and not an animation-ready model.

**Goal:** a real, interactive 3D raccoon who can walk, wave, react to taps,
celebrate, demonstrate exercises, and eventually approach the screen and knock
on the glass — in the spirit of Talking Tom, and bounded by the
[Rickie Character Bible](docs/rickie_character_bible.md): a supportive fitness
companion, never an intrusive notification system.

---

## Stages (owner-defined)

1. Establish Rickie's final character design from the approved visual direction.
2. Create or acquire an original, animation-ready 3D raccoon model.
3. Build a standalone browser prototype, **separate from production StreakFit**.
4. Test walking, waving, tap reactions, and screen-knocking.
5. Evaluate performance, accessibility, and mobile compatibility.
6. Integrate into StreakFit **only after the prototype is approved.**

Stage 3 is the first thing needing a green light. Nothing before it touches
this repository.

---

## Owner requirement: the 3D experience must be OPTIONAL

Three user-selectable display modes:

| Mode | Behaviour |
|---|---|
| **Full** | Roams, interacts with interface elements, celebrates, responds to taps. |
| **Quiet** | Stays in one designated place. Interacts **only** when the user starts it. |
| **Hidden** | No on-screen character, no character animation, no character sounds. Private AI coaching and every other StreakFit feature remain fully available. |

Plus:

- Separate controls for **character sounds** and for **attention-getting
  behaviour** (including screen-knocking), independent of the display mode.
- Respect `prefers-reduced-motion`.
- Remember the choices between visits.
- **Apply saved preferences BEFORE displaying or loading the 3D character.**
- Never obstruct essential controls or intercept unrelated taps.
- An easy way to restore Rickie after hiding him.
- Hidden mode must not download or render 3D assets at all.

---

## What already exists, and why it does not satisfy the above

This is the part worth reading before designing anything. StreakFit already has
**two** independent Rickie controls, on different axes, stored in different
places, and neither one can express "Hidden".

### 1. `user.rickie_mode` — server-side, and it is about CHATTINESS

`VALID_RICKIE_MODES = {'full', 'quiet', 'minimal'}` (`app.py:611`), a column on
`user` defaulting to `'full'`, editable via `PATCH /api/me`, surfaced in
settings as *"How chatty Rickie is."* The help text says outright: *"He never
stops being available — this only changes how often he speaks up on his own."*

`'quiet'` gates reaction toasts to milestone moments; `'minimal'` drops jokes
and flavour lines. **None of the three removes the character from the screen.**

**The name collision is the trap.** The owner's Quiet means *stationary and
user-initiated*; the existing `quiet` means *speaks up less often*. A user who
has already chosen `quiet` for chattiness has said nothing about whether they
want a raccoon walking around. Reusing the value would silently reinterpret a
preference they already expressed.

### 2. `rickie_roam_paused` — localStorage, and it is about MOTION

`static/rickie-roam.js` keeps its own pause flag in `localStorage`
(`setPaused`, read at mount). It is **not** on the server, so it does not
follow a user between devices, and `localStorage` can throw or return empty in
private mode — the engine wraps it, correctly, but that means the fallback is
"unpaused", i.e. the more intrusive state.

It is also not exposed in settings. A user cannot find it.

### 3. Reduced motion is already honoured — partially

`reducedMotion()` reads `prefers-reduced-motion: reduce` and pauses roaming
while keeping him present and reactive. That is the right instinct and a good
precedent. It is a **runtime check with no user override**, and it only
governs travel, not sound (there is no sound today) and not attention-getting.

### 4. The "apply before display" requirement is already violated today

`RickieRoam.mount()` is called from `showView('dashboard')`
(`static/app.js:4135`). `currentUser` — which carries `rickie_mode` — is not
populated until `/api/me` resolves (`static/app.js:4409`). The character is
therefore mounted **before** the server-side preference is known.

Today that is invisible, because no value of `rickie_mode` hides him. The
moment "Hidden" exists, this ordering becomes a **flash of unwanted raccoon**
on every load for exactly the users who asked not to see one — and in 3D it
would also mean downloading a model they will never look at, which breaks the
"must not load unnecessary assets" requirement at the same time.

**This is the single most important thing in this document.** The requirement
is not a checkbox on the 3D work; it is a change to how preferences are
delivered, and it has to be solved before a model exists to load.

---

## Design requirements this produces

### R1. One presence axis, reconciled with the existing chattiness axis

Do **not** stack a third control. The plan is to introduce presence as its own
setting — working name `rickie_presence` — with values `full` / `quiet` /
`hidden`, and to leave `rickie_mode` doing what it already does and says.

Migration position for existing users: `rickie_presence` defaults to `full`,
because that is what every current user already experiences. A user with
`rickie_mode='minimal'` has asked for less *talking*, not less *raccoon*, and
must not be silently migrated into a different choice. `rickie_roam_paused=1`
in localStorage is the one signal that genuinely means "stop moving", and is
the only reasonable candidate for seeding `quiet` — per-device, best-effort,
and never in a way that loses data if it is absent.

### R2. Preferences must arrive before the character does

Options to evaluate at prototype time, in rough order of preference:

1. **Server-rendered into the document.** The page already authenticates; emit
   the presence preference into the initial HTML so it is known at first
   paint, before any script decides whether to fetch a model.
2. **localStorage as a first-paint cache, server as the authority.** Read the
   cached value synchronously to decide whether to load anything; reconcile
   when `/api/me` resolves. Must fail toward *hidden*, not toward *shown* —
   the opposite of today's roam-pause fallback.
3. Block the mount on `/api/me`. Simplest, and pays for correctness with a
   visible delay on every load. Listed for completeness; probably wrong.

Whichever is chosen, the asset fetch must sit **behind** the decision, not
beside it.

### R3. Sounds and attention-getting are separate switches

Three independent controls, not one enum:

- presence: `full` / `quiet` / `hidden`
- character sounds: on / off — **default off.** StreakFit has never made a
  sound; a fitness app for children that starts talking unprompted is a
  different product, and an unexpected noise is the fastest way to get an app
  deleted from a family's phone.
- attention-getting (including screen-knocking): on / off — **default off**,
  for the same reason and because the Character Bible's whole position is that
  Rickie is a companion, not a notification system. Screen-knocking is
  charming when invited and manipulative when not.

Hidden implies no sound and no attention-getting regardless of those switches.
The switches remain independently meaningful in Full and Quiet.

### R4. Reduced motion, extended

`prefers-reduced-motion: reduce` must continue to be honoured, and should now
mean: no roaming, no celebration animation, no knocking. It should **not** by
itself mean Hidden — a still Rickie is not an accessibility problem, and
removing him entirely is a bigger change than the user asked for. An explicit
user choice always outranks the media query in the direction of *less*, never
*more*: someone who set Full with reduced-motion on gets a stationary Rickie,
not a roaming one.

### R5. Obstruction and tap interception — the hard one

**The current safety guarantee does not survive into 3D.** Today Rickie cannot
block anything because `.rickie-roam-band` and `.rickie-roam` both carry
`pointer-events: none`; a tap passes through him to whatever is underneath.
That is a structural property, not a behaviour, and it is asserted in
`tests/test_rickie_size.py`.

A character who *reacts to taps* must accept pointer events. The moment he
does, that guarantee is gone and has to be rebuilt deliberately:

- hit-testing confined to the character's own silhouette, never a bounding box
  or a full-screen canvas;
- a canvas or WebGL surface that is `pointer-events: none` everywhere except
  that silhouette;
- the existing clear-spot search (`somewhereClear`, `occupiedRects`) carried
  forward, because it is what keeps him off text;
- and the existing rule that he is **never the only way to reach anything** —
  he is `aria-hidden` today precisely so a screen reader loses nothing.

Two bugs from the 2D character are worth carrying into the prototype as known
traps, both found in Sept 2026:

- he was placed at fixed default coordinates and shown **before** page content
  rendered, standing on the mission for ~600ms of every phone load;
- the check written to catch that reported clear throughout, because it
  teleported him to coordinates the engine *nominated* and never observed where
  he actually was.

The prototype's obstruction test must measure **rendered position during a real
load**, not the engine's own model of the scene. See
`_watch_him_through_a_page_load` in `scripts/uicheck.py` for the shape of it.

### R6. Restoring him after hiding

Hidden must not be a one-way door, and must not require a user to remember
where the setting was. At minimum: the same settings row that hid him, plus a
discoverable entry point that does not depend on the character existing. The
Coach panel — which stays available in every mode — is the obvious host.

### R7. Hidden must cost nothing

No model download, no texture fetch, no WebGL context, no animation loop, no
audio decode. Verified by observation, not by assertion: a Hidden session's
network log must contain zero character assets, and the check belongs in the
prototype's own harness from stage 3 rather than being retrofitted at stage 6.

---

## Open questions for the owner

1. **Does Quiet mean visible-but-still, or visible-only-when-summoned?** "Stays
   in a designated location" implies the former; "only interacts when the user
   initiates it" could mean either. The difference decides whether a Quiet
   Rickie is on screen all the time.
2. **Where is Quiet's designated location?** A fixed corner is predictable and
   will sometimes sit on content at 320px — the same collision problem, minus
   the ability to walk out of it.
3. **Do the three modes replace the existing chattiness setting in the UI, or
   sit beside it?** Two Rickie dropdowns in settings needs a good reason.
4. Does Hidden also suppress the 2D expression avatars in the greeting, Journey
   card and Coach panel, or only the 3D character? These are different things
   and the requirement says "on-screen Rickie", which could mean either.

---

## Explicitly NOT in scope here

Nothing in this document authorises implementation. No 3D engine or rendering
library is to be added, no assets bought, and the existing 2D roaming Rickie is
to be left exactly as he is until the stage-3 prototype is approved.
