-- Row level security, role grants, Realtime and the raw-records bucket.
-- Supabase-only objects are guarded so this file also runs on a plain Postgres.

do $$
declare
    t text;
begin
    for t in
        select tablename from pg_tables where schemaname = 'public'
    loop
        execute format('alter table public.%I enable row level security', t);
    end loop;
end;
$$;

-- anon: nothing. authenticated: read notifications only (Realtime toasts).
-- The backend uses the service role, which bypasses RLS.
do $$
begin
    if exists (select 1 from pg_roles where rolname = 'anon') then
        execute 'revoke all on all tables in schema public from anon';
    end if;
    if exists (select 1 from pg_roles where rolname = 'authenticated') then
        execute 'revoke all on all tables in schema public from authenticated';
        execute 'grant select on public.notification to authenticated';
        execute 'create policy notification_read on public.notification for select to authenticated using (true)';
    end if;
    -- No role, the service role included, may change audit history.
    if exists (select 1 from pg_roles where rolname = 'service_role') then
        execute 'revoke update, delete, truncate on public.audit_event from service_role';
    end if;
    if exists (select 1 from pg_roles where rolname = 'anon') then
        execute 'revoke update, delete, truncate on public.audit_event from anon, authenticated';
    end if;
end;
$$;

do $$
begin
    if exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
        execute 'alter publication supabase_realtime add table public.notification';
    end if;
end;
$$;

do $$
begin
    if to_regclass('storage.buckets') is not null then
        execute $sql$
            insert into storage.buckets (id, name, public)
            values ('raw-records', 'raw-records', false)
            on conflict (id) do nothing
        $sql$;
    end if;
end;
$$;
