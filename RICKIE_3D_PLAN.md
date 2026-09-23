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
6. Integrate into StreakFit **only after the prototype is approved** — as a
   second renderer alongside the retained 2D fallback (R8), never as a
   deletion of it.

Stage 3 is the first thing needing a green light. Nothing before it touches
this repository.

---

## Owner requirement: the 3D experience must be OPTIONAL

Three user-selectable display modes:

| Mode | Behaviour |
|---|---|
| **Full** | Roams freely from the selected corner as his home position. Moves in front of and behind interface elements, peeks around cards, climbs onto panels, celebrates, demonstrates exercises, responds to taps. |
| **Quiet** | **Visible but stationary**, in the user's selected corner. No roaming, no knocking, no climbing, no peeking, no self-initiated attention-getting of any kind. **Still tappable**, and a tap may be answered with a brief **in-place** reaction. |
| **Hidden** | **Neither** roaming character is rendered — not the 3D one, not the 2D fallback — and the 3D assets are not loaded. The small Rickie images and expression avatars **stay**: greeting, Journey card, Coach panel and anywhere else they appear today. Private AI coaching and every other StreakFit feature remain fully available. |

**Quiet is visible-but-still, not visible-on-demand** (owner decision). He is
on screen the whole time; what he stops doing is moving and asking for
attention.

**Stationary is not motionless** (owner decision). A tap may be answered with a
wave, a nod, a smile or an expression change — brief, in place, and only ever
because the user started it. What a tap may **not** do is take him out of his
corner: no walking across the interface, no climbing onto another element, no
roaming sequence begun by a tap. The rule is short enough to hold in one line:

> **A Quiet reaction may animate. It may not travel.**

And Quiet still initiates nothing on its own. Every reaction in this mode has a
user's finger behind it. Reduced motion applies on top (R4): it governs which
reactions are chosen and how they are played, not whether Rickie may answer at
all — a user who taps a character deserves some acknowledgement that the tap
landed.

Plus:

- Separate controls for **character sounds** and for **attention-getting
  behaviour** (including screen-knocking), independent of the display mode.
  Both **default OFF**.
- A **user-selectable corner**, remembered between visits, applying **across
  every display mode** rather than only Quiet, with a safe temporary
  alternative whenever that corner would cover something essential — and the
  saved preference never overwritten by that displacement.
- One consolidated Rickie settings panel holding all of it, with display mode
  and chattiness kept as **independent** preferences.
- Respect `prefers-reduced-motion`.
- Remember the choices between visits.
- **Apply saved preferences BEFORE displaying or loading the 3D character.**
- Never obstruct essential controls or intercept unrelated taps.
- Restore Rickie through the settings panel after hiding him.
- Hidden mode must not download or render the roaming 3D model at all, and
  must not substitute the 2D roamer in its place.
- The existing **2D roaming Rickie is retained as a fallback** for devices that
  cannot run the 3D character adequately — obeying the same display mode,
  corner, chattiness, sound, attention-getting and reduced-motion preferences.

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
- preferred character corner — applies in every mode (R1b), not Quiet only
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

### R1b. The corner is a PREFERENCE, it spans every mode, and it is not a position

**The selected corner is a persistent character-position preference, not a
Quiet-only setting** (owner decision). It means something different in each
mode, and something in all of them:

| Mode | What the corner means |
|---|---|
| **Full** | His **home / resting position** — where he returns between behaviours and where he starts. He may still roam and interact wherever permitted. |
| **Quiet** | Where he stays, subject to safe placement. |
| **Full + reduced motion** | His **stationary position**. The saved display mode stays Full (R4). |
| **Hidden** | Nothing is rendered, and the preference is **preserved** for whenever he is made visible again. |

That last row is the one most likely to be lost in implementation: hiding the
character must not clear the corner. A user who hides Rickie for a month and
brings him back should find him where they put him, not in a default corner
they have to set again.

Because it spans modes, the settings panel presents it as a live setting in
all of them — including while reduced motion is active, where it is arguably
*more* relevant, because the corner is then the only place he will ever be. It
should not be greyed out or hidden outside Quiet.

The second half of the wording matters as much: what is stored is the
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
2. When it is not, use an appropriate temporary alternative — preferring the
   same side of the screen, so he stays roughly where the user put him.
3. **Never overwrite the saved preference with the fallback position** (owner
   decision). Displacement is a runtime resolution, not a settings change. If
   the app writes the alternative back, a user who set "bottom-left" and once
   opened a keyboard has quietly had their choice changed for them.
