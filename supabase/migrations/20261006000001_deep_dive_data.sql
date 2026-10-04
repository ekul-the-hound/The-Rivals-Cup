-- Deep-dive research data: short interest, earnings calendar, analyst ratings, transcripts,
-- XBRL fundamentals, SEC filing feed, options IV. RESEARCH ONLY: public data about companies,
-- no order, trade, position or execution state, and nothing from WSR / Trader View.

create table public.short_interest (
  ticker text not null,
  settlement_date date not null,
  short_interest_shares numeric,
  previous_short_interest_shares numeric,
  change_percent numeric,
  avg_daily_volume numeric,
  days_to_cover numeric,
  source text not null default 'finra_short_interest',
  fetched_at timestamptz not null default now(),
  primary key (ticker, settlement_date)
);

create table public.short_sale_volume_daily (
  ticker text not null,
  trade_date date not null,
  short_volume numeric,
  short_exempt_volume numeric,
  total_volume numeric,
  short_volume_ratio numeric,
  source text not null default 'finra_reg_sho_daily',
  fetched_at timestamptz not null default now(),
  primary key (ticker, trade_date)
);

create table public.earnings_calendar (
  ticker text not null,
  earnings_date date not null,
  time_of_day text,
  eps_estimate numeric,
  revenue_estimate numeric,
  fiscal_period_end date,
  source text not null,
  fetched_at timestamptz not null default now(),
  primary key (ticker, earnings_date)
);

create table public.analyst_recommendations (
  ticker text not null,
  period date not null,
  strong_buy integer,
  buy integer,
  hold integer,
  sell integer,
  strong_sell integer,
  source text not null default 'finnhub',
  fetched_at timestamptz not null default now(),
  primary key (ticker, period)
);

create table public.earnings_transcripts (
  ticker text not null,
  fiscal_quarter text not null,                 -- e.g. 2026Q2
  excerpt text,                                 -- bounded excerpt, not the full transcript
  segment_count integer,
  source text not null default 'alpha_vantage',
  fetched_at timestamptz not null default now(),
  primary key (ticker, fiscal_quarter)
);

create table public.company_fundamentals (
  ticker text primary key,
  cik text,
  period_end date,
  fiscal_period text,                           -- FY / Q1 / Q2 / Q3 (SEC "fp")
  revenue numeric,
  net_income numeric,
  operating_income numeric,
  total_assets numeric,
  total_liabilities numeric,
  stockholders_equity numeric,
  eps_diluted numeric,
  shares_outstanding numeric,
  prior_year_revenue numeric,
  revenue_growth_pct numeric,
  metrics jsonb not null default '{}'::jsonb,   -- every tag used, with its filing reference
  source text not null default 'sec_xbrl_companyfacts',
  fetched_at timestamptz not null default now()
);

create table public.sec_filing_feed (
  accession_number text primary key,
  cik text,
  ticker text,
  company_name text,
  form_type text,
  filed_at timestamptz,
  title text,
  link text,
  source text not null default 'sec_rss',
  fetched_at timestamptz not null default now()
);
create index on public.sec_filing_feed (ticker, filed_at desc);

create table public.options_iv_snapshots (
  ticker text not null,
  as_of date not null,
  expiry date,
  atm_implied_vol numeric,
  call_volume numeric,
  put_volume numeric,
  put_call_volume_ratio numeric,
  source text not null default 'yahoo_options',
  fetched_at timestamptz not null default now(),
  primary key (ticker, as_of)
);

alter table public.short_interest enable row level security;
alter table public.short_interest force row level security;
create policy owner_select on public.short_interest for select to authenticated using (public.is_owner());
alter table public.short_sale_volume_daily enable row level security;
alter table public.short_sale_volume_daily force row level security;
create policy owner_select on public.short_sale_volume_daily for select to authenticated using (public.is_owner());
alter table public.earnings_calendar enable row level security;
alter table public.earnings_calendar force row level security;
create policy owner_select on public.earnings_calendar for select to authenticated using (public.is_owner());
alter table public.analyst_recommendations enable row level security;
alter table public.analyst_recommendations force row level security;
create policy owner_select on public.analyst_recommendations for select to authenticated using (public.is_owner());
alter table public.earnings_transcripts enable row level security;
alter table public.earnings_transcripts force row level security;
create policy owner_select on public.earnings_transcripts for select to authenticated using (public.is_owner());
alter table public.company_fundamentals enable row level security;
alter table public.company_fundamentals force row level security;
create policy owner_select on public.company_fundamentals for select to authenticated using (public.is_owner());
alter table public.sec_filing_feed enable row level security;
alter table public.sec_filing_feed force row level security;
create policy owner_select on public.sec_filing_feed for select to authenticated using (public.is_owner());
alter table public.options_iv_snapshots enable row level security;
alter table public.options_iv_snapshots force row level security;
create policy owner_select on public.options_iv_snapshots for select to authenticated using (public.is_owner());
grant select on public.short_interest, public.short_sale_volume_daily, public.earnings_calendar, public.analyst_recommendations, public.earnings_transcripts, public.company_fundamentals, public.sec_filing_feed, public.options_iv_snapshots
  to authenticated;
