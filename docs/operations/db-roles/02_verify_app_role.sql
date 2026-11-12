-- StreakFit: privilege matrix for streakfit_app. READ-ONLY.
-- Returns ZERO rows when everything is as designed; every row is a defect.
begin read only;
with
  -- MATERIALIZED: otherwise the planner may call has_*_privilege() on rows the
  -- relkind filter has not removed yet ("... is not a sequence").
  tables as materialized (select c.oid, c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
             where n.nspname = 'public' and c.relkind in ('r', 'p')),
  seqs as materialized (select c.oid, c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
           where n.nspname = 'public' and c.relkind = 'S'),
  r as materialized (select * from pg_roles where rolname = 'streakfit_app')
select 'role missing' as defect, null::text as object from (select 1) x where not exists (select 1 from r)
union all select 'role attribute ' || a, null from r,
  lateral (values ('superuser', rolsuper), ('createrole', rolcreaterole), ('createdb', rolcreatedb),
                  ('replication', rolreplication), ('bypassrls', rolbypassrls), ('cannot login', not rolcanlogin)) v(a, bad)
  where bad
union all select 'member of ' || g.rolname, null from pg_auth_members m join pg_roles g on g.oid = m.roleid
  join r on r.oid = m.member
union all select 'missing ' || p || ' on table', t.relname from tables t,
  unnest(array['SELECT','INSERT','UPDATE','DELETE']) p
  where t.relname <> 'alembic_version' and exists (select 1 from r)
    and not has_table_privilege('streakfit_app', t.oid, p)
union all select 'missing SELECT on alembic_version', null
  where exists (select 1 from r) and not has_table_privilege('streakfit_app', 'alembic_version', 'SELECT')
union all select 'may write alembic_version (' || p || ')', null from unnest(array['INSERT','UPDATE','DELETE','TRUNCATE']) p
  where exists (select 1 from r) and has_table_privilege('streakfit_app', 'alembic_version', p)
union all select 'may ' || p || ' table', t.relname from tables t, unnest(array['TRUNCATE','REFERENCES','TRIGGER']) p
  where exists (select 1 from r) and has_table_privilege('streakfit_app', t.oid, p)
union all select 'owns ' || c.relname, null from pg_class c join r on r.oid = c.relowner
union all select 'missing USAGE on sequence', s.relname from seqs s
  where exists (select 1 from r) and not has_sequence_privilege('streakfit_app', s.oid, 'USAGE')
union all select 'may CREATE in schema public', null
  where exists (select 1 from r) and has_schema_privilege('streakfit_app', 'public', 'CREATE')
union all select 'may CREATE in database', null
  where exists (select 1 from r) and has_database_privilege('streakfit_app', current_database(), 'CREATE')
union all select 'default privileges for future tables missing', null
  where exists (select 1 from r) and not exists (
    select 1 from pg_default_acl d where d.defaclrole = 'neondb_owner'::regrole and d.defaclobjtype = 'r'
      and d.defaclacl::text like '%streakfit_app=arwd/%')
union all select 'default privileges for future sequences missing', null
  where exists (select 1 from r) and not exists (
    select 1 from pg_default_acl d where d.defaclrole = 'neondb_owner'::regrole and d.defaclobjtype = 'S'
      and d.defaclacl::text like '%streakfit_app=%U%/%');
rollback;