4. **Return to the chosen corner when it becomes clear again.** The stored
   preference is the anchor; displacement is temporary and must not become
   permanent drift.
5. Never resolve to a position that covers an essential control (see R5). The
   corner preference does not outrank that constraint in any mode.

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
mean: no roaming, no **self-initiated** celebration animation, no knocking.

It does **not** mean a tap goes unanswered. The Quiet decision permits brief
in-place reactions and requires reduced motion to be respected *when choosing
and displaying them* — which is a different instruction from suppressing them.
A user who deliberately taps a character and gets absolutely nothing back
cannot tell the difference between "reduced motion" and "broken". The
reconciliation: under reduced motion a reaction is expressed as a **change of
state rather than a change of position** — an expression swap or a cross-fade
instead of a bounce or a wave — and never as travel, which is already
forbidden in Quiet for everyone.

It should **not** by itself mean Hidden — a still Rickie is not an accessibility problem, and
removing him entirely is a bigger change than the user asked for. An explicit
user choice always outranks the media query in the direction of *less*, never
*more*: someone who set Full with reduced-motion on gets a stationary Rickie,
not a roaming one.

Now that Quiet exists and is defined as *visible but stationary*, the two
converge: **reduced-motion + Full is behaviourally Quiet.** It resolves to the
user's selected corner (R1b) rather than freezing him wherever he happened to
be standing, so the accessible experience is the designed one rather than an
accident of timing.

**The saved display mode stays Full** (owner decision). Reduced motion is a
rendering constraint applied on top of the preference, never a rewrite of it.
Two consequences to build to: the panel keeps showing Full, and a user who
later turns the OS setting off gets roaming back without having to re-choose
anything.

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

**Only the roaming character — but BOTH implementations of it** (owner
decision).

| Removed in Hidden | Kept in Hidden |
|---|---|
| The roaming 3D model, its animation loop, its assets | The small Rickie images and expression avatars — greeting, Journey card, Coach panel, reaction toasts, Rise Again, anywhere they appear today |
| **The roaming 2D fallback** | Private AI coaching, in full |
| Character sounds | Every other StreakFit feature |
| Attention-getting behaviour, including knocking | The saved corner preference, for when he comes back (R1b) |

**A device that cannot run 3D is not permission to override Hidden** (owner
decision). This is the failure mode to guard: the fallback logic sees "3D
unavailable", reaches for the 2D roamer, and a user who asked for no roaming
character gets one — on the oldest phones, which is the least likely place
anyone would test for it. Hidden is checked **before** capability, not after.
See R8.

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

**The 2D fallback costs nothing in Hidden either.** Since Hidden removes both
roaming implementations (R6b), the 2D engine must not mount, must not start its
behaviour timers or its MutationObserver, and must not append its band to the
DOM. It is cheap, not free, and "cheap" is not the requirement. `mount()` is
already the single entry point for all of that, so the gate is one call site —
worth writing down precisely because it is easy to assume the old lightweight
path does not need gating.

### R8. One roaming Rickie at a time — 3D when it can run, 2D when it cannot

**The 3D character replaces the roaming 2D Rickie when it is available and
enabled** (owner decision). **The 2D roamer is retained as a fallback**, not
retired, for devices and browsers that cannot run the 3D experience adequately
(owner decision).

**Two roaming Rickies must never be on screen at once.** They are mutually
exclusive renderers of one character, chosen per session.

This is the decision that keeps `static/rickie-roam.js` alive after stage 6,
and it changes how stage 6 should be approached: integration is not a deletion
followed by a replacement, it is a **second renderer behind a shared
preference layer**. The preferences, the corner resolution, the clearance
machinery and the obstruction checks belong to neither renderer and should be
factored out of the 2D engine rather than reimplemented in the 3D one — the
alternative is two implementations of "never stand on the mission", which is
exactly the kind of duplication that produced the hardcoded-`56` bug in the
check harness.

**Resolution order, and it matters:**

1. **Hidden?** Render nothing. Stop. Do not evaluate capability, do not fetch
   anything. A device that cannot run 3D is never a reason to show the 2D
   roamer to somebody who asked for neither.
2. **Can this device run the 3D character adequately?** If yes, 3D. If no, the
   2D fallback.
3. Apply the display mode (Full / Quiet), the corner, chattiness, sounds,
   attention-getting and reduced motion to whichever renderer was chosen.

The fallback obeys **all** the same preferences and **all** the same safety
requirements — it is a different renderer, not a different product, and it does
not get a lighter set of rules because it is the older code:

