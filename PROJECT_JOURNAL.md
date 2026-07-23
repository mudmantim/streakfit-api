# StreakFit — Product Polish Journal

Session: overnight UX polish pass (2026-07-22)
Branch: `overnight-ux-polish`

Goal set by Tim: *"Make StreakFit feel like one of the best apps on my phone."* Not new
features — elevation of the existing product. Every change below is polish or clarity on
something that already exists. No invented functionality, no fake data.

---

## Guiding read of the app before touching anything

StreakFit is already a thoughtful product. Before making a single change I drove the whole
thing as a new user (guest + a fresh registered account, mission completed end to end):

- **The bones are strong.** The Rickie voice (RICKIE_LINES pools), the streak framing
  ("Day N" for days 1–6, "N days" from 7), the anti-punishment Rise Again ceremony, and the
  Mission → Insight → Brain Boost unlock loop are all genuinely well designed. This is not a
  rebuild job.
- **What holds it back from feeling "premium" is the first impression and the chrome**, not
  the core loop. Specifically:
  1. The auth screen is a bare login form. A person who's never heard of StreakFit learns
     almost nothing about what it is or why it's different before being asked to make an
     account.
  2. The dashboard header carries five separate controls (skill select, Rickie-mode select,
     three theme buttons, logout). It reads like a settings bar, competing with the mission
     for attention on the single most important screen.
  3. A brand-new user's stats row shows `🔥 0 days · Best: 0 days · ✓ 0 missions` — three
     zeros as the first thing they see. That's a deflating first impression of a system that
     is otherwise careful never to make you feel behind.
  4. Small visual-noise issues: underlined "How to do this / Exercise Tips" links on every
     exercise row, flat card depth, and celebration moments that are informative but not
     quite *felt*.

Decisions and their reasoning are logged below as I make them.

---

## 1 — First 30 seconds: auth screen (Priority 1)

**Change:** Replaced the bare login-form landing with a value-first invitation. Above the
sign-up controls there is now a three-point card that answers, in one scan:

- 🎯 *Five tiny moves a day* — what it is (a finishable daily mission, not a dreaded workout).
- 🔥 *Build a streak that means something / Rickie helps you rise again* — why it's different
  (the anti-punishment core rule, stated honestly: a streak can slip, and the response is help,
  not a lecture).
- 🦝 *Rickie's in your corner* — why you'd care (a companion, using Rickie's real face).

**Reasoning:** A newcomer previously saw a one-line tagline and a password field — nothing about
what the product *is*. The fastest "aha" for StreakFit is understanding the philosophy (showing
up beats perfection) plus seeing the mission. So: (a) three tight value props carry the pitch in
well under 30 seconds, and (b) the guest button copy changed from "Continue as Guest" to
"Peek at today's mission first — no signup" so the zero-friction path to the actual product is
framed as an invitation, not a fallback. Register CTA changed to "Start free — takes 10 seconds"
to name the (low) cost of the primary path. Copy was kept accurate to the Rise Again design — it
does not claim the streak never breaks, only that you're never punished for it.

## 2 — Home screen: header declutter + honest zero-state (Priority 2)

**Change A — one gear instead of five controls.** The dashboard header used to carry a
difficulty select, a Rickie-mode select, a three-button theme toggle, and a logout button, all
competing with the mission. They now live behind a single ⚙️ settings menu (opens on tap, closes
on outside-click / Escape). Every control kept its *exact* original id and onchange handler — the
JS that reads/writes `#skill-level-select`, `#rickie-mode-select`, the `.theme-btn`s and
`#btn-logout` is untouched; they just moved into a dropdown. Guests see only Theme + Exit (the
difficulty/Rickie rows hide for guests, matching the old behaviour).

**Reasoning:** The home screen should lead with the mission, not with configuration. These are
set-once-and-forget preferences; burying them behind a gear is standard for a reason. Header is
now just brand + gear — calm, focused, unmistakably "here's your thing to do today."

**Change B — no wall of zeros for new users.** A brand-new account used to see
`🔥 0 days · Best: 0 days · ✓ 0 missions`. The stats row is now hidden until there's at least one
day of streak or one completed mission; until then the "Complete all 5 to start your streak"
helper carries the message.

**Reasoning:** Three zeros is the one moment this otherwise-encouraging app accidentally says
"you're behind." A fresh start should look like a fresh start. The stats appear the instant
they're worth celebrating.

**Bonus fix (latent bug):** `--gray-300`, `--gray-600`, `--gray-800` were referenced in the CSS
but never defined in `:root`, so six existing rules silently inherited the wrong colour (this is
what made the relocated Log Out button render white-on-white at first). Defined all three with
the standard grey ramp, repairing those existing usages too.
