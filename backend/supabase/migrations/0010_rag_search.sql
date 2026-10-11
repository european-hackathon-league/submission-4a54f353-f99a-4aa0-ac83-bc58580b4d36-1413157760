-- Hybrid search for the RAG sidebar: full-text + pgvector, reciprocal rank fusion.

create or replace function rag_search(
    q text,
    qv vector(1536),
    k int default 12,
    f_vehicle text default null,
    f_case text default null,
    f_types text[] default null
)
returns table (id uuid, object_type text, object_id text, vehicle text, market text, case_id text,
               text text, score double precision)
language sql stable as $$
    with filtered as (
        select * from rag_chunk c
        where (f_vehicle is null or c.vehicle is null or c.vehicle = f_vehicle)
          and (f_case is null or c.case_id = f_case)
          and (f_types is null or c.object_type = any(f_types))
    ),
    kw as (
        select c.id, row_number() over (
                   order by ts_rank_cd(c.tsv, websearch_to_tsquery('english', q)) desc) as r
        from filtered c
        where q is not null and c.tsv @@ websearch_to_tsquery('english', q)
        limit 60
    ),
    vec as (
        select c.id, row_number() over (order by c.embedding <=> qv) as r
        from filtered c
        where qv is not null and c.embedding is not null
        order by c.embedding <=> qv
        limit 60
    ),
    fused as (
        select id, sum(1.0 / (60 + r)) as score
        from (select id, r from kw union all select id, r from vec) u
        group by id
    )
    select c.id, c.object_type, c.object_id, c.vehicle, c.market, c.case_id, c.text, f.score
    from fused f join rag_chunk c on c.id = f.id
    order by f.score desc
    limit k
$$;

do $$
begin
    if exists (select 1 from pg_roles where rolname = 'anon') then
        execute 'revoke execute on function rag_search(text, vector, int, text, text, text[]) from public, anon, authenticated';
    end if;
end;
$$;
