-- Leaders & laggards research data. RESEARCH ONLY: public price history, competitor mapping and the
-- weekly candidate book. No order, trade, position or execution state; nothing from WSR / Trader View.

-- One row per ticker: a year of daily bars stored as parallel arrays (compact, easy to refresh whole).
create table public.universe_price_history (
  ticker text primary key,
  as_of date not null,
  first_date date,
  n_bars integer not null default 0,
  series jsonb not null,            -- {"d": [...iso dates], "c": [...close], "a": [...adj close], "v": [...volume]}
  dividends jsonb not null default '[]'::jsonb,   -- [[iso date, amount], ...]
  splits jsonb not null default '[]'::jsonb,      -- [[iso date, "numerator:denominator"], ...]
  source text not null default 'yahoo_finance',
  fetched_at timestamptz not null default now()
);

create table public.competitor_map (
  ticker text not null,
  peer text not null,
  source text not null,             -- FINNHUB_PEERS
  fetched_at timestamptz not null default now(),
  primary key (ticker, peer)
);

-- The weekly candidate book (one row per scoring week; the latest build wins). Research output only:
-- it never records that anything was entered as a trade.
create table public.leader_laggard_books (
  scoring_week_start date primary key,
  generated_at timestamptz not null default now(),
  params jsonb not null default '{}'::jsonb,
  book jsonb not null,
  is_research_only boolean not null default true check (is_research_only)
);

alter table public.universe_price_history enable row level security;
alter table public.universe_price_history force row level security;
create policy owner_select on public.universe_price_history for select to authenticated using (public.is_owner());
alter table public.competitor_map enable row level security;
alter table public.competitor_map force row level security;
create policy owner_select on public.competitor_map for select to authenticated using (public.is_owner());
alter table public.leader_laggard_books enable row level security;
alter table public.leader_laggard_books force row level security;
create policy owner_select on public.leader_laggard_books for select to authenticated using (public.is_owner());
grant select on public.universe_price_history, public.competitor_map, public.leader_laggard_books to authenticated;
