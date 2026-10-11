-- Fetch cadence per source (changes.md 8.8): the daily worker only runs a source
-- when its last successful run is at least refresh_days old. Values per source
-- come from the seed step seed:source_decisions (backend/seed/source_decisions.yaml).

alter table source_registry
    add column refresh_days int not null default 1 check (refresh_days between 1 and 365),
    add column disabled_reason text;
