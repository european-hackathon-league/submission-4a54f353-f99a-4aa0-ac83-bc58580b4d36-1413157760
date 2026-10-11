-- Decisions, chat, notifications, RAG index and the append-only audit log.
-- See database-design.md sections 5 and 6.

create table decision (
    id          uuid primary key default gen_random_uuid(),
    object_type text not null,
    object_id   text not null,
    vehicle     text references vehicle (code),
    actor       text not null,
    action      text not null check (action in (
                    'open', 'confirm', 'reject', 'edit', 'reorder', 'merge', 'split',
                    'approve', 'delete', 'export', 'override', 'retarget', 'reopen')),
    rationale   text,
    created_at  timestamptz not null default now(),
    check (action not in ('reject', 'reorder', 'delete', 'override') or rationale is not null)
);
create index decision_object on decision (object_type, object_id);

create table chat_message (
    id                uuid primary key default gen_random_uuid(),
    object_type       text not null check (object_type in ('case', 'requirement')),
    object_id         text not null,
    role              text not null check (role in ('pm', 'agent')),
    author            text not null,
    content           text not null,
    cited_signals     uuid[] not null default '{}',
    retrieved_signals uuid[] not null default '{}',
    proposed_diff     jsonb,
    diff_status       text not null default 'none' check (diff_status in ('none', 'proposed', 'accepted', 'rejected')),
    model             text,
    prompt_version    text,
    created_at        timestamptz not null default now(),
    check (cited_signals <@ retrieved_signals)
);
create index chat_message_object on chat_message (object_type, object_id, created_at);

create table notification (
    id          uuid primary key default gen_random_uuid(),
    type        text not null check (type in (
                    'case_opened', 'new_evidence', 'market_conflict', 'run_failed',
                    'candidate_feature', 'new_competitor', 'requirement_generated')),
    object_type text,
    object_id   text,
    vehicle     text,
    market      text,
    message     text not null,
    read_by     uuid[] not null default '{}',
    created_at  timestamptz not null default now()
);
create index notification_recent on notification (created_at desc);

create table rag_chunk (
    id          uuid primary key default gen_random_uuid(),
    object_type text not null check (object_type in (
                    'case', 'requirement', 'signal', 'source_record', 'decision', 'audit_event')),
    object_id   text not null,
    vehicle     text,
    market      text,
    case_id     text,
    text        text not null,
    embedding   vector(1536),
    tsv         tsvector generated always as (to_tsvector('english', coalesce(text, ''))) stored,
    updated_at  timestamptz not null default now(),
    unique (object_type, object_id)
);
create index rag_chunk_tsv on rag_chunk using gin (tsv);
create index rag_chunk_embedding on rag_chunk using hnsw (embedding vector_cosine_ops);
create index rag_chunk_filters on rag_chunk (vehicle, case_id);

create table audit_event (
    seq         bigserial primary key,
    at          timestamptz not null default now(),
    actor       text not null,
    actor_type  actor_type_t not null,
    action      text not null,
    object_type text not null,
    object_id   text not null,
    before      jsonb,
    after       jsonb,
    rationale   text,
    run_id      uuid,
    request_id  text
);
create index audit_event_object on audit_event (object_type, object_id, seq);
create index audit_event_actor on audit_event (actor, at);

-- Append-only: no role may change or remove history.
create or replace function audit_event_immutable() returns trigger
language plpgsql as $$
begin
    raise exception 'audit_event is append-only (% refused)', tg_op
        using errcode = 'insufficient_privilege';
end;
$$;
create trigger audit_event_no_update before update or delete on audit_event
    for each row execute function audit_event_immutable();
create trigger audit_event_no_truncate before truncate on audit_event
    for each statement execute function audit_event_immutable();
revoke update, delete, truncate on audit_event from public;
