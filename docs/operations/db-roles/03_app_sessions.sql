-- READ-ONLY: which roles hold sessions on this database right now. After the
-- switch, the app's sessions must be streakfit_app, not neondb_owner.
begin read only;
select current_setting('neon.endpoint_id', true) as endpoint, usename, application_name, count(*)
  from pg_stat_activity where datname = current_database()
 group by 1, 2, 3 order by 2;
rollback;
