-- Runs, source records, data records (incl. BMW sales), configuration.
-- See database-design.md sections 1 and 3.

create table agent_run (
    id                  uuid primary key default gen_random_uuid(),
    agent               text not null,
    mode                text not null check (mode in ('seed', 'backfill', 'incremental', 'on_demand', 'manual')),
    params              jsonb not null default '{}',
    started_at          timestamptz not null default now(),
    ended_at            timestamptz,
    records_fetched     int not null default 0,
    signals_valid       int not null default 0,
    signals_quarantined int not null default 0,
    llm_tokens          int not null default 0,
    llm_cost_usd        numeric not null default 0,
    status              text not null default 'running' check (status in ('running', 'ok', 'partial', 'failed')),
    error               text
);

create table source_record (
    record_id     text primary key,
    source_id     text not null references source_registry (source_id),
    category      text check (category ~ '^[A-J]$'),
    content_type  text check (content_type in ('owner', 'expert', 'press', 'news', 'bmw_internal', 'reference')),
    level         text check (level in ('model', 'brand', 'market')),
    url           text,
    raw_ref       text,
    fetched_at    timestamptz not null default now(),
    published_at  timestamptz,
    search_term   text,
    thread_id     text,
    author_hash   text,
    brand         text,
    model         text,
    chassis_code  text,
    model_year    text,
    vehicle_role  vehicle_role_t not null default 'unknown',
    powertrain    powertrain_t not null default 'unknown',
    country       text,
    market        market_t not null default 'unknown',
    language      text,
    title         text,
    text_original text,
    text_en       text,
    rating_value  numeric,
    rating_scale  numeric,
    engagement    jsonb not null default '{}',
    extra         jsonb not null default '{}',
    content_hash  text not null unique,
    run_id        uuid references agent_run (id),
    deleted_at    timestamptz,
    created_at    timestamptz not null default now(),
    check (deleted_at is not null or text_original is not null)
);
create index source_record_source_published on source_record (source_id, published_at);
create index source_record_chassis on source_record (chassis_code);

create table data_record (
    record_id       text primary key,
    source_id       text not null references source_registry (source_id),
    category        text check (category ~ '^[A-J]$'),
    url             text,
    raw_ref         text,
    fetched_at      timestamptz not null default now(),
    brand           text,
    model           text,
    chassis_code    text,
    competitor_id   uuid references competitor (id),
    powertrain      powertrain_t not null default 'unknown',
    country         text,
    market          market_t not null default 'unknown',
    period          text,
    metric          text not null,
    value           numeric,
    value_text      text,
    unit            text,
    source_location text,
    suspect         boolean not null default false,
    run_id          uuid references agent_run (id),
    created_at      timestamptz not null default now(),
    check (value is not null or value_text is not null)
);
create index data_record_lookup on data_record (source_id, chassis_code, metric);

-- BMW sales per vehicle, market and year (source_id = bmw_sales).
create view sales_volume as
select
    chassis_code                                  as vehicle,
    market,
    (regexp_match(period, '^(\d{4})'))[1]::int    as year,
    value::int                                    as units,
    period like '%forecast%'                      as forecast,
    suspect,
    record_id
from data_record
where source_id = 'bmw_sales' and metric = 'sales units';

create table config (
    id               uuid primary key default gen_random_uuid(),
    name             text not null,
    vehicle          text not null references vehicle (code),
    market           market_t not null check (market <> 'unknown'),
    competitor_ids   uuid[] not null default '{}',
    brand_profile_id uuid references brand_profile (id),
    source_filter    jsonb not null default '{}',
    thresholds       jsonb not null default '{}',
    active           boolean not null default true,
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now(),
    unique (vehicle, market)
);
create trigger config_updated_at before update on config
    for each row execute function set_updated_at();

-- A vehicle + market is valid only if the 2030 sales forecast is above zero.
create or replace function config_check_sales() returns trigger
language plpgsql as $$
begin
    if not exists (
        select 1 from sales_volume
        where vehicle = new.vehicle and market = new.market and year = 2030 and units > 0
    ) then
        raise exception 'config %/% invalid: no 2030 sales volume above zero', new.vehicle, new.market
            using errcode = 'check_violation';
    end if;
    return new;
end;
$$;
create trigger config_sales_check before insert or update of vehicle, market on config
    for each row execute function config_check_sales();

create table weight_profile (
    id               uuid primary key default gen_random_uuid(),
    config_id        uuid not null references config (id),
    name             text not null,
    case_weights     jsonb not null,
    brand_multipliers jsonb not null default '{"fits": 1.0, "neutral": 0.8, "off_brand": 0.5}',
    cost_multipliers jsonb not null default '{"S": 1.0, "M": 0.85, "L": 0.7}',
    is_default       boolean not null default false,
    created_by       text not null,
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now(),
    unique (config_id, name)
);
create unique index weight_profile_one_default on weight_profile (config_id) where is_default;
create trigger weight_profile_updated_at before update on weight_profile
    for each row execute function set_updated_at();

create or replace function weights_non_negative(w jsonb) returns boolean
language sql immutable as $$
    select coalesce(bool_and((value)::numeric >= 0), true) from jsonb_each_text(w)
$$;
alter table weight_profile add constraint weight_profile_weights_ok
    check (weights_non_negative(case_weights));
