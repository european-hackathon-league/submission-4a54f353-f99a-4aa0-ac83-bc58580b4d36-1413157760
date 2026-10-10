-- Fixed taxonomy (taxonomy/ at the repo root): 11 car areas, 219 topics (BMW VFC
-- level 2), 11 aspect groups. A topic is a row of the feature table, so equipment,
-- study scores and cases keep their feature_id link. See changes.md "Taxonomy".

create table aspect_group (
    code        text primary key,
    description text,
    created_at  timestamptz not null default now()
);

-- feature = topic. topic_id is the taxonomy slug; 'other' is the fallback row and has no car area.
alter table feature
    add column topic_id         text unique,
    add column hint             text,
    add column vfc_level2       text[] not null default '{}',
    add column taxonomy_version text,
    alter column car_area drop not null;

alter table signal
    add column aspect_group              text references aspect_group (code),
    add column classified_by             text check (classified_by in ('bmw_label', 'llm', 'pm')),
    add column classification_confidence text check (classification_confidence in ('high', 'medium', 'low')),
    add column taxonomy_version          text;
create index signal_aspect_group on signal (aspect_group);

-- A case sits under one topic (feature_id) and one aspect group.
alter table case_item
    add column aspect_group text references aspect_group (code);
create index case_item_topic_aspect on case_item (feature_id, aspect_group);

-- Review queue: signals the classifier could not place ('other') or placed with low
-- confidence. Only a human resolves a row; a topic is never created automatically.
create table taxonomy_proposal (
    id                  uuid primary key default gen_random_uuid(),
    signal_id           uuid not null unique references signal (id),
    suggested_name      text,
    reason              text not null check (reason in ('other', 'low_confidence')),
    status              text not null default 'open'
                        check (status in ('open', 'mapped_to_existing', 'accepted_new_topic', 'dismissed')),
    resolved_feature_id uuid references feature (id),
    decided_by          text,
    decided_at          timestamptz,
    created_at          timestamptz not null default now(),
    check (status = 'open' or decided_by is not null)
);
create index taxonomy_proposal_status on taxonomy_proposal (status);

create view taxonomy_review as
select
    p.id,
    p.reason,
    p.suggested_name,
    f.topic_id,
    s.aspect_group,
    s.classification_confidence,
    s.summary,
    s.quote,
    s.vehicle,
    s.market,
    s.record_id,
    p.created_at
from taxonomy_proposal p
join signal s on s.id = p.signal_id
left join feature f on f.id = s.feature_id
where p.status = 'open';

alter table aspect_group enable row level security;
alter table taxonomy_proposal enable row level security;

do $$
begin
    if exists (select 1 from pg_roles where rolname = 'anon') then
        execute 'revoke all on aspect_group, taxonomy_proposal, taxonomy_review from anon, authenticated';
    end if;
end;
$$;
