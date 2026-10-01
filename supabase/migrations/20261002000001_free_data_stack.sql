-- Free data stack: provider fields, idempotency keys, weekly event blackout (manual), eligibility.

-- Run logs: allow SKIPPED (control state gate / missing key) and record request stats.
alter table public.provider_run_logs drop constraint provider_run_logs_status_check;
alter table public.provider_run_logs add constraint provider_run_logs_status_check
  check (status in ('RUNNING', 'SUCCEEDED', 'FAILED', 'PARTIAL', 'SKIPPED'));
alter table public.provider_run_logs
  add column cache_hits integer, add column http_requests integer, add column retries integer;

-- Companies: Wikipedia (static context only) + Yahoo profile (unofficial, best effort).
alter table public.companies
  add column wiki_title text,
  add column wiki_summary text,
  add column wiki_industry text,
  add column wiki_products jsonb not null default '[]'::jsonb,
  add column wiki_competitors_hint text,
  add column wiki_fetched_at timestamptz,
  add column yahoo_sector text,
  add column yahoo_industry text,
  add column market_cap_source text,
  add column sec_ticker_title text;

-- Idempotent snapshots.
alter table public.market_context_snapshots add column snapshot_date date;
create unique index market_context_snapshots_date_key on public.market_context_snapshots (snapshot_date);
alter table public.macro_context_snapshots
  add column dgs2 numeric, add column dgs10 numeric, add column yield_curve_10y2y numeric,
  add column fed_funds numeric, add column vix numeric, add column regime text,
  add column series_dates jsonb not null default '{}'::jsonb;
create unique index macro_context_snapshots_date_key on public.macro_context_snapshots (as_of_date);

-- SEC filings: bounded excerpt + parsed Form 4 summary.
alter table public.filing_documents
  add column primary_document text,
  add column items text[] not null default '{}',
  add column excerpt text check (excerpt is null or length(excerpt) <= 8000),
  add column form4_summary jsonb;

-- Catalysts: idempotent key (accession:type).
alter table public.corporate_catalysts
  add column dedupe_key text unique,
  add column detected_at timestamptz not null default now();

-- News: syndication de-dupe via content_hash (= normalized-title hash) + provenance.
alter table public.news_items
  add column publisher_name text,
  add column publisher_domain text,
  add column matched_query text,
  add column link_confirmed boolean not null default false;

-- Data quality: idempotent auto-generated issues.
alter table public.data_quality_issues
  add column dedupe_key text unique,
  add column source text not null default 'auto';

-- ---------------------------------------------------------------------------------------------
-- Manual weekly event blackout (owner-entered; NOT fetched from any earnings/consensus API).
create table public.security_event_blackouts (
  id uuid primary key default gen_random_uuid(),
  security_id uuid not null references public.securities (id) on delete cascade,
  week_start date not null check (extract(isodow from week_start) = 1),  -- Monday of scoring week
  earnings_date_if_known date,
  known_major_event_date date,
  event_risk_notes text,
  manually_verified_at timestamptz,
  source_url text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (security_id, week_start)
);

-- Logged manual override: a pair may be un-blacked only with a written reason. Insert-only.
create table public.pair_blackout_overrides (
  id uuid primary key default gen_random_uuid(),
  pair_id uuid not null references public.peer_pairs (id) on delete cascade,
  week_start date not null check (extract(isodow from week_start) = 1),
  reason text not null check (length(btrim(reason)) >= 10),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (pair_id, week_start)
);

create table public.pair_weekly_eligibility (
  id uuid primary key default gen_random_uuid(),
  pair_id uuid not null references public.peer_pairs (id) on delete cascade,
  week_start date not null,
  eligible boolean not null,
  blocked_reasons jsonb not null default '[]'::jsonb,
  warnings jsonb not null default '[]'::jsonb,
  override_id uuid references public.pair_blackout_overrides (id) on delete set null,
  computed_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (pair_id, week_start)
);

alter table public.security_event_blackouts enable row level security;
alter table public.security_event_blackouts force row level security;
alter table public.pair_blackout_overrides enable row level security;
alter table public.pair_blackout_overrides force row level security;
alter table public.pair_weekly_eligibility enable row level security;
alter table public.pair_weekly_eligibility force row level security;

create trigger set_updated_at before update on public.security_event_blackouts
  for each row execute function public.set_updated_at();
create trigger set_updated_at before update on public.pair_blackout_overrides
  for each row execute function public.set_updated_at();
create trigger set_updated_at before update on public.pair_weekly_eligibility
  for each row execute function public.set_updated_at();

create policy owner_select on public.security_event_blackouts for select to authenticated using (public.is_owner());
create policy owner_insert on public.security_event_blackouts for insert to authenticated with check (public.is_owner());
create policy owner_update on public.security_event_blackouts for update to authenticated
  using (public.is_owner()) with check (public.is_owner());
create policy owner_select on public.pair_blackout_overrides for select to authenticated using (public.is_owner());
create policy owner_insert on public.pair_blackout_overrides for insert to authenticated with check (public.is_owner());
create policy owner_select on public.pair_weekly_eligibility for select to authenticated using (public.is_owner());

grant select on public.security_event_blackouts, public.pair_blackout_overrides,
  public.pair_weekly_eligibility to authenticated;
grant insert, update on public.security_event_blackouts to authenticated;
grant insert on public.pair_blackout_overrides to authenticated;  -- insert-only log

grant select on public.security_event_blackouts, public.pair_blackout_overrides,
  public.pair_weekly_eligibility to mcp_readonly;
create policy mcp_readonly_select on public.security_event_blackouts for select to mcp_readonly using (true);
create policy mcp_readonly_select on public.pair_blackout_overrides for select to mcp_readonly using (true);
create policy mcp_readonly_select on public.pair_weekly_eligibility for select to mcp_readonly using (true);

-- Wikipedia article titles for the seeded sample equities (editable).
insert into public.companies (security_id, legal_name, wiki_title)
select s.id, s.name, v.title
from (values
  ('KO','The Coca-Cola Company'),('PEP','PepsiCo'),('HD','Home Depot'),('LOW','Lowe''s'),
  ('V','Visa Inc.'),('MA','Mastercard'),('XOM','ExxonMobil'),('CVX','Chevron Corporation'),
  ('JPM','JPMorgan Chase'),('BAC','Bank of America'),('UPS','United Parcel Service'),('FDX','FedEx'),
  ('MRK','Merck & Co.'),('PFE','Pfizer'),('AMD','Advanced Micro Devices'),('INTC','Intel')
) as v(ticker, title)
join public.securities s on s.ticker = v.ticker
on conflict (security_id) do nothing;
