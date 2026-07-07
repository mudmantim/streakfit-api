# Team UI Baseline — RICKIE 2.0

Started: July 2026
Status: Planning — screens and interaction flow only, no code
Depends on: [Team System Baseline](TEAM_SYSTEM_BASELINE.md) (schema this UI reads and writes — every element below is traceable to a table or a locked decision in that document, not invented fresh)

## Overview

Six screens, in the order a user actually encounters them: Team Rickie (always there) → Teams List (the hub) → Campfire (a team's identity) → Team Chat (the daily witness moment) → Team Memory Book (the retrospective) → Invite Flow (how anyone gets from outside to inside). Each section below names which table backs it and which locked decision from the system baseline it depends on, so nothing here quietly re-decides something already settled.

No code, no component names, no CSS. Screens are described the way the Rickie Expression Language document described poses before any SVG existed: what's on the screen, top to bottom, what's tappable, what's empty-state, and why.

---

## Screen 1 — 🦝 Team Rickie

**Backed by:** nothing new. No `team` row, no `team_membership`, no `team_campfire`. Pulls from data that already exists: `currentUser` and `currentRickieExpression` (R1.5.2).

**Why it looks like a Team card but isn't one:** Team System Baseline Section 1 was explicit that Team Rickie must render identically to a real team, so a brand-new user's first screen doesn't look like an empty product waiting to be filled in. Structurally:

- Header: "🦝 Team Rickie"
- Member row 1: Rickie — avatar renders whatever `currentRickieExpression` currently is, so this card is quietly alive even when nothing else on the dashboard is
- Member row 2: the user — name, today's status (same ✅/○ used everywhere else), current personal streak
- Where a real team's Campfire row would go: the user's own Journey summary (current streak, total missions) instead — this is deliberately *not* a fake Campfire number. Team Rickie has no Campfire (System Baseline Section 1), and inventing a fake one to fill the visual slot would undercut the reason that decision exists. What actually goes in that slot is real: "what you and Rickie have built so far."
- One action: opens the existing Coach panel. Team Rickie doesn't get its own chat UI — there's no `team_message` table backing it (Section 1), and the existing Coach conversation already *is* the user-and-Rickie conversation. Building a second, parallel, fake-persisted chat just for this card would be exactly the kind of complexity Section 1 was written to avoid.

**Empty-state consideration:** there isn't one. This card cannot be empty — it's the one screen state that's guaranteed to always have something to show, which is the entire point of it existing.

**Entry point for everything else:** below the Team Rickie card, a single line for users with zero real teams — "Create or join a team" — leading to Screen 2.

---

## Screen 2 — 🔥 Teams List

**Backed by:** `team_membership` rows for the current user (System Baseline Section 3), joined against `team` and `team_campfire` for the summary line each team needs.

**Layout, top to bottom:**
- Team Rickie card first, always pinned at the top, identical to Screen 1 (not a link to a separate screen — it's small enough to just live here directly, since it has no drill-down of its own beyond opening Coach).
- One row per real team, ordered by most recently active (a team someone posted or completed in today surfaces above one that's been quiet for weeks — encourages returning to the team that's actually alive, not an alphabetical or creation-date list nobody asked for).
- Each team row shows: team name, Campfire stage (derived per System Baseline Section 2 — "🔥 Bonfire," not a raw number), a small stack of member avatars (capped visually at a handful with a "+3" overflow, matching the existing member-count caps from Section 12), and a same-day witness line — "6 of 8 completed today" — which is Teams v1's original "I can see who showed up" promise, now surfaced at the list level instead of requiring a tap into each team to see it.
- Tapping a row opens that team's Campfire screen (Screen 3).

**Actions:**
- "Join a team" → Screen 6 (Invite Flow), accept-a-code path.
- "Create a team" → a short inline form (name only — `team.name` is the only required field per the schema), not its own numbered screen. Creating a team immediately creates its `team_campfire` row at Kindling (System Baseline Section 9) and its `team_invite_code` (Section 2), so the very next thing after naming a team is being handed something to share.

**Empty state (zero real teams):** just the Team Rickie card and the two entry-point actions. Never a blank page — Team Rickie means "zero teams" and "day one" look identical to "already engaged," which is the whole design goal from Section 1.

**Limit feedback:** if a user is at their team cap (10 free / unlimited Plus, Section 12), "Create a team" and "Join a team" show the cap plainly rather than failing silently after the fact — "You're in 10 of 10 teams" rather than a rejected join attempt with no explanation.

---

## Screen 3 — 🔥 Campfire

**Backed by:** `team_campfire` (the stage/count), `team_moment` (Moments/History), `team_membership` (the roster), `team_message` (a preview, not the full thread — Screen 4 owns that).

This is a single team's identity screen — what you land on from Screen 2, and what a new member sees right after accepting an invite.

**Layout, top to bottom:**
- Header: team name, current Campfire stage with its visual (Kindling through Beacon), and a progress indicator toward the next stage — "312 of 750 team missions to Bonfire." Since stage is computed, not stored (System Baseline Section 2), this is a read-time calculation, not a field the UI trusts blindly.
- **Logs**: the raw `total_team_missions` count, framed as *the team's*, not any one person's — "342 logs added to this fire" — deliberately not broken into a per-member leaderboard here (that's Screen 5's contribution history, and even there it's framed as celebration, not ranking — see Screen 5's note on this).
- **Moments**: a short, recent slice of `team_moment` rows — the last few stage-ups, milestones, and full-team days — rendered as small cards, not a chat-style feed. This is *not* the same surface as Screen 5's Team Memory Book; it's a preview (last handful), while the Memory Book is the full retrospective. Tapping "See all moments" is the bridge from this compact preview into Screen 5.
- **History**: same idea as Moments but slower-moving — team age ("Hill Family, 4 months old"), the stage-progression timeline itself. Kept lightweight here on purpose; anything more belongs in Screen 5.
- **Rickie**: his current expression avatar plus, when one exists, his most recent team-chat line (System Baseline Section 8's trigger set) — shown here as a highlight, separate from the full thread, since his team messages are rare enough that surfacing the latest one prominently is more valuable than making someone scroll chat to find it.
- Roster: member list with today's status and personal streaks — the same witness view from Screen 2's summary line, now shown per-member instead of aggregated.

**Actions:** "Open Chat" → Screen 4. "Memory Book" → Screen 5. Creator only: "Invite" → Screen 6, "Remove a member," "Rotate invite code" (System Baseline Section 4 — these two are the *only* extra actions the creator sees anywhere in this whole UI, deliberately not surfaced as a general "manage team" menu that implies broader admin power than actually exists).

---

## Screen 4 — 👥 Team Chat

**Backed by:** `team_message` (System Baseline Sections 6–8).

**Layout:**
- Chronological thread, oldest to newest, standard chat framing.
- Each message shows sender: a member's name/avatar, or — visually distinct, not styled like a bot notification — Rickie's current-expression avatar and name for `sender_type = rickie` rows.
- Rickie's messages are rare by design (Section 8's trigger set: full-team days, stage-ups, surfaced milestones) — the UI shouldn't compensate for that rarity by making them louder than they need to be. He should read like a participant who speaks up when something's actually worth saying, not a system banner.
- Input row: free-text entry, plus a small row of quick-tap reaction options (🔥 ❤️ 👏 💪) above or beside it. Tapping one sends a short-body `team_message` — there is no separate "reaction" schema (Section 2 already decided this: a reaction is just a short message), so this is purely a faster way to produce the same row a full message would, not a new mechanism.
- No edit, no delete, no threading — matches Section 2's "fun or simple" scope for `team_message` exactly. What you see is the whole feature.

**Empty state:** "No one's said anything yet — the first log is still warming up," inviting a first reaction rather than a blank, intimidating text box.

---

## Screen 5 — 📖 Team Memory Book

**Backed by:** `team_moment` (System Baseline Sections 2 and 10), plus `team_campfire` and `team_membership` for the summary pages.

**Reuses the existing personal Memory Book's page-turning book UI** (built in R1.7) rather than inventing a new browsing pattern — same left/right page navigation, same "this is a scrapbook, not a stats dashboard" framing, just reading from `team_moment` instead of a user's personal event history. This is the retrospective surface System Baseline Section 13 flagged as designed-in-schema-but-not-in-UI; this screen is that design.

**Pages, in order:**
- **Anniversaries** — team creation date and time-based milestones ("Hill Family: 4 months together").
- **Milestones** — `team_moment` rows with `moment_type = campfire_stage` or `mission_milestone`, presented the way the personal Memory Book already presents personal milestones: one moment, one page, unlocked/locked framing for what's still ahead.
- **Campfire growth** — the stage-progression story specifically, visualized as a path from Kindling to wherever the team currently sits, each past stage-crossing dated.
- **Contribution history** — **the one page that needs explicit care.** This is where "who added how much" lives, and it would be easy to accidentally build a leaderboard here — which every existing design document (Teams v1, Game Design Baseline) explicitly rules out. The framing has to stay celebratory and individual, not comparative or ranked: "Tim has added 340 logs to this fire" as its own standalone fact, not positioned against anyone else's number, no ordering by size, no "#1" of any kind. If two members' numbers are visible on the same page, they're listed alphabetically or by join order, never by count.

**Rickie's role here:** this is the natural home for the "remember when" callback the whole `team_moment` table exists to enable — a Rickie-voiced line at the top of relevant pages ("Rickie remembers when this fire first became a Bonfire"), reading `team_moment` data directly rather than trying to reconstruct meaning from `team_message` chat history (System Baseline Section 10's stated reason for keeping moments and messages as separate concerns).

---

## Screen 6 — ✉️ Invite Flow

**Backed by:** `team_invite_code` only (System Baseline Section 5 — the email-invite table was cut; there is no second mechanism to build a screen around).

**Two entry directions:**

**Sharing (from inside a team, Screen 3):**
- Copy Link — copies `streakfit.pro/join/<code>` to the clipboard.
- Copy Code — copies the bare code, for reading aloud or texting.
- Send Email — hands the same link to a mail integration; no separate tracked invite, no per-recipient state (Section 5's accepted limitation — this screen has nothing to show for "did they open it," because nothing stores that).
- Creator only: Rotate Code — regenerates `team_invite_code.code`, invalidating anything already shared. Shown with a plain warning that old links/codes stop working, since this is also the safety lever from Section 4 and Section 11.

**Accepting (from outside, via a tapped link or a typed code):**
- Landing view shows a preview before joining — team name, Campfire stage, a few member avatars — so accepting is a real, informed choice rather than an instant silent join. This matters more here than it would elsewhere: Section 11's safety framing is specifically about invite chains extending past who's actually trusted, and a preview screen is the one moment where a person can recognize "I don't actually know this team" before becoming a member of it.
- "Join" creates the `team_membership` row and lands directly on that team's Campfire screen (Screen 3) — the new member's first real view of the team they just joined, not back on a generic list.
- If the team is already at its member cap (Section 12), the preview says so plainly instead of allowing an attempt that fails after tapping Join.

---

## What this document does not decide

- Visual design, color, typography, spacing — none of that is implied by this document; it describes structure and content, not a look.
- The "Create a team" inline form's exact fields beyond `name` (the only one the schema requires) — kept deliberately minimal here; if more fields earn their way in later (Team Type being the obvious candidate per System Baseline Section 13), this document doesn't pre-block that.
- Notification/push behavior for team chat or Rickie's rare messages — a real question, not addressed here.
- Anything already flagged as undecided in the System Baseline (age verification, email delivery specifics, Team Type) remains undecided here too — this document builds UI on top of that schema, it doesn't resolve gaps in it.

## What this explicitly is not

No code. No component library choices. No API contracts. Six screens, described the way a person would actually move through them, each one traceable back to a table or a locked decision in `TEAM_SYSTEM_BASELINE.md` — nothing invented fresh that the schema document didn't already make room for.
