# Rickie the Raccoon, in 3D — project plan

**Status: BACKLOG. Approved to plan, not approved to build.** No 3D engine, no
asset purchase, no change to the existing 2D character. The concept image from
the owner's ChatGPT conversation is visual inspiration, not a production asset
and not an animation-ready model.

**Goal:** a real, interactive 3D raccoon who can walk, wave, react to taps,
celebrate, demonstrate exercises, move **in front of and behind** interface
elements, peek around cards, climb onto panels, and eventually approach the
screen and knock on the glass — in the spirit of Talking Tom, and bounded by
the [Rickie Character Bible](docs/rickie_character_bible.md): a supportive
fitness companion, never an intrusive notification system.

Those capabilities are the point of the project and are not negotiable down to
a static sprite. They are also where every hard problem in this document comes
from: a character that goes *in front of* things has to be stopped from going
in front of the wrong things, and one that climbs onto a panel has to know
where the panel is.

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
| **Full** | Roams freely. Moves in front of and behind interface elements, peeks around cards, climbs onto panels, celebrates, demonstrates exercises, responds to taps. |
| **Quiet** | **Visible but stationary**, in the user's chosen corner. No roaming, no knocking, no climbing, no peeking, no self-initiated attention-getting of any kind. **Still tappable** — he reacts when the user starts it. |
| **Hidden** | The roaming character is not rendered and its assets are not loaded. The existing small Rickie images and expression avatars **stay** — greeting, Journey card, Coach panel and anywhere else they appear today. Private AI coaching and every other StreakFit feature remain fully available. |

**Quiet is visible-but-still, not visible-on-demand** (owner decision, Sept
2026). He is on screen the whole time; what he stops doing is moving and
asking for attention. The distinction that matters is not "does he animate"
but **who started it**: a tap the user chose to make may be answered with a
wave or a celebration, because that is a reply, not an interruption.

Plus:

- Separate controls for **character sounds** and for **attention-getting
  behaviour** (including screen-knocking), independent of the display mode.
  Both **default OFF**.
- A **user-selectable corner** for Quiet, remembered between visits, with a
  safe alternative whenever that corner would cover something essential.
- One consolidated Rickie settings panel holding all of it, with display mode
  and chattiness kept as **independent** preferences.
- Respect `prefers-reduced-motion`.
- Remember the choices between visits.
- **Apply saved preferences BEFORE displaying or loading the 3D character.**
- Never obstruct essential controls or intercept unrelated taps.
- Restore Rickie through the settings panel after hiding him.
- Hidden mode must not download or render the roaming 3D model at all.

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

### R1. One panel, two independent preferences

**One consolidated Rickie settings panel** (owner decision), containing:

- character display mode — Full / Quiet / Hidden
- the existing chattiness preference
- preferred corner for Quiet
- character sound controls
- attention-getting behaviour controls
- the relevant accessibility / reduced-motion controls

One panel is a layout decision. It is **not** a merge: display mode and
chattiness stay two independent preferences, stored separately, and the panel
has to make that legible rather than imply one supersedes the other.

Presence becomes its own setting — working name `rickie_presence` — with values
`full` / `quiet` / `hidden`. `rickie_mode` keeps doing what it already does and
says: how often Rickie speaks up, never whether his character appears or moves.

**Existing `rickie_mode` values are never reinterpreted or overwritten**
(owner decision). This is the rule the migration has to be written around, not
a preference about it.

Migration position for existing users: `rickie_presence` defaults to `full`,
because that is what every current user already experiences. A user with
`rickie_mode='minimal'` has asked for less *talking*, not less *raccoon*, and
must not be silently migrated into a different choice. `rickie_roam_paused=1`
in localStorage is the one signal that genuinely means "stop moving", and is
the only reasonable candidate for seeding `quiet` — per-device, best-effort,
and never in a way that loses data if it is absent.

### R1b. The Quiet corner is a PREFERENCE, not a position

The user picks a preferred corner and it is remembered between visits (owner
decision). The important consequence is in the wording: what is stored is the
**preference**, and what gets rendered is a **resolved position** that may not
be it.

**A fixed corner is not safe on every screen.** The owner's instruction is
explicit — do not assume it is — and the 2D character already proves the point.
At 320px the content column IS the screen; the measured ceiling for the 2D
Rickie was 64px, and 72px obstructed 20 placements out of 20. A corner is
simply a position with no ability to walk out of trouble, which makes it the
*worse* case, not the safer one:

