-- Manual records ONLY. Rows describe trades the owner ALREADY entered by hand in Trader View.
-- Nothing here is an order, queue, or simulation.

create table public.manual_portfolios (
  id uuid primary key default gen_random_uuid(),
  name text not null unique,
  competition text not null default '2026 Rival Cup',
  starting_cash_usd numeric not null default 0,
  is_active boolean not null default true,
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.manual_positions (
  id uuid primary key default gen_random_uuid(),
  portfolio_id uuid not null references public.manual_portfolios (id) on delete cascade,
  pair_id uuid references public.peer_pairs (id) on delete set null,
  security_id uuid not null references public.securities (id) on delete restrict,
  side public.direction not null check (side <> 'NO_TRADE'),
  quantity numeric not null check (quantity >= 0),
  avg_entry_price numeric,
  opened_at timestamptz,
  closed_at timestamptz,
  is_open boolean not null default true,
  data_status public.data_status not null default 'MANUAL' check (data_status = 'MANUAL'),
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index on public.manual_positions (portfolio_id, is_open);

create table public.manual_trades (
  id uuid primary key default gen_random_uuid(),
  portfolio_id uuid not null references public.manual_portfolios (id) on delete cascade,
  position_id uuid references public.manual_positions (id) on delete set null,
  pair_id uuid references public.peer_pairs (id) on delete set null,
  security_id uuid not null references public.securities (id) on delete restrict,
  side public.direction not null check (side <> 'NO_TRADE'),
  action text not null check (action in ('OPEN', 'ADD', 'REDUCE', 'CLOSE')),
  quantity numeric not null check (quantity > 0),
  price numeric not null check (price > 0),
  fees_usd numeric not null default 0,
  traded_at timestamptz not null,
  recorded_at timestamptz not null default now(),
  source text not null default 'MANUAL' check (source = 'MANUAL'),
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index on public.manual_trades (portfolio_id, traded_at desc);

create table public.portfolio_daily_values (
  id uuid primary key default gen_random_uuid(),
  portfolio_id uuid not null references public.manual_portfolios (id) on delete cascade,
  value_date date not null,
  cash_usd numeric,
  long_mv_usd numeric,
  short_mv_usd numeric,
  net_liq_usd numeric,
  gross_exposure_usd numeric,
  net_exposure_usd numeric,
  daily_pnl_usd numeric,
  data_status public.data_status not null default 'MANUAL',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (portfolio_id, value_date)
);

create table public.score_snapshots (
  id uuid primary key default gen_random_uuid(),
  portfolio_id uuid not null references public.manual_portfolios (id) on delete cascade,
  snapshot_at timestamptz not null default now(),
  estimated_score numeric,
  components jsonb not null default '{}'::jsonb,
  warnings jsonb not null default '[]'::jsonb,
  methodology text,
  is_estimate boolean not null default true check (is_estimate),  -- never an official WSR score
  data_status public.data_status not null default 'UNVERIFIED',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index on public.score_snapshots (portfolio_id, snapshot_at desc);
