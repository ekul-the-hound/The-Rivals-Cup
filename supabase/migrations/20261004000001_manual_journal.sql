-- Turn 5: manual journal additions. Rows are records of trades the owner ALREADY entered by hand
-- in Trader View. Nothing here is an order, queue, fill, or simulation.

-- A manual pair record links two separately-recorded legs. It is NOT a trade and does not change
-- the research status of any peer_pairs row.
create table public.manual_pair_records (
  id uuid primary key default gen_random_uuid(),
  portfolio_id uuid not null references public.manual_portfolios (id) on delete cascade,
  peer_pair_id uuid references public.peer_pairs (id) on delete set null,
  name text not null,
  scoring_week_start date check (scoring_week_start is null or extract(isodow from scoring_week_start) = 1),
  notes text,
  status text not null default 'OPEN' check (status in ('OPEN', 'CLOSED')),
  opened_at timestamptz,
  closed_at timestamptz,
  source text not null default 'MANUAL' check (source = 'MANUAL'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index on public.manual_pair_records (portfolio_id, status);

alter table public.manual_positions
  add column manual_pair_record_id uuid references public.manual_pair_records (id) on delete set null,
  add column scoring_week_start date,
  add column stop_concept text,
  add column target_concept text,
  add column exit_price numeric check (exit_price is null or exit_price > 0),
  add column exit_reason text,
  add column source text not null default 'MANUAL' check (source = 'MANUAL');

-- Dividend / distribution events (manually entered or loaded from a data provider).
create table public.dividend_events (
  id uuid primary key default gen_random_uuid(),
  security_id uuid not null references public.securities (id) on delete cascade,
  ex_date date not null,
  amount_per_share numeric not null check (amount_per_share >= 0),
  source text not null default 'MANUAL',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (security_id, ex_date)
);

do $$
declare t text;
begin
  foreach t in array array['manual_pair_records','dividend_events'] loop
    execute format('create trigger set_updated_at before update on public.%I for each row execute function public.set_updated_at()', t);
    execute format('alter table public.%I enable row level security', t);
    execute format('alter table public.%I force row level security', t);
    execute format('create policy owner_select on public.%I for select to authenticated using (public.is_owner())', t);
    execute format('create policy owner_insert on public.%I for insert to authenticated with check (public.is_owner())', t);
    execute format('create policy owner_update on public.%I for update to authenticated using (public.is_owner()) with check (public.is_owner())', t);
    execute format('grant select on public.%I to authenticated', t);
    execute format('grant insert, update on public.%I to authenticated', t);
    execute format('grant select on public.%I to mcp_readonly', t);
    execute format('create policy mcp_readonly_select on public.%I for select to mcp_readonly using (true)', t);
  end loop;
end $$;
