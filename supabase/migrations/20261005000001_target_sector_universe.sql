-- U.S.-listed common-stock research universe for five target sectors.
-- RESEARCH ONLY. `competition_tradable_status` is a manual research note: it never connects to,
-- and cannot be derived from, Wall Street Rivals / Trader View. No order, trade or execution state.

create table public.security_master (
  id uuid primary key default gen_random_uuid(),
  ticker text not null unique,
  company_name text,
  exchange text,
  listing_source text not null default 'unknown',
  listing_verified_at timestamptz,
  security_type text not null default 'UNKNOWN' check (security_type in
    ('COMMON_STOCK', 'ADR', 'ETF', 'ETN', 'FUND', 'PREFERRED', 'WARRANT', 'RIGHT', 'UNIT', 'DEBT', 'UNKNOWN')),
  security_type_source text not null default 'DERIVED' check (security_type_source in ('DERIVED', 'MANUAL')),
  is_common_stock boolean not null default false,
  is_adr boolean not null default false,
  is_reit boolean not null default false,
  is_etf boolean not null default false,
  is_leveraged_product boolean not null default false,
  is_otc boolean not null default false,
  is_preferred boolean not null default false,
  is_warrant boolean not null default false,
  is_right boolean not null default false,
  is_unit boolean not null default false,
  is_spac boolean not null default false,
  is_test_issue boolean not null default false,
  is_active boolean not null default true,
  listing_financial_status text,                       -- Nasdaq financial-status code, when published
  -- sector classification (five targets, or OTHER for a known non-target sector)
  sector text check (sector is null or sector in
    ('HEALTH_CARE', 'INDUSTRIALS', 'FINANCIALS', 'UTILITIES', 'REAL_ESTATE', 'OTHER')),
  sector_raw text,                                     -- provider label before normalization
  industry text,
  sector_source text,                                  -- YAHOO_FINANCE | MANUAL | SECTOR_MAPPING_CSV
  sector_classified_at timestamptz,
  sector_data_status public.data_status not null default 'MISSING',
  profile_checked_at timestamptz,                      -- last classification attempt (rotates batches)
  -- SEC identity
  cik text,
  sec_company_name text,
  -- market data
  market_cap numeric,
  average_dollar_volume_20d numeric,
  average_daily_volume_20d numeric,
  last_price numeric,
  liquidity_data_as_of date,
  market_data_source text,
  -- manual event fields (the weekly blackout still comes from security_event_blackouts too)
  earnings_date_if_known date,
  known_major_event_date date,
  event_risk_notes text,
  -- manual research metadata
  competition_tradable_status text not null default 'UNKNOWN' check (competition_tradable_status in
    ('UNKNOWN', 'MANUALLY_VERIFIED', 'MANUALLY_REJECTED')),
  competition_verification_note text,
  competition_verified_at timestamptz,
  manually_overridden boolean not null default false,
  override_reason text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index on public.security_master (sector);
create index on public.security_master (is_active);
create index on public.security_master (competition_tradable_status);

-- Output of build_target_sector_universe_views: which view each symbol belongs to and why not.
create table public.security_master_view_state (
  id uuid primary key default gen_random_uuid(),
  ticker text not null unique,
  in_all_target_sector_listings boolean not null default false,
  in_pair_research_eligible boolean not null default false,
  in_manual_review boolean not null default false,
  exclusion_reasons text[] not null default '{}',
  review_reasons text[] not null default '{}',
  flags text[] not null default '{}',
  built_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Insert-only audit trail of every manual verification / override (and what it replaced).
create table public.security_master_audit (
  id uuid primary key default gen_random_uuid(),
  ticker text not null,
  action text not null check (action in ('MANUAL_VERIFY', 'MANUAL_OVERRIDE')),
  field_changes jsonb not null default '{}'::jsonb,    -- {field: {"old": ..., "new": ...}}
  reason text,
  actor text not null default 'owner',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index on public.security_master_audit (ticker, created_at desc);

create trigger set_updated_at before update on public.security_master
  for each row execute function public.set_updated_at();
create trigger set_updated_at before update on public.security_master_view_state
  for each row execute function public.set_updated_at();
create trigger set_updated_at before update on public.security_master_audit
  for each row execute function public.set_updated_at();

alter table public.security_master enable row level security;
alter table public.security_master force row level security;
alter table public.security_master_view_state enable row level security;
alter table public.security_master_view_state force row level security;
alter table public.security_master_audit enable row level security;
alter table public.security_master_audit force row level security;

-- Owner can read; every write goes through the service-role refresh jobs / admin API only.
create policy owner_select on public.security_master for select to authenticated using (public.is_owner());
create policy owner_select on public.security_master_view_state for select to authenticated using (public.is_owner());
create policy owner_select on public.security_master_audit for select to authenticated using (public.is_owner());
grant select on public.security_master, public.security_master_view_state, public.security_master_audit
  to authenticated;

-- The three research universes. security_invoker keeps row level security in force.
create view public.all_target_sector_listings with (security_invoker = true) as
  select m.*, s.exclusion_reasons, s.review_reasons, s.flags, s.built_at as view_built_at
  from public.security_master m
  join public.security_master_view_state s on s.ticker = m.ticker
  where s.in_all_target_sector_listings;

create view public.pair_research_eligible_universe with (security_invoker = true) as
  select m.*, s.exclusion_reasons, s.review_reasons, s.flags, s.built_at as view_built_at
  from public.security_master m
  join public.security_master_view_state s on s.ticker = m.ticker
  where s.in_pair_research_eligible;

create view public.manual_review_universe with (security_invoker = true) as
  select m.*, s.exclusion_reasons, s.review_reasons, s.flags, s.built_at as view_built_at
  from public.security_master m
  join public.security_master_view_state s on s.ticker = m.ticker
  where s.in_manual_review;

grant select on public.all_target_sector_listings, public.pair_research_eligible_universe,
  public.manual_review_universe to authenticated;
