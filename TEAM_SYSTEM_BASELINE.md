# Team System Baseline — RICKIE 2.0

Started: July 2026
Status: Planning — schema and interaction design only, no implementation
Revision: 2.1 (member removal/ownership, safety, Team Moments schema, invite simplification, teams-per-user cap resolved to 10 free / unlimited Plus, Team Type noted as a deferred future hook)
Depends on: [Teams v1](memory: teams-v1) (witness-only spec, never implemented), [Team Campfire Baseline](memory: team-campfire-baseline) (approved, never implemented), [Retention Direction](memory: retention-direction) (Team Rickie, premium philosophy)

## Overview

Neither Teams v1 nor Team Campfire has ever been implemented — there is no `Team` table, no membership model, no chat, nothing in the current schema beyond a single unused hook: `ProgressEvent.team_id`, a nullable integer column with no foreign key, added ahead of need and never populated. This document is the first real schema design for the whole team layer, written before any of it gets built.

Scope discipline, carried over from every prior sprint in this project: no code, no migrations, no new routes. Schema described in prose and tables, not SQLAlchemy classes. No AI, no cosmetics beyond what's already speced elsewhere, no Rickie Memory (personal-fact tracking) — this document is about team *structure*, not Rickie's intelligence or memory.

---

## 1. Team Rickie vs. the real Team table — resolved

**Decision: Team Rickie is not a row in the Team table. It has no persisted membership, no chat table, no Campfire. It is a presentation-layer frame over data that already exists.**

Reasoning: a real `Team` row requires real members, and Rickie isn't a `User` — giving him a synthetic user row (or a nullable-user membership row) to satisfy foreign keys would introduce a permanent edge case into every team query, every membership check, every chat permission check, forever, to support exactly one team that never changes shape. That's a bad trade for a feature whose entire purpose is "don't show the user a blank screen on day one."

