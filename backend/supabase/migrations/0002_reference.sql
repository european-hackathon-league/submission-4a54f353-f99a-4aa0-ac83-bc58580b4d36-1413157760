-- Reference tables: vehicles, competitors, sources, slugs, search terms,
-- car areas, brand profile, users. See database-design.md section 1.

create table vehicle (
    code             text primary key,
    brand            text not null default 'BMW',
    series           text not null,
    segment          text check (segment in ('entry', 'business', 'luxury')),
    generation       text,
    on_sale_since    date,
    predecessor_code text references vehicle (code),
    is_current       boolean not null default false,
    markets_sold     market_t[] not null default '{}',
    variants         text[] not null default '{}',
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now()
);
create trigger vehicle_updated_at before update on vehicle
    for each row execute function set_updated_at();

create table competitor (
    id             uuid primary key default gen_random_uuid(),
    bmw_vehicle    text not null references vehicle (code),
    brand          text not null,
    model          text not null,
    generation     text,
    since          text,
    names          jsonb not null default '{}',
    wikipedia_page text,
    markets        market_t[] not null default '{}',
    status         text not null default 'active' check (status in ('active', 'ended', 'announced')),
    origin         text not null default 'registry',
    confirmed      boolean not null default true,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now(),
    unique (bmw_vehicle, brand, model)
);
create trigger competitor_updated_at before update on competitor
    for each row execute function set_updated_at();

create table source_registry (
    source_id        text primary key,
    name             text not null,
    url_pattern      text,
    categories       text[] not null default '{}',
    country          text,
    level            text check (level in ('model', 'brand', 'market')),
    access           text,
    check_status     text,
    fetch_step       int check (fetch_step between 1 and 13),
    enabled          boolean not null default false,
    delay_seconds    numeric not null default 2,
    last_cursor      jsonb not null default '{}',
    backfill_done_at timestamptz,
    notes            text,
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now()
);
create trigger source_registry_updated_at before update on source_registry
    for each row execute function set_updated_at();

create table site_slug (
    subject_type text not null check (subject_type in ('vehicle', 'competitor')),
    subject_id   text not null,
    source_id    text not null references source_registry (source_id),
    slug         text not null,
    note         text,
    created_at   timestamptz not null default now(),
    primary key (subject_type, subject_id, source_id, slug)
);

create table search_term (
    id             uuid primary key default gen_random_uuid(),
    subject_type   text not null check (subject_type in ('vehicle', 'competitor')),
    subject_id     text not null,
    term           text not null,
    language       text not null default 'en',
    collision_safe boolean not null default true,
    scope          text not null default 'any' check (scope in ('any', 'single_brand_source')),
    created_at     timestamptz not null default now(),
    unique (subject_type, subject_id, term, scope)
);

create table car_area (
    code        text primary key,
    name        text not null,
    description text,
    created_at  timestamptz not null default now()
);

create table brand_profile (
    id         uuid primary key default gen_random_uuid(),
    version    int not null unique,
    body_md    text not null,
    created_by text not null,
    created_at timestamptz not null default now()
);

create table app_user (
    id         uuid primary key,
    name       text not null,
    role       text not null check (role in ('pm', 'engineer', 'admin')),
    vehicles   text[] not null default '{}',
    created_at timestamptz not null default now()
);