- **Top-left / top-right** collide with the header brand and the settings
  button.
- **Bottom-left / bottom-right** collide with the bottom nav, which is why
  `.rickie-roam-band` already reserves
  `bottom: calc(60px + env(safe-area-inset-bottom, 0px))`.
- Any corner collides with a toast, a modal, an expanded Brain Boost, or a
  keyboard on a short viewport.

So the corner resolves through the same clearance machinery the 2D character
uses (`occupiedRects`, `isClear`, `somewhereClear` in `static/rickie-roam.js`),
with these rules:

1. Use the chosen corner when it is clear.
2. When it is not, use the nearest safe position — preferring the same side of
   the screen, so he stays roughly where the user put him.
3. **Return to the chosen corner when it becomes clear again.** The stored
   preference is the anchor; displacement is temporary and must not become
   permanent drift, or the setting silently stops meaning anything.
4. Never resolve to a position that covers an essential control (see R5).

Displacement must be silent. A Quiet Rickie who shuffles visibly every time a
toast appears has reintroduced the motion the user turned off.

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

### R4. Reduced motion, extended — and where it now overlaps Quiet

`prefers-reduced-motion: reduce` must continue to be honoured, and should now
mean: no roaming, no celebration animation, no knocking. It should **not** by
itself mean Hidden — a still Rickie is not an accessibility problem, and
removing him entirely is a bigger change than the user asked for. An explicit
user choice always outranks the media query in the direction of *less*, never
*more*: someone who set Full with reduced-motion on gets a stationary Rickie,
not a roaming one.

Now that Quiet exists and is defined as *visible but stationary*, the two
converge: **reduced-motion + Full is behaviourally Quiet.** It should therefore
resolve to the Quiet corner rather than freezing him wherever he happened to
be standing, so the accessible experience is the designed one rather than an
accident of timing.

The panel must not present this as the user having chosen Quiet. The setting
still reads Full; the system is honouring a platform preference on top of it,
and a control that silently rewrites itself is how people stop trusting
settings screens.

### R5. Obstruction and tap interception — the hard one

**The contradiction to resolve first.** The approved capability list says he
moves *in front of* interface elements, peeks around cards and climbs onto
panels. The approved constraint says he never obstructs essential controls.
Taken literally these cannot both hold, because moving in front of something IS
obstructing it.

They are reconcilable, and the reconciliation has to be written down before
anyone builds it, because "don't obstruct" alone would forbid the feature:

- **What he may pass in front of:** decorative surfaces, card backgrounds,
  whitespace, illustrations — and only in passing.
- **What he may never occlude, moving or still:** any control that completes a
  task. The mission's "I did this" buttons, the bottom nav, the settings
  control, form fields, the Coach input, any modal's confirm/cancel, and the
  moderation dashboard's action controls.
- **Text is the middle case.** Passing over a line of copy for a few hundred
  milliseconds while walking is the character being alive. Coming to rest on it
  is the bug that shipped twice already.

The rule that falls out: **transient occlusion of non-essential content is a
feature; resting occlusion of anything, and any occlusion of an essential
control, is a defect.** The 2D character's check already encodes exactly this
split — standing overlap fails absolutely, transit overlap is reported and
bounded rather than forbidden — and that is the shape to carry forward.

Climbing and peeking invert an existing mechanism rather than needing a new
one. `occupiedRects()` currently computes where the interface is so he can
**avoid** it; climbing onto a panel means using the same geometry to **attach**
to it. That is a reason to keep one source of layout truth rather than two, and
a reason the prototype should read the real DOM rather than a hand-authored
scene.


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

Restoration is through the consolidated Rickie settings panel (owner decision).
Hidden must not be a one-way door and must not require the user to remember
where the setting was.

Hiding the roaming character does **not** hide the route back, because the 2D
expression avatars stay: the Coach panel, the greeting and the Journey card all
still show Rickie's face, so there remains something recognisably Rickie to
tap toward the settings. That is a second reason the Hidden scope decision in
R6b is the right one — an entirely Rickie-less interface would leave no
affordance pointing at the control that brings him back.

### R6b. What Hidden actually removes

**Only the roaming 3D character** (owner decision, Sept 2026).