**What Team Rickie actually is:** a UI component styled identically to a real Team card, populated from data that's already there — `currentUser`'s own activity (personal streak, today's status) plus `currentRickieExpression` (built in R1.5.2) standing in for Rickie's "status." No new table. No new query. It renders before any real team exists and keeps rendering alongside real teams afterward, not replaced by them.

This also resolves the "can Team Rickie be left / can Rickie be removed" question from the original brief — there's nothing to leave. It's not a membership row that could be deleted; it's always-present UI, the same way the auth screen's Rickie greeting is always present without being a database record.

**Consequence for Campfire:** Team Rickie does not get a Campfire. Campfire attaches only to real Teams (Section 9). Team Rickie's "relationship" is expressed through Rickie's existing expression/reaction system, not a second progression mechanic.

---

## 2. Schema overview

Six new tables. Naming follows the existing lowercase-with-underscore `__tablename__` convention already used by `daily_completion`, `brain_boost_answer`, `progress_event`.

### `team`
| Field | Type | Notes |
|---|---|---|
| id | integer PK | |
| name | string | user-chosen, e.g. "Hill Family" |
| created_by_user_id | FK → user.id | creator; holds two narrow, safety-scoped powers — see Section 4 — not a general admin role |
| created_at | datetime | |

Deliberately thin. No `max_members` column stored here — see Section 12 (Team Limits) for why that's computed, not stored.

### `team_membership`
| Field | Type | Notes |
|---|---|---|
| id | integer PK | |
| team_id | FK → team.id | |
| user_id | FK → user.id | |
| joined_at | datetime | |
| UNIQUE(team_id, user_id) | | a user can't join the same team twice |

This is the multiple-membership answer (Section 3): a user's teams are just every row in this table where `user_id` matches theirs. No cap enforced at the schema level — caps are a business rule checked at join/create time (Section 12), not a constraint, since the cap can change (free → Plus).

### `team_invite_code`
| Field | Type | Notes |
|---|---|---|
| id | integer PK | |
| team_id | FK → team.id, UNIQUE | one active code per team |
| code | string(8), UNIQUE | short, human-typeable (e.g. 6 chars per the existing Campfire Baseline spec) |
| created_at | datetime | |
| rotated_at | datetime, nullable | set when the creator regenerates the code, invalidating the old one |

This single code is both the thing you type ("join code: HLLFAM") and the token embedded in the shareable link (`streakfit.pro/join/HLLFAM`) — one mechanism, two presentations. It is also, in this revision, the *entire* invite mechanism — see Section 5 for why a separate email-invite table was cut from v1.

### `team_message`
| Field | Type | Notes |
|---|---|---|
| id | integer PK | |
| team_id | FK → team.id | |
| sender_type | enum: user / rickie | |
| sender_user_id | FK → user.id, nullable | null when sender_type = rickie |
| body | text | |
| created_at | datetime | |

No edit, no delete, no threading in v1 — matches the "fun or simple, don't build it" design rule. A reaction (🔥, ❤️) is just a short `body` — no separate reaction type or table needed. `sender_type` is how Rickie's messages (Section 8) live in the same table as human messages without a parallel system.

### `team_campfire`
| Field | Type | Notes |
|---|---|---|
| id | integer PK | |
| team_id | FK → team.id, UNIQUE | 1:1 with team |
| total_team_missions | integer, default 0 | cumulative, **never decrements** — matches the locked Campfire Baseline rule |
| created_at | datetime | |

`stage` (Kindling / Small Flame / Campfire / Bonfire / Beacon) is deliberately **not stored** — it's derived from `total_team_missions` on read, using the five thresholds already locked in the Campfire Baseline (0–99 / 100–299 / 300–749 / 750–1999 / 2000+). Storing a redundant `stage` column risks it drifting out of sync with the count it's supposed to represent; computing it is one cheap comparison.

### `team_moment`
| Field | Type | Notes |
|---|---|---|
| id | integer PK | |
| team_id | FK → team.id | |
| moment_type | string enum: mission_milestone / campfire_stage / first_week / brain_boost_streak / family_record / ... | open-ended, extend as new moment types get designed |
| subject_user_id | FK → user.id, nullable | set for a moment that belongs to one member (a personal milestone surfaced to the team); null for team-wide moments (a Campfire stage crossing) |
| occurred_at | datetime | |
| metadata | JSON | flexible payload — `{"streak": 7}`, `{"stage": "Bonfire"}`, `{"correct_streak": 20}` |

Promoted from "deferred" in the first revision of this document — see Section 10 for why it earns a real table now, and how it relates to `team_message`.

---

## 3. Multiple team membership

Answered by `team_membership` being a proper join table (Section 2), not a `team_id` column on `User`. A user's full team list is a simple query; no schema changes needed if that list grows from 1 to 8.

**Interaction consequence:** a single mission completion is not "for" one team. If Tim is in both "Hill Family" and "Beta Testers," completing today's mission increments `total_team_missions` on **both** teams' Campfires simultaneously — no "which team is this for" picker, no friction at the moment of completion. This mirrors how Personal Streak already works: one true personal number, witnessed by however many teams the user happens to belong to. Consistent with Design Rule #2 (every feature should make the app more fun or simple).

---

## 4. Member removal and ownership

Not addressed in the first revision of this document. Two questions, answered separately because they have different risk profiles:

**Leaving:** any member can leave any team at any time, unconditionally. No permission required, no one can prevent it — including the creator leaving their own team.

**Removing someone else:** this is where the existing Team Campfire Baseline's "no admin" rule needs a deliberate, narrow exception, not a blanket carry-over. That rule was written for the Campfire mechanic itself — no admin is needed to protect a counter that literally cannot break. It wasn't written with membership safety in mind, and applying it there without a second look creates a real gap: the scenario that actually matters here is a 13-year-old inviting a friend, and that friend re-sharing the same standing code with someone nobody in the family actually knows. Someone needs a way to remove that person, and it can't be "nobody."

**Decision:** the team creator (`team.created_by_user_id`) holds exactly two powers, and nothing else:
1. Remove another member from the team.
2. Rotate the team's invite code (cutting off anyone who only has the old one).

No message moderation, no chat deletion, no other team settings. This is not a general admin role — it's a safety-scoped exception to "no admin," justified specifically by the child-invite-chain scenario, and should be read as narrow on purpose, not as reopening the door to a broader permissions system.

**Ownership transfer:** the creator can explicitly hand the role to another member — a deliberate, mutual action, not automatic. No inactivity-based reassignment: if a creator goes permanently inactive with no successor named, the team keeps working (Campfire keeps growing, chat keeps working, membership is unaffected) — only the removal/rotation power is unavailable until someone addresses it manually. That's a rare edge case better handled by a one-off support touch than an automated succession algorithm, consistent with not building for hypothetical scenarios before they're observed in real usage (Design Rule #3).

**Rickie:** cannot be removed — moot, since he was never a membership row to begin with (Section 1).

---

## 5. Invites — code and link only, email deferred

Original design had three request-methods (code, link, email) backed by two mechanisms. This revision collapses it to one mechanism, one table (`team_invite_code`), after a direct challenge to whether the email-specific `team_invite` table (per-recipient token, single-use, revocable, expiring) earns its complexity for an MVP.

**It doesn't, yet.** In v1:
- **Invite code**: typed directly into the app.
- **Invite link**: the same code, wrapped in a URL (`streakfit.pro/join/<code>`) — convenience packaging, not a separate token.
- **Invite by email**: the app sends that *same* link/code through a mail integration. No per-recipient token, no accept/revoke lifecycle beyond what the code already has (the creator can rotate it — Section 4).

**Known, accepted limitation:** v1 can't answer "did the specific person I emailed actually join, or did someone else use the link." That's a real gap, deliberately accepted for MVP simplicity rather than an oversight. If per-recipient tracking becomes a real need later — not a hypothetical one (Design Rule #3) — a `team_invite` table can be added as a second, parallel option without disrupting the code/link mechanism it wouldn't replace.

No path here allows searching for a team or browsing a directory — a team is only ever reached via a code, a link, or an emailed link, which is what actually enforces the "strengthens existing relationships, not anonymous ones" rule from Retention Direction. That rule isn't a moderation policy to build; it's the absence of a discovery feature that was never designed in the first place.

---

## 6. Public membership visibility

"Membership visible, chat private" (per Retention Direction) needs no new schema — it's an access-control statement about existing tables:

- **Roster read** (`team_membership` rows for a given `team_id`): any current member of that team can see the full list of who else is in it. No new privacy table needed.
- **Chat read** (`team_message` rows): scoped strictly to members of that `team_id`. A user with zero membership rows for a team sees nothing from it, ever — enforced the same way `daily_completion` reads are already scoped to `user_id` today, just one join away (member of `team_id` → allowed to read `team_id`'s messages).

No cross-team visibility of any kind. Being in Team A gives no information about Team B, even if some members overlap.

---

## 7. Team chat permissions

- Only members of a team (a `team_membership` row for that `team_id`) can post or read that team's `team_message` rows.
- No global chat, no DMs, no one-on-one stranger chat — a chat thread only exists as a property of a team, never standalone. This isn't a permission to design around; there's simply no route that creates a message without a `team_id`.
- Rickie can post into any team's chat as a system-authored participant (`sender_type = 'rickie'`), never gated by membership since he's not a member — he's present in every team by design, the same way he's present in Team Rickie without being a real member there either.

---

## 8. Rickie reactions inside team chat

**These are not the same thing as the personal reaction toasts already built in R1.5.2.** Those are ephemeral, per-user, never persisted, never seen by anyone else. Team reactions are different in kind: persisted `team_message` rows, visible to every member, and — per Retention Direction's explicit "should not dominate" instruction — deliberately infrequent. Rickie should not post into team chat every time any member completes any exercise; that would turn the chat into notification spam and violate the same "amplify, don't replace" principle governing his whole role.

**Recommended trigger set** (template-driven, reusing the existing `RICKIE_LINES`-pool pattern rather than inventing a new authoring system or introducing generative AI, which stays explicitly out of scope):
- A team-wide full-completion day (every member finished today's mission) — the single strongest witness moment the whole system is built around.
- The team's Campfire crossing a stage threshold (Kindling → Small Flame, etc.) — rare by construction, since stages span hundreds of missions.
- A member's personal milestone surfaced to the team (7-day streak, Rise Again) — this is exactly the "Encouragement" pillar from the Campfire Baseline: individual milestones surfaced to the team, not manufactured team activity.

Everything else — ordinary single-exercise completions, one member finishing while others haven't — stays silent in team chat. Loud individually (the existing personal reaction toast), quiet collectively.

---

## 9. Campfire attachment strategy

One `team_campfire` row per `team` (Section 2), created at the same time as the team itself, starting at `total_team_missions = 0` (Kindling). Never a separate joinable object, never optional, never detachable — a team simply has a Campfire the way it simply has a name. Team Rickie has none (Section 1).

Growth path: every mission completion by every member of a team increments that team's `total_team_missions` by 1 (Section 3). Stage is derived, not stored. No shrinking, no reset, no protection mechanism needed — this is the entire point of the original Campfire Baseline decision to retire Family Flame's fragile shared-streak model in favor of something that literally cannot break.

---

## 10. Team Moments

Promoted from a deferred note to a real table (`team_moment`, Section 2) after review — the scrapbook/callback use case is strong enough to design now rather than later: *"Remember when your campfire first reached Stage 3?"* or *"Claire answered 20 Brain Boost questions in a row back in August."*

**Relationship to `team_message`:** moments are the durable record; chat messages are the optional, live, filtered *announcement* of a subset of them. The trigger set in Section 8 (full-team days, Campfire stage-ups, surfaced milestones) determines which moments also get posted to chat in real time. Every other moment — a `family_record`, a `brain_boost_streak` that wasn't dramatic enough to interrupt anyone live — sits quietly in `team_moment` until something retrospective surfaces it: a future Team Memory Book, or Rickie pulling one up in conversation. This is what actually makes the "remember when" callback possible — Rickie reads structured moment history, not chat transcripts, which is a much more reliable and much cheaper thing to query than trying to extract meaning from a message log after the fact.

Not designed here: the retrospective surface itself (a Team Memory Book UI, parallel to the personal one). That's real scope for a future pass, not implied by adding this table now.

---

## 11. Safety

Given who this system is actually for — the original brief named the two kids in this project's own household as the reason it exists — this gets its own section rather than staying folded into invites or removal.

**The core risk pattern:** an invite chain extending past who a parent or family actually knows and trusts. A kid invites a friend; the friend, having a valid standing code, could pass it to someone else entirely.

**What the existing design already prevents, structurally, by omission rather than by moderation:**
- No discovery feed, no user search, ever — a team is unreachable except through a code, a link, or an emailed link (Section 5).
- Chat is strictly team-scoped (Section 7) — no DMs, no cross-team visibility, no way to message someone outside a shared team.
- No public profiles, no "people nearby," no matchmaking — none of that was ever designed into any part of this system.

**What this revision adds specifically for safety:** creator-only member removal and code rotation (Section 4), so a parent or guardian who created a family team has a real, immediate lever — removing someone who shouldn't be there doesn't require anyone else's cooperation, and rotating the code cuts off further spread from an over-shared link.

**Explicitly out of scope for this document:** age verification, parental consent flows, COPPA or equivalent legal compliance mechanics. Those are real requirements for a product with a stated child userbase — but they're legal and product-policy decisions, not schema decisions, and shouldn't be designed inline here as an afterthought. Flagged as a required follow-up with whoever owns that call, not addressed by this document.

---

## 12. Team limits

| | Free | Plus |
|---|---|---|
| Teams a user can join | 10 | unlimited |
| Max members per team | 8 | 25 |

Member-cap numbers locked in revision 2 — 8 free / 25 Plus covers families, friend groups, a church group, a small classroom, or a small team at work without drifting into needing moderation tooling a Discord-scale product would require. A third, higher tier has been suggested informally but doesn't exist in any approved pricing document yet (Retention Direction defines only Free and Plus) — noted here as a natural extension point if a third tier is ever introduced, not decided as real scope now.

**Teams-per-user cap resolved in this revision, replacing the earlier "3" placeholder.** 10 free teams is generously above what most people will actually use — realistically Team Rickie plus five or six real ones (family, friends, a beta group, a church or work group) covers nearly everyone — so 10 reads as "no meaningful ceiling" without literally being unlimited on the free tier. Plus removes the cap entirely, since gating *how many* relationships someone can maintain doesn't fit the "strengthens existing relationships" principle as comfortably as gating team *size* does.

**Interaction design for the per-team member cap:** rather than pinning a team's cap to its creator's plan forever, the effective cap for a given team is **the highest tier held by any current member.** One Plus subscriber in a friend group lifts the ceiling for everyone in that team, not just themselves. This matches how people actually experience family/friend-group products (one person subscribes, the group benefits) and avoids the awkward case where a team's creator downgrades and suddenly the team they built is over its own cap.

This is a limits question, not a paywall on the feature — Teams, Team Rickie, chat, and Rickie's reactions are all free per Retention Direction's premium philosophy. Free users get the full experience at a smaller scale, never a locked door.

---

## 13. What this document does not decide

Flagged explicitly rather than silently assumed:
- Email delivery mechanism itself (which provider, template content) for the code/link sent in Section 5 — out of scope, this document only decides that no separate invite record is needed for it in v1.
- Age verification, parental consent, COPPA-equivalent compliance — flagged in Section 11 as real, required, and explicitly not decided here.
- The retrospective Team Moments surface (a Team Memory Book UI) — the schema exists (Section 2, Section 10) but the UI that reads it doesn't. **Now being designed in a companion document, `TEAM_UI_BASELINE.md`.**
- **Team Type** (family / friends / school / church / work / custom) — raised during review as a future flavor lever for Rickie's language ("Walker added another log" vs. "Crew's been showing up lately"), not a mechanics change. Deliberately not designed now. If it happens, it's a single nullable `team_type` string column on `team` (Section 2) — cheap to add later, not worth reserving schema space for today.
- Nothing here touches XP/Acorns economics, cosmetics, or Rickie Memory — those remain governed by their own baselines, untouched by this document.

---

## 14. What this explicitly is not

No code. No migrations. No new API routes. No AI (Rickie's team-chat reactions are template/pool-driven, same mechanism as his existing reaction lines — not a generative system). No cosmetics beyond what R1.5's own baseline already covers. This is schema and interaction design only, per the brief it was written against.
