-- Option A: Monday peer-pair portfolio builder. Research-review state only; no trades/orders.

alter table public.peer_pairs
  add column relationship_explanation text,
  add column business_driver text,                       -- shared industry/business-driver classification
  add column macro_driver text,                          -- used to avoid redundant macro exposures
  add column relationship_quality smallint check (relationship_quality between 1 and 5),
  add column thesis_basis text not null default 'RELATIVE_VALUE' check (thesis_basis in ('RELATIVE_VALUE', 'CATALYST')),
  add column mapping_notes text;

alter table public.securities
  add column country text not null default 'US',
  add column leverage_factor numeric not null default 1 check (leverage_factor > 0),
  add column unresolved_corporate_action boolean not null default false,
  add column corporate_action_notes text;

alter table public.pair_rankings
  add column week_start date,
  add column long_security_id uuid references public.securities (id) on delete set null,
  add column short_security_id uuid references public.securities (id) on delete set null,
  add column eligible boolean not null default true,
  add column ineligible_reasons jsonb not null default '[]'::jsonb,
  add column data_quality_score numeric,
  add column adjusted_score numeric,
  add column packet jsonb;

-- Immutable-draft snapshots of each weekly build. status can only ever be RESEARCH_DRAFT.
create table public.weekly_portfolios (
  id uuid primary key default gen_random_uuid(),
  week_start date not null check (extract(isodow from week_start) = 1),
  run_id uuid not null,
  built_at timestamptz not null default now(),
  status text not null default 'RESEARCH_DRAFT' check (status = 'RESEARCH_DRAFT'),
  params jsonb not null default '{}'::jsonb,
  selected jsonb not null default '[]'::jsonb,
  alternates jsonb not null default '[]'::jsonb,
  warnings jsonb not null default '[]'::jsonb,
  summary jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index on public.weekly_portfolios (week_start, built_at desc);

-- Human include/exclude for a scoring week. A decision is review state, never a trade.
create table public.pair_manual_decisions (
  id uuid primary key default gen_random_uuid(),
  pair_id uuid not null references public.peer_pairs (id) on delete cascade,
  week_start date not null check (extract(isodow from week_start) = 1),
  decision text not null check (decision in ('INCLUDE', 'EXCLUDE')),
  reason text,
  decided_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (pair_id, week_start),
  check (decision <> 'EXCLUDE' or length(btrim(coalesce(reason, ''))) >= 5)
);

alter table public.weekly_portfolios enable row level security;
alter table public.weekly_portfolios force row level security;
alter table public.pair_manual_decisions enable row level security;
alter table public.pair_manual_decisions force row level security;
create trigger set_updated_at before update on public.weekly_portfolios
  for each row execute function public.set_updated_at();
create trigger set_updated_at before update on public.pair_manual_decisions
  for each row execute function public.set_updated_at();

create policy owner_select on public.weekly_portfolios for select to authenticated using (public.is_owner());
create policy owner_select on public.pair_manual_decisions for select to authenticated using (public.is_owner());
create policy owner_insert on public.pair_manual_decisions for insert to authenticated with check (public.is_owner());
create policy owner_update on public.pair_manual_decisions for update to authenticated
  using (public.is_owner()) with check (public.is_owner());
grant select on public.weekly_portfolios, public.pair_manual_decisions to authenticated;
grant insert, update on public.pair_manual_decisions to authenticated;
grant select on public.weekly_portfolios, public.pair_manual_decisions to mcp_readonly;
create policy mcp_readonly_select on public.weekly_portfolios for select to mcp_readonly using (true);
create policy mcp_readonly_select on public.pair_manual_decisions for select to mcp_readonly using (true);

-- Seed mapping metadata for the sample pairs (editable; samples, not recommendations).
update public.peer_pairs p set
  business_driver = v.bd, macro_driver = v.md, relationship_quality = v.q, relationship_explanation = v.ex
from (values
  ('KO / PEP','Beverages & snacks','consumer_defensive',5,'Global beverage duopoly; overlapping brands, channels and input costs.'),
  ('HD / LOW','Home improvement retail','housing_consumer_cyclical',5,'Two dominant U.S. home-improvement retailers with near-identical formats and customers.'),
  ('V / MA','Payment networks','consumer_payments_volume',5,'Global card networks with the same take-rate and volume drivers.'),
  ('XOM / CVX','Integrated oil & gas','oil_price',5,'U.S. integrated majors; results driven by crude and refining margins.'),
  ('JPM / BAC','Money-center banks','rates_credit',4,'Largest U.S. universal banks; shared rate, credit and capital-markets drivers.'),
  ('UPS / FDX','Parcel & freight delivery','freight_industrial_cycle',4,'Global parcel carriers exposed to e-commerce volume and the freight cycle.'),
  ('MRK / PFE','Large-cap pharmaceuticals','pharma_policy_pipeline',4,'Large-cap pharma exposed to pricing policy and patent cliffs; different pipelines.'),
  ('AMD / INTC','x86 CPUs & data-center silicon','semis_cycle',3,'x86 competitors; diverging execution makes the relationship looser.')
) as v(name, bd, md, q, ex)
where p.name = v.name;

-- Idempotent upsert keys for weekly builds.
create unique index pair_rankings_run_pair_uq on public.pair_rankings (run_id, pair_id);
create unique index weekly_portfolios_run_uq on public.weekly_portfolios (run_id);
