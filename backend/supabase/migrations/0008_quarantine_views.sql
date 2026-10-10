-- Quarantine views for the PM (visible counts per agent and reason).

create view signal_quarantine as
select
    s.id,
    s.record_id,
    s.reject_reason,
    s.kind,
    s.summary,
    s.quote,
    s.vehicle,
    s.market,
    coalesce(r.agent, 'unknown') as agent,
    s.created_at
from signal s
left join agent_run r on r.id = s.run_id
where s.validation = 'quarantined';

create view signal_quarantine_summary as
select
    agent,
    split_part(reject_reason, ':', 1) as reason,
    count(*)                          as signals
from signal_quarantine
group by 1, 2;

-- Views follow the table security model: no access for anon / authenticated.
do $$
begin
    if exists (select 1 from pg_roles where rolname = 'anon') then
        execute 'revoke all on signal_quarantine, signal_quarantine_summary from anon, authenticated';
    end if;
end;
$$;
