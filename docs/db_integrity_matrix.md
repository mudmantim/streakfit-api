# Database Integrity & Deletion-Policy Matrix (WS4)

Every foreign key that references a **user** or **team**, its current DB behavior,
the desired policy, and the rationale. This is the policy the **application-level
deletion service** (`delete_user_account`, WS5) now enforces. A matching set of
DB-level constraints is the recommended follow-up migration (see bottom).

Legend: **CASCADE** delete children · **SET NULL** keep child, null the link ·
**RESTRICT/manual** block until handled.

## FKs referencing `user.id`

| Parent → Child | FK column | Current DB | Desired | Policy | Rationale / shared-data risk |
|---|---|---|---|---|---|
| user → challenge | `user_id` (NOT NULL) | no action | CASCADE | **CASCADE** | Private to the user. |
| user → daily_completion | `user_id` (NOT NULL) | no action | CASCADE | **CASCADE** | Private progress. |
| user → brain_boost_answer | `user_id` (NOT NULL) | no action | CASCADE | **CASCADE** | Private progress. |
| user → progress_event | `user_id` (NOT NULL) | no action | CASCADE | **CASCADE** | Private progress. |
| user → team_membership | `user_id` (NOT NULL) | no action | CASCADE | **CASCADE** | The user's own membership; deleting them removes them from teams. Does **not** affect the team or other members. |
| user → coach_turn | `user_id` (NOT NULL) | no action | CASCADE | **CASCADE** | Private conversation memory. |
| user → coach_note | `user_id` (NOT NULL, unique) | no action | CASCADE | **CASCADE** | Private conversation memory. |
| user → team_message | `sender_user_id` (**NULLABLE**) | no action | SET NULL | **SET NULL** | Shared team content. Keep the message in the team's history; drop the author link (anonymize). |
| user → team_moment | `subject_user_id` (**NULLABLE**) | no action | SET NULL | **SET NULL** | Shared team timeline. Keep the moment; drop the subject link. |
| user → team | `created_by_user_id` (NOT NULL) | no action | RESTRICT | **manual / block** | **Team ownership.** A team is shared data with other members. Deleting an owner must not silently tear down other people's team — block unless an explicit team policy (reassign owner, or delete a QA-only team — see WS6) is applied. |

## FKs referencing `team.id`

| Parent → Child | FK column | Current DB | Desired | Policy | Rationale |
|---|---|---|---|---|---|
| team → team_membership | `team_id` | no action | CASCADE | **CASCADE** | Deleting a team removes its memberships. |
| team → team_invite_code | `team_id` (unique) | no action | CASCADE | **CASCADE** | Per-team. |
| team → team_message | `team_id` | no action | CASCADE | **CASCADE** | Per-team. |
| team → team_moment | `team_id` | no action | CASCADE | **CASCADE** | Per-team. |
| team → team_campfire | `team_id` (unique) | no action | CASCADE | **CASCADE** | Per-team. |

## Other integrity notes
- **Nullable columns that should stay nullable:** `team_message.sender_user_id`
  (Rickie messages have `sender_type='rickie'`, no sender) and
  `team_moment.subject_user_id` (some moments have no subject). Both are load-bearing
  for the SET NULL policy — do **not** make them NOT NULL.
- **Orphan risk today:** because no FK has `ON DELETE`, a raw `DELETE FROM user` would
  either FK-error (Postgres, if children exist) or orphan children (SQLite w/o FK
  enforcement). The app never does raw user deletes — deletion goes through
  `delete_user_account`, which removes children in the correct order — so there are no
  orphans in practice. The migration below is defense-in-depth.
- **Indexes:** the user-FK hot paths already have indexes (`coach_turn.user_id`,
  `team_membership.user_id`, `daily_completion(user_id,date)`, `team_message.team_id`,
  `team_moment.team_id`, `coach_note.user_id` unique). No missing index identified in
  the reviewed paths.

## Recommended migration (deferred — review required, do NOT run against prod tonight)
Add the `ondelete` above to each FK. Implementation notes / why it's deferred:
1. Requires adding `ondelete='CASCADE'` / `ondelete='SET NULL'` to the **models** too,
   or the from-empty parity test (`tests/test_migrations.py`) will fail on the drift.
2. SQLite can't `ALTER` a constraint in place — the migration must use
   `op.batch_alter_table` (table-rebuild) for local testing; Postgres uses
   `ALTER TABLE … DROP/ADD CONSTRAINT`.
3. It's a **schema migration against production data** — out of scope for tonight
   per the operating rules. The application layer (`delete_user_account`) already
   enforces this exact policy safely, so the DB-level change is belt-and-suspenders,
   not a correctness blocker.

Test plan for the migration when implemented: run `flask db upgrade` then
`flask db downgrade` against a **temporary** database seeded with a user + teams +
messages + moments; assert cascades/nulls behave and the downgrade restores the
prior constraints; confirm `tests/test_migrations.py` (from-empty parity) passes.
