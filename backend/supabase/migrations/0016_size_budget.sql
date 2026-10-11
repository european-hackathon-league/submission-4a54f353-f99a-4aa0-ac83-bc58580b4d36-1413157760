-- Size budget: the database must fit the 500 MB of the Supabase free plan with room
-- for the web signals the fetch loop adds. A local build of the BMW data alone was
-- 789 MB; 420 MB of it were embeddings and their indexes. See changes.md "Size budget".

-- 1. Two vector indexes are dropped (228 MB). signal.embedding is never searched by
--    nearest neighbour (cases are matched through case_item.embedding), and the RAG
--    index has tens of thousands of chunks: an exact scan answers in milliseconds and
--    finds the true nearest chunks. Add an HNSW index again when rag_chunk passes
--    a few hundred thousand rows.
drop index if exists signal_embedding;
drop index if exists rag_chunk_embedding;

-- 2. Embeddings have 768 dimensions instead of 1536 (text-embedding-3-small shortened
--    by the API, EMBEDDING_DIM): half the storage per vector. Vectors of the old size
--    cannot be converted, so they are cleared and written again by the next embedding
--    run (signals: python -m app.signals.embed; cases and topics: the case pipeline and
--    the seed; RAG: python -m app.rag.index).
delete from rag_chunk;
update signal set embedding = null where embedding is not null;
update case_item set embedding = null where embedding is not null;
update feature set embedding = null where embedding is not null;
alter table rag_chunk alter column embedding type vector(768);
alter table signal alter column embedding type vector(768);
alter table case_item alter column embedding type vector(768);
alter table feature alter column embedding type vector(768);

-- The search function takes a query vector of any size (it must match the stored one).
drop function if exists rag_search(text, vector, int, text, text, text[]);
create or replace function rag_search(
    q text,
    qv vector,
    k int default 12,
    f_vehicle text default null,
    f_case text default null,
    f_types text[] default null
)
returns table (id uuid, object_type text, object_id text, vehicle text, market text, case_id text,
               text text, score double precision)
language sql stable set search_path = public, pg_temp as $$
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
create index if not exists rag_chunk_types on rag_chunk (object_type, case_id);