| Removed in Hidden | Kept in Hidden |
|---|---|
| The roaming 3D model, its animation loop, its assets | The small Rickie images and expression avatars — greeting, Journey card, Coach panel, reaction toasts, Rise Again, anywhere they appear today |
| Character sounds | Private AI coaching, in full |
| Attention-getting behaviour, including knocking | Every other StreakFit feature |

These are genuinely different things and the plan should stop calling both of
them "Rickie" in the same sentence. The avatars are 2D inline images that are
part of the page's own copy — they illustrate what Rickie is *saying*. The
roaming character is an autonomous thing that occupies the screen on its own
initiative. A user turning off the second has not asked to stop being talked
to by the first.

### R7. Hidden must cost nothing — for the 3D character specifically

No model download, no texture fetch, no WebGL context, no animation loop, no
audio decode.

To be precise about what this does and does not cover, now that R6b keeps the
2D avatars: those avatars are inline images the page already loads to
illustrate what Rickie is saying, and they are not what this requirement is
about. "Zero character assets" means **zero 3D character assets** — the model,
its textures, its animation data, its audio, and the renderer itself.

Verified by observation, not by assertion: a Hidden session's network log must
contain none of them, and the check belongs in the prototype's own harness from
stage 3 rather than being retrofitted at stage 6. Stated that way because the
lazy-loading mistake is easy and quiet — importing a renderer at module scope
"just in case" costs every Hidden user the download while the character
correctly never appears, and nothing on screen would ever reveal it.

---

## Decisions on record (Sept 2026)

The four questions this document opened with are all answered.

| Question | Decision |
|---|---|
| Is Quiet visible-but-still, or visible-on-demand? | **Visible but stationary.** On screen the whole time; stays in the user's corner; no roaming, knocking, climbing or peeking; still tappable. |
| Where does Quiet stand? | **A corner the user picks**, remembered between visits, with a safe alternative whenever that corner would cover something essential. No corner is assumed safe on any screen size. |
| One settings surface or two? | **One consolidated Rickie panel** holding display mode, chattiness, Quiet corner, sounds, attention-getting and the accessibility controls — while display mode and chattiness stay **independent preferences**. |
| Does Hidden remove the 2D avatars too? | **No.** Hidden removes only the roaming 3D character and its assets. The small Rickie images and expression avatars stay, and private AI coaching is untouched. |

Also reaffirmed: character sounds and unsolicited attention-getting stay **OFF
by default**, and the full capability set — walking, waving, celebrating,
exercising, screen-knocking, moving in front of and behind interface elements,
peeking around cards, climbing onto panels — remains the target, behind a
standalone prototype.

## Open questions that remain

1. **What does a tap do in Quiet?** Quiet forbids self-initiated attention-
   getting but keeps him tappable, and a tap has to be answered with
   *something*. A wave is clearly fine. A full celebration that leaves the
   corner is arguably the roaming the user switched off. Suggested rule, for
   confirmation: **a Quiet reaction may animate but may not travel** — he can
   wave, nod, or bounce in place, and does not leave his corner.
2. **Does the Quiet corner preference apply to Full mode at all?** Full roams,
   so it has no corner — but reduced-motion + Full resolves to a stationary
   position (R4), and the natural place is the Quiet corner. That means a user
   on Full may need to set a corner they will normally never see. Either the
   corner control is always visible and occasionally pointless, or it appears
   only in Quiet and the reduced-motion case has no anchor.
3. **Does the 3D character replace the existing 2D roaming Rickie, or coexist
   with it?** This plan assumes replacement — one roaming character, in one
   generation of technology. Worth stating explicitly, because if they coexist
   then `rickie_presence` governs two different renderers and "Hidden" has to
   mean both.
4. **What happens on a device that cannot run the 3D character?** An old phone,
   a blocked WebGL context, a failed asset fetch. Falling back to today's 2D
   roaming Rickie is the graceful answer and the one that keeps the product
   whole; falling back to nothing is simpler and means some users silently lose
   the companion. This decides whether the 2D engine is kept alive after stage
   6 or retired.

---

## Explicitly NOT in scope here

Nothing in this document authorises implementation. No 3D engine or rendering
library is to be added, no assets bought, and the existing 2D roaming Rickie is
to be left exactly as he is until the stage-3 prototype is approved.
