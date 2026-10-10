-- Review fixes (Devin, phases 0 and 1). See changes.md, section "Review fixes".
-- Migrations 0001 to 0007 are applied and checksum-protected, so the fixes live here.

-- 1. source_record.content_hash is a duplicate-detection hint, not an identity.
--    Distinct customers can write identical text (G70: 4,000 verbatim IDs, 3,993
--    distinct texts). Records are deduplicated by their source-native identity
--    (record_id, primary key); content_hash keeps a plain, non-unique index.
do $$
declare
    c text;
begin
    for c in
        select con.conname
        from pg_constraint con
        join pg_attribute att on att.attrelid = con.conrelid and att.attnum = any (con.conkey)
        where con.conrelid = 'public.source_record'::regclass
          and con.contype = 'u'
          and att.attname = 'content_hash'
          and array_length(con.conkey, 1) = 1
    loop
        execute format('alter table public.source_record drop constraint %I', c);
    end loop;
end;
$$;
create index if not exists source_record_content_hash on source_record (content_hash);

-- 2. Weight profiles: every weight a finite JSON number >= 0 and a positive sum
--    (the scorer divides by the sum). JSON numbers cannot be NaN or Infinity;
--    strings such as "NaN" are rejected because only the JSON type number passes.
create or replace function weights_valid(w jsonb) returns boolean
language sql immutable as $$
    select case
        when jsonb_typeof(w) <> 'object' then false
        else coalesce((
            select bool_and(case when jsonb_typeof(value) = 'number'
                                 then (value::text)::numeric >= 0 else false end)
                   and sum(case when jsonb_typeof(value) = 'number'
                                then (value::text)::numeric else 0 end) > 0
            from jsonb_each(w)
        ), false)
    end
$$;
alter table weight_profile drop constraint weight_profile_weights_ok;
alter table weight_profile add constraint weight_profile_weights_valid
    check (weights_valid(case_weights));
drop function weights_non_negative(jsonb);

-- 3. Case IDs: four digits up to CASE-9999, then the plain number (CASE-10000).
--    lpad(..., 4) truncated 10000 to "1000" and collided with CASE-1000.
create or replace function next_case_id() returns text
language sql as $$
    select 'CASE-' || case when n < 10000 then lpad(n::text, 4, '0') else n::text end
    from (select nextval('case_number_seq') as n) s
$$;

-- 4. Requirement rank is computed on read, per weight profile:
--    latest case score of that profile x the profile's cost multiplier.
--    A profile applies when its config is the requirement's vehicle and, for a
--    market-split requirement, the requirement's market. requirement.rank_score
--    is only a cache for the default profile of the config (see database-design.md).
--    A missing cost size counts as multiplier 1.0 and is reported (cost_missing).
create view requirement_rank with (security_invoker = true) as
select
    r.id                as requirement_id,
    r.case_id,
    r.vehicle,
    r.market            as requirement_market,
    c.id                as config_id,
    c.market            as config_market,
    wp.id               as weight_profile_id,
    wp.name             as weight_profile_name,
    wp.is_default,
    cs.score            as case_score,
    r.cost_size,
    r.cost_size is null as cost_missing,
    coalesce((wp.cost_multipliers ->> r.cost_size)::numeric, 1.0) as cost_multiplier,
    cs.score * coalesce((wp.cost_multipliers ->> r.cost_size)::numeric, 1.0) as rank_score,
    r.manual_rank,
    r.manual_rank_reason,
    cs.computed_at      as score_computed_at
from requirement r
join config c on c.vehicle = r.vehicle and (r.market is null or c.market = r.market)
join weight_profile wp on wp.config_id = c.id
join case_score_current cs on cs.case_id = r.case_id and cs.weight_profile_id = wp.id
where r.deleted_at is null;

do $$
begin
    if exists (select 1 from pg_roles where rolname = 'anon') then
        execute 'revoke all on public.requirement_rank from anon';
    end if;
    if exists (select 1 from pg_roles where rolname = 'authenticated') then
        execute 'revoke all on public.requirement_rank from authenticated';
    end if;
end;
$$;
