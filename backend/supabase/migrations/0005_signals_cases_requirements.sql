-- Cases (problems), signals (evidence), scores, requirements (solutions), page history.
-- See database-design.md sections 3 and 4.

create sequence case_number_seq;

create or replace function next_case_id() returns text
language sql as $$
    select 'CASE-' || lpad(nextval('case_number_seq')::text, 4, '0')
$$;

create table case_item (
    id              text primary key default next_case_id(),
    feature_id      uuid not null references feature (id),
    title           text not null,
    body_md         text not null default '',
    body_version    int not null default 1,
    figures         jsonb not null default '{}',
    scope_tag       text not null default 'in_scope' check (scope_tag in ('in_scope', 'out_of_scope')),
    brand_fit       text check (brand_fit in ('fits', 'neutral', 'off_brand')),
    brand_fit_source text check (brand_fit_source in ('ai', 'pm')),
    segment         text check (segment in ('base', 'premium', 'luxury')),
    segment_source  text check (segment_source in ('ai', 'pm')),
    tags            text[] not null default '{}',
    embedding       vector(1536),
    created_by      text not null,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);
create trigger case_item_updated_at before update on case_item
    for each row execute function set_updated_at();
create index case_item_feature on case_item (feature_id);
create index case_item_embedding on case_item using hnsw (embedding vector_cosine_ops);

create table case_vehicle (
    case_id            text not null references case_item (id),
    vehicle            text not null references vehicle (code),
    state              text not null default 'observed'
                       check (state in ('observed', 'open', 'confirmed', 'rejected')),
    state_reason       text,
    opened_by          text,
    market_conflict    boolean not null default false,
    release_fit        text check (release_fit in ('current_car', 'next_gen', 'ota')),
    release_fit_reason text,
    release_fit_source text check (release_fit_source in ('ai', 'pm')),
    manual_rank        int,
    manual_rank_reason text,
    flag_new_evidence  int not null default 0,
    confirmed_at       timestamptz,
    created_at         timestamptz not null default now(),
    updated_at         timestamptz not null default now(),
    primary key (case_id, vehicle),
    check (state <> 'rejected' or state_reason is not null),
    check (manual_rank is null or manual_rank_reason is not null)
);
create trigger case_vehicle_updated_at before update on case_vehicle
    for each row execute function set_updated_at();
create index case_vehicle_state on case_vehicle (vehicle, state);

create table case_lineage (
    id         uuid primary key default gen_random_uuid(),
    from_case  text not null references case_item (id),
    to_case    text not null references case_item (id),
    op         text not null check (op in ('merge', 'split')),
    actor      text not null,
    rationale  text,
    created_at timestamptz not null default now()
);

create table signal (
    id             uuid primary key default gen_random_uuid(),
    record_id      text not null references source_record (record_id),
    run_id         uuid references agent_run (id),
    schema_version text not null,
    prompt_version text,
    model          text,
    customer_ref   text,
    market         market_t not null default 'unknown',
    market_basis   text not null default 'unknown' check (market_basis in ('stated', 'inferred', 'unknown')),
    vehicle        text references vehicle (code),
    chassis_code   text,
    competitor_id  uuid references competitor (id),
    vehicle_role   vehicle_role_t not null default 'unknown',
    evidence_role  text not null check (evidence_role in ('bmw_customer', 'competitor', 'market')),
    powertrain     powertrain_t not null default 'unknown',
    kind           text check (kind in ('complaint', 'wish', 'like', 'competitor_fact', 'trend', 'innovation')),
    severity       text check (severity in ('defect', 'difficult_to_use', 'wish', 'none')),
    car_area       text[] not null default '{}',
    feature_hint   text,
    feature_id     uuid references feature (id),
    case_id        text references case_item (id),
    summary        text,
    quote          text,
    quote_en       text,
    quote_start    int,
    reach          int,
    reach_unit     text check (reach_unit in ('views', 'upvotes', 'likes', 'replies', 'none')),
    data_record_id text references data_record (record_id),
    published_at   timestamptz,
    scope_tag      text not null default 'in_scope' check (scope_tag in ('in_scope', 'out_of_scope')),
    extra          jsonb not null default '{}',
    validation     text not null check (validation in ('valid', 'quarantined', 'deleted')),
    reject_reason  text,
    embedding      vector(1536),
    deleted_at     timestamptz,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now(),
    check (validation <> 'valid' or (quote is not null and summary is not null and kind is not null)),
    check (validation <> 'quarantined' or reject_reason is not null),
    check (evidence_role <> 'competitor' or competitor_id is not null),
    check (validation <> 'deleted' or (quote is null and summary is null and deleted_at is not null))
);
create trigger signal_updated_at before update on signal
    for each row execute function set_updated_at();
create index signal_case on signal (case_id);
create index signal_feature on signal (feature_id);
create index signal_market on signal (market);
create index signal_vehicle on signal (vehicle);
create index signal_record on signal (record_id);
create index signal_validation on signal (validation);
create index signal_embedding on signal using hnsw (embedding vector_cosine_ops);

create table case_score (
    id                uuid primary key default gen_random_uuid(),
    case_id           text not null references case_item (id),
    weight_profile_id uuid not null references weight_profile (id),
    factors           jsonb not null,
    brand_multiplier  numeric not null,
    score             numeric not null,
    rationale         text not null,
    computed_at       timestamptz not null default now()
);
create index case_score_lookup on case_score (case_id, weight_profile_id, computed_at desc);

create view case_score_current as
select distinct on (case_id, weight_profile_id) *
from case_score
order by case_id, weight_profile_id, computed_at desc;

create table requirement (
    id                  text primary key,
    case_id             text not null references case_item (id),
    vehicle             text not null references vehicle (code),
    market              market_t,
    title               text,
    body_md             text,
    body_version        int not null default 1,
    metric_target       jsonb not null default '{}',
    acceptance_criteria jsonb not null default '[]',
    release_target      text check (release_target in ('next_gen', 'current_car', 'ota')),
    evidence_level      text check (evidence_level in ('bmw_data', 'web', 'assumption')),
    assumptions         jsonb not null default '[]',
    cost_size           text check (cost_size in ('S', 'M', 'L')),
    cost_source         text check (cost_source in ('option_price', 'ai_estimate', 'pm', 'engineering')),
    cost_price_eur      numeric,
    status              text not null default 'draft'
                        check (status in ('draft', 'in_discussion', 'approved', 'rejected', 'deleted')),
    rank_score          numeric,
    manual_rank         int,
    manual_rank_reason  text,
    model               text,
    prompt_version      text,
    deleted_at          timestamptz,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    check (status <> 'deleted' or deleted_at is not null),
    check (manual_rank is null or manual_rank_reason is not null)
);
create trigger requirement_updated_at before update on requirement
    for each row execute function set_updated_at();
create index requirement_case on requirement (case_id);
create index requirement_vehicle_status on requirement (vehicle, status);

create table page_version (
    object_type text not null check (object_type in ('case', 'requirement')),
    object_id   text not null,
    version     int not null,
    body_md     text,
    author      text not null,
    created_at  timestamptz not null default now(),
    primary key (object_type, object_id, version)
);
