-- Extensions, shared value sets and helper functions.
-- See database-design.md, "Conventions".

create extension if not exists vector;

create domain market_t as text
    check (value in ('USA', 'China', 'Europe', 'Rest of World', 'unknown'));

create domain powertrain_t as text
    check (value in ('ICE', 'PHEV', 'BEV', 'unknown'));

create domain vehicle_role_t as text
    check (value in ('current', 'predecessor', 'competitor', 'unknown'));

create domain actor_type_t as text
    check (value in ('ai', 'human', 'system'));

-- Keeps updated_at current on every mutable table.
create or replace function set_updated_at() returns trigger
language plpgsql as $$
begin
    new.updated_at := now();
    return new;
end;
$$;

-- Country (ISO code) to market, fetching-sources.md section 7.4.
create or replace function country_to_market(country text) returns market_t
language sql immutable as $$
    select case
        when country is null or btrim(country) = '' then 'unknown'
        when upper(country) = 'US' then 'USA'
        when upper(country) = 'CN' then 'China'
        when upper(country) in (
            'GB', 'DE', 'FR', 'IT', 'ES', 'NL', 'SE', 'NO', 'AT', 'BE', 'CH', 'DK', 'FI',
            'IE', 'PT', 'PL', 'CZ', 'SK', 'HU', 'RO', 'BG', 'GR', 'HR', 'SI', 'LU', 'EE',
            'LV', 'LT', 'IS', 'MT', 'CY', 'RS', 'BA', 'ME', 'MK', 'AL', 'UA', 'MD', 'LI',
            'MC', 'SM', 'AD', 'TR'
        ) then 'Europe'
        else 'Rest of World'
    end::market_t
$$;
