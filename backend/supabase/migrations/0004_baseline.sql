-- Baseline: feature catalog, mappings, equipment today, customer study.
-- See database-design.md section 2.

create table feature (
    id          uuid primary key default gen_random_uuid(),
    name        text not null unique,
    car_area    text not null references car_area (code),
    description text,
    status      text not null default 'candidate' check (status in ('confirmed', 'candidate', 'rejected')),
    embedding   vector(1536),
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);
create trigger feature_updated_at before update on feature
    for each row execute function set_updated_at();
create index feature_embedding on feature using hnsw (embedding vector_cosine_ops);

create table feature_mapping (
    id           uuid primary key default gen_random_uuid(),
    feature_id   uuid not null references feature (id),
    kind         text not null check (kind in ('vfc_topic', 'option_code', 'study_attribute')),
    value        text not null,
    vehicle      text references vehicle (code),
    confirmed_by text,
    created_at   timestamptz not null default now()
);
create unique index feature_mapping_unique
    on feature_mapping (kind, value, coalesce(vehicle, ''), feature_id);
create index feature_mapping_value on feature_mapping (kind, value);

create table vehicle_feature (
    id           uuid primary key default gen_random_uuid(),
    vehicle      text not null references vehicle (code),
    feature_id   uuid references feature (id),
    variant      text not null,
    market       text not null default 'DE',
    availability text not null check (availability in ('standard', 'optional', 'absent', 'removed')),
    sa_code      text,
    item_name    text not null,
    price_eur    numeric,
    package      text,
    record_id    text references source_record (record_id),
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);
create trigger vehicle_feature_updated_at before update on vehicle_feature
    for each row execute function set_updated_at();
create index vehicle_feature_lookup on vehicle_feature (vehicle, feature_id);

create table study_score (
    id                 uuid primary key default gen_random_uuid(),
    vehicle            text not null references vehicle (code),
    market             market_t not null,
    sheet              text not null,
    attribute          text not null,
    method             text not null check (method in ('mean_10pt', 'dist_7level')),
    raw                jsonb not null,
    normalized         numeric check (normalized between 0 and 1),
    dissatisfied_share numeric check (dissatisfied_share between 0 and 1),
    feature_id         uuid references feature (id),
    source_location    text,
    created_at         timestamptz not null default now(),
    unique (vehicle, market, sheet, attribute)
);
