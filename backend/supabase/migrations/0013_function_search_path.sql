-- Pin the search_path of every function in public (Supabase security advisor
-- "function_search_path_mutable"): a caller cannot shadow the tables or
-- operators a function uses by putting another schema first. pgvector lives in
-- public, so public stays on the path; pg_temp comes last.

do $$
declare
    f record;
begin
    for f in
        select p.oid::regprocedure as sig
        from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        left join pg_depend d on d.objid = p.oid and d.deptype = 'e'
        where n.nspname = 'public' and d.objid is null  -- skip functions owned by extensions (pgvector)
    loop
        execute format('alter function %s set search_path = public, pg_temp', f.sig);
    end loop;
end;
$$;
