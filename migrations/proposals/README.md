# Migration proposals: NOT APPLIED

Files here are **proposed** Alembic revisions. Alembic only loads
`migrations/versions/` (`script_location = migrations`, default version
location), so nothing in this folder is ever run by `flask db upgrade`, counted
by `scripts/build_check.py`, or seen as a head.

To adopt one (only with the owner's approval):
1. Re-point its `down_revision` at the then-current head (`flask db heads`).
2. Move it into `migrations/versions/`.
3. Make the model match (each proposal says how), then run the full gates.

| File | What | Proposal write-up |
|---|---|---|
| `e5a7c3d91f20_team_message_photo_id_index.py` | Index on `team_message.photo_id` | `STREAKFIT_PHOTOID_INDEX_PROPOSAL.md` (coordinator handoff, 2026-11-14) |
