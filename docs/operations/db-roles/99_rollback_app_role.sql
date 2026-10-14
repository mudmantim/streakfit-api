-- Remove streakfit_app ONLY after DATABASE_URL no longer uses it (03 shows no sessions).
-- Run as neondb_owner on the same endpoint.
-- Explicit revokes rather than DROP OWNED: on PostgreSQL 16+ (Neon runs 17)
-- DROP OWNED needs the privileges OF the role being dropped, which its creator
-- does not have by default. streakfit_app owns nothing; it only holds grants.
begin;
alter default privileges for role neondb_owner in schema public
  revoke select, insert, update, delete on tables from streakfit_app;
alter default privileges for role neondb_owner in schema public
  revoke usage, select on sequences from streakfit_app;
revoke all on all tables in schema public from streakfit_app;
revoke all on all sequences in schema public from streakfit_app;
revoke all on schema public from streakfit_app;
do $$ begin execute format('revoke all on database %I from streakfit_app', current_database()); end $$;
drop role streakfit_app;
commit;