| | 3D | 2D fallback |
|---|---|---|
| Full | Roams, depth, climbing, peeking | Roams, within its existing safeguards |
| Quiet | Stationary in the selected corner; tap-initiated in-place reactions | Stationary in the selected corner; whatever in-place reactions it supports |
| Hidden | Not rendered, assets not loaded | Not rendered |
| Obstruction rules (R5) | Apply | Apply — already enforced today |
| Corner preference (R1b) | Applies | Applies |
| Sounds / attention-getting / chattiness / reduced motion | Apply | Apply as supported |

Where the 2D engine cannot do something the 3D one can — it has no depth, no
climbing, and today no concept of a home corner — the honest position is that
the fallback is a **reduced** experience, not a broken one. What it must never
be is a **less safe** one.

**Do not retire or remove the 2D roaming engine.** Stage 6 previously implied
replacement-by-deletion; it does not, and this document is the record of that.

---

## Decisions on record (Sept 2026)

The four questions this document opened with are all answered.

| Question | Decision |
|---|---|
| Is Quiet visible-but-still, or visible-on-demand? | **Visible but stationary.** On screen the whole time; stays in the user's corner; no roaming, knocking, climbing or peeking; still tappable. |
| Where does Quiet stand? | **A corner the user picks**, remembered between visits, with a safe alternative whenever that corner would cover something essential. No corner is assumed safe on any screen size. |
| One settings surface or two? | **One consolidated Rickie panel** holding display mode, chattiness, the character corner, sounds, attention-getting and the accessibility controls — while display mode and chattiness stay **independent preferences**. |
| Does Hidden remove the 2D avatars too? | **No.** Hidden removes only the roaming character and its assets. The small Rickie images and expression avatars stay, and private AI coaching is untouched. |
| What may a tap do in Quiet? | **Animate in place, never travel.** Wave, nod, smile, change expression — brief, user-initiated, and he does not leave his corner, climb, or begin roaming. Stationary is not motionless. |
| Does the corner apply outside Quiet? | **Yes — it is a persistent character-position preference across every mode.** Home position in Full, standing position in Quiet, stationary position under reduced motion, and preserved through Hidden. |
| Does the 3D character replace the 2D roamer? | **Yes, when available and enabled.** Never two roaming Rickies at once. |
| What happens on a device that cannot run 3D? | **The 2D roamer is retained as a fallback**, obeying the same modes, corner, preferences and safety rules. Inability to run 3D never overrides a Hidden preference. |

Also reaffirmed: character sounds and unsolicited attention-getting stay **OFF
by default**, and the full capability set — walking, waving, celebrating,
exercising, screen-knocking, moving in front of and behind interface elements,
peeking around cards, climbing onto panels — remains the target, behind a
standalone prototype.

## Open questions that remain

All four questions this document previously carried are now answered above.
These are what is genuinely still undecided — recorded rather than guessed.

1. **What counts as "adequately" for running the 3D character?** R8's
   resolution order turns on it, and it is the one input nobody can pick from
   a spec: a WebGL2 context alone is a weak signal, and a device can create one
   and still render at eight frames a second. Whether the test is a capability
   probe, a measured first-frame budget, a device allow/deny list, or a
   downgrade that happens *after* a few seconds of real frame timing changes
   what stage 5 has to measure. A mid-session downgrade also raises its own
   question: is swapping renderers in front of the user acceptable, or does
   the choice have to be made once per session and kept?

2. **Does the corner preference have a sensible default, or must it be
   chosen?** Every corner collides with something on some screen (R1b), so
   there is no safe universal default. Shipping one means picking the least-bad
   and accepting it will sometimes be wrong; requiring a choice puts a settings
   question in front of a user who has not yet seen the character. This is a
   first-run experience decision, not a technical one.

3. **Is the 2D fallback expected to gain a corner and in-place tap reactions,
   or only to honour what it already supports?** R8 says the fallback is a
   reduced experience but never a less safe one. Today's 2D engine has no
   concept of a home corner and no tap handling at all — it is
   `pointer-events: none` by design (R5). Making it honour Quiet properly is a
   real change to shipped production code, with its own testing, and it is not
   in this project's stages.

4. **Where does the consolidated settings panel live, and does building it wait
   for the 3D work?** The panel is specified (R1) but it also improves things
   that exist today — the roam pause is currently unreachable in the UI, stored
   only in `localStorage`. It could ship against the 2D character well before
   any 3D work starts. Whether it does is a sequencing call.

---

## Explicitly NOT in scope here

Nothing in this document authorises implementation. No 3D engine or rendering
library is to be added, no assets bought, and the existing 2D roaming Rickie is
to be left exactly as he is until the stage-3 prototype is approved.
